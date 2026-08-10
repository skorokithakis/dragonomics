"""Sandboxed Lua rule hooks and the capability bridge.

Rule code is written by a strong LLM that may be prompted by scheming
agents, so every byte of it is treated as hostile input.  This module runs
Lua rule hooks (``on_day_start``, ``on_night_theft``,
``on_public_message``, ``on_moot_end``, ``validate_action``) inside a
hardened sandbox with two layers.

Layer 1 - in-process sandbox (``run_hook``):

* ``LuaRuntime(register_eval=False, register_builtins=False)`` keeps lupa's
  ``python`` module (``python.eval`` / ``python.builtins``) out of the
  runtime, and an ``attribute_filter`` denies *every* attribute access on
  Python objects from Lua.  Without the filter, ``adjust_score.__globals__
  .__builtins__.__import__("os")`` reaches the Python interpreter (a
  verified escape); with it, any attribute get/set raises AttributeError.
* The rule source is compiled with ``load(chunk, name, "t", env)`` against a
  whitelist environment (see ``_WHITELIST``).  ``os``, ``io``, ``require``,
  ``load``, ``dofile``, ``loadstring``, ``collectgarbage``, ``debug``,
  ``coroutine``, ``_G``, ``setmetatable`` and friends are simply not there:
  any reference to them is a nil value, and there is no way to reach them
  (no ``_G``, no ``getfenv``/``setfenv``, no ``debug``, no ``load``).
  ``table`` is not whitelisted wholesale either: only the audited subset
  ``insert, remove, concat, sort, unpack`` is exposed (``table.move`` can
  loop ~1e12 times inside C, bypassing the instruction budget).
* ``pcall`` is deliberately *not* whitelisted.  A count-mode
  ``debug.sethook`` budget error is just a Lua error: if the guest could
  call ``pcall`` it could swallow the budget error inside
  ``pcall(function() while true do end end)`` and spin forever without the
  host ever regaining control (verified empirically).  With no
  protected-call primitive in the environment, every error -- including the
  budget error -- unwinds to the host and becomes ``HookResult.error``.
* The host installs ``debug.sethook`` (count mode, ``INSTRUCTION_BUDGET``)
  *before* invoking any guest code.  The guest cannot see or remove the
  hook because ``debug`` is not in its environment.  An infinite Lua loop
  therefore surfaces as an error within the budget instead of hanging the
  process.
* ``max_memory`` caps the runtime's memory so that a single hostile C call
  (e.g. ``string.rep("x", 1e12)``) fails as a contained ``LuaMemoryError``
  instead of OOM-killing the host.
* ``state`` and ``args`` are converted to *native* Lua tables before the
  call (dicts become string-keyed tables, lists become 1-based tables,
  depth-limited to ``_MAX_DEPTH``), so missing keys read as ``nil``, list
  indexing is 1-based, and ``pairs()``/``ipairs()`` iterate as any Lua
  author expects.  Writes to ``day``/``hoard``/``scores`` in Lua never
  reach the engine (they are not read back); ``scratchpad`` is the one
  mutable surface and comes back on the result.  ``None`` values are
  dropped during conversion: Lua tables cannot hold nil, setting a key to
  nil deletes it, and a list containing ``None`` becomes a sparse table
  (which is then rejected on read-back).

Layer 2 - process isolation (``run_hook_isolated``):

  The instruction budget cannot interrupt C calls: ``table.move`` and
  pathological ``string.match``/``string.find`` patterns run inside C and
  never hit ``debug.sethook``.  To leash those, ``run_hook_isolated()``
  forks a child process (fork start method), sets ``RLIMIT_CPU`` (2s) and
  ``RLIMIT_AS`` (the parent's current address space -- which the fork
  inherits -- plus a 256 MiB headroom, so the rule gets at most 256 MiB of
  additional memory), runs ``run_hook`` there, and returns the result
  through a pipe.  The parent waits ~5s wall-clock and terminates the child
  on overrun, returning ``HookResult(error='rule exceeded the sandbox time
  budget')``.  Everything in ``HookResult`` is JSON-able and crosses the
  pipe as JSON.

  ENGINE CODE MUST USE ``run_hook_isolated``.  ``run_hook`` is the
  in-process core (kept for speed and tests); only ``run_hook_isolated``
  contains the C-function leash.

Capability bridge: ``adjust_score(name, amount, reason)`` and
``announce(text)`` are injected into the guest environment but do nothing
except record the call on the result; the engine applies them later, in
order.  Caps: at most ``_MAX_CAPABILITY_CALLS`` calls per hook run
(overrun is a hook error) and string arguments are truncated at
``_MAX_STRING_ARG`` characters.

Conversion rules for the returned scratchpad: empty Lua tables convert to
``{}`` (an object, never an empty list -- rule authors should delete keys
rather than store empty arrays), ``{1, nil}`` converts as ``[1]``,
consecutive integer keys 1..n become lists, string keys become dicts.
Anything not JSON-able (functions, userdata, mixed or non-string table
keys, non-finite numbers, cycles, structures deeper than ``_MAX_DEPTH``)
turns the run into a hook error and the engine keeps the old scratchpad.
Lua strings are UTF-8: a non-UTF-8 string (e.g. ``string.char(255)``) is a
hook error.

``run_hook`` NEVER raises: every failure path -- Lua errors, budget
overruns, bad output, malformed input (``KeyError``/``TypeError``), deep
nesting (``RecursionError``), non-UTF-8 output (``UnicodeDecodeError``) --
is contained in ``HookResult.error``.

One fresh ``LuaRuntime`` per ``run_hook`` call: slow but simple, no
cross-rule contamination, and rule volume is tiny (a handful of hook calls
per beat).  Optimize only if a real day is measurably slow.
"""

from __future__ import annotations

import json
import math
import multiprocessing
import resource
from dataclasses import dataclass, field
from typing import Any

import lupa

#: Instruction-count budget enforced via debug.sethook (count mode).
INSTRUCTION_BUDGET = 1_000_000
#: Memory cap for the Lua runtime, in bytes: makes memory bombs contained
#: LuaMemoryErrors instead of host OOMs.
MEMORY_BUDGET = 16 * 1024 * 1024
#: Depth limit for both Python->Lua and Lua->Python conversion.
_MAX_DEPTH = 100
#: Max capability calls recorded per hook run; overrun is a hook error.
_MAX_CAPABILITY_CALLS = 100
#: String arguments to capability calls are truncated at this length.
_MAX_STRING_ARG = 2000
#: RLIMIT_CPU (seconds) and wall-clock wait (seconds) for the isolated child.
_ISOLATED_CPU_BUDGET = 2
_ISOLATED_WALL_BUDGET = 5.0
#: Extra address space the isolated child may use beyond what fork inherits.
_ISOLATED_MEMORY_HEADROOM = 256 * 1024 * 1024
_TIME_BUDGET_ERROR = "rule exceeded the sandbox time budget"

#: Every global the rule code may see.  Nothing here can touch the
#: filesystem, the network, the Python interpreter, or unbounded CPU, and
#: none of it can create a protected call boundary (no pcall/xpcall), so
#: the instruction-budget error always unwinds to the host.
_WHITELIST = (
    "pairs",
    "ipairs",
    "next",
    "select",
    "type",
    "tostring",
    "tonumber",
    "error",
    "string",
    "math",
)
#: Audited subset of the table library.  table.move is excluded: it can
#: loop ~1e12 times inside C, bypassing the instruction budget.
_AUDITED_TABLE = ("insert", "remove", "concat", "sort", "unpack")


@dataclass(frozen=True)
class CapabilityCall:
    """A capability the rule invoked, to be applied by the engine later.

    ``kind`` is ``"adjust_score"`` or ``"announce"``; ``args`` holds the
    JSON-able arguments the guest passed, e.g. ``("Alice", 3, "reason")``
    or ``("text",)``.  Calls are recorded in invocation order.
    """

    kind: str
    args: tuple


@dataclass
class HookResult:
    """Outcome of one sandboxed hook run.  Never raises.

    ``value`` is the hook's return value (for ``validate_action``), or
    ``None``.  ``scratchpad`` is the mutated scratchpad on success, and the
    engine's original scratchpad object when the hook was missing or
    errored (the engine keeps the old scratchpad either way).  ``error`` is
    set on a Lua error, a budget overrun, or bad output; a missing hook is
    a clean no-op with no error.
    """

    value: Any = None
    scratchpad: dict | None = None
    calls: list[CapabilityCall] = field(default_factory=list)
    error: str | None = None


class _BadOutput(Exception):
    """The guest produced something that cannot cross back to the engine."""


def _deny_all_attributes(obj: Any, attr_name: Any, is_setting: bool) -> Any:
    """Lupa attribute filter: deny every attribute access from Lua.

    Without this, ``adjust_score.__globals__.__builtins__.__import__("os")``
    reaches the Python interpreter from inside the sandbox (a verified
    escape).  Raising AttributeError turns the access into a contained Lua
    error.
    """
    raise AttributeError("attribute access on Python objects is denied")


def _table_from_items(pairs: list[tuple[Any, Any]], seen: set[int], depth: int) -> Any:
    """Classify a table's (key, value) pairs into a JSON-able Python shape.

    Consecutive integer keys 1..n become a list, string keys become a
    dict; anything else (mixed, sparse, non-string keys) is bad output.
    """
    if not pairs:
        return {}
    keys = [key for key, _ in pairs]
    if all(isinstance(key, int) and not isinstance(key, bool) for key in keys):
        if sorted(keys) == list(range(1, len(keys) + 1)):
            ordered = sorted(pairs, key=lambda pair: pair[0])
            return [_convert(value, seen, depth + 1) for _, value in ordered]
        raise _BadOutput(
            "table has integer keys that are not a sequence 1..n "
            "(mixed or sparse integer keys are not JSON-able)"
        )
    if all(isinstance(key, str) for key in keys):
        return {key: _convert(value, seen, depth + 1) for key, value in pairs}
    raise _BadOutput(
        "table keys are not JSON-able (only string keys, or integer "
        "keys 1..n, are allowed)"
    )


def _convert(value: Any, seen: set[int], depth: int) -> Any:
    if depth > _MAX_DEPTH:
        raise _BadOutput("structure is too deeply nested")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, bytes):
        raise _BadOutput("non-UTF-8 text produced by the rule")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _BadOutput(f"non-finite number {value} is not JSON-able")
        return value
    if isinstance(value, dict):
        obj_id = id(value)
        if obj_id in seen:
            raise _BadOutput("cyclic or repeated structure is not JSON-able")
        seen.add(obj_id)
        try:
            return _table_from_items(list(value.items()), seen, depth)
        finally:
            seen.discard(obj_id)
    if isinstance(value, list):
        obj_id = id(value)
        if obj_id in seen:
            raise _BadOutput("cyclic or repeated structure is not JSON-able")
        seen.add(obj_id)
        try:
            return [_convert(item, seen, depth + 1) for item in value]
        finally:
            seen.discard(obj_id)
    # Anything else that looks like a table is a lupa Lua table; everything
    # else (functions, userdata, ...) is bad output.
    items = getattr(value, "items", None)
    if items is None:
        raise _BadOutput(
            f"{type(value).__name__} is not JSON-able (only numbers, "
            "strings, booleans, nil and tables may cross)"
        )
    obj_id = id(value)
    if obj_id in seen:
        raise _BadOutput("cyclic or repeated structure is not JSON-able")
    seen.add(obj_id)
    try:
        return _table_from_items(list(items()), seen, depth)
    finally:
        seen.discard(obj_id)


def _to_python(value: Any) -> Any:
    """Convert a Lua value, recursively, to a JSON-able Python value.

    Raises ``_BadOutput`` for anything JSON cannot represent: functions,
    userdata, tables with mixed/non-string/non-1..n-integer keys, non-finite
    numbers, cyclic structures, and nesting deeper than ``_MAX_DEPTH``.
    """
    return _convert(value, set(), 0)


def _to_lua(runtime: lupa.LuaRuntime, value: Any, depth: int = 0) -> Any:
    """Convert a JSON-able Python value to a native Lua value.

    Dicts become string-keyed Lua tables, lists become 1-based Lua tables,
    scalars pass through.  ``None`` is passed as nil (assigning it deletes
    the key, which is Lua semantics).  Raises ``_BadOutput`` for
    non-JSON-able values and nesting deeper than ``_MAX_DEPTH``.
    """
    if depth > _MAX_DEPTH:
        raise _BadOutput("structure is too deeply nested")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        table = runtime.table()
        for index, item in enumerate(value, start=1):
            table[index] = _to_lua(runtime, item, depth + 1)
        return table
    if isinstance(value, dict):
        table = runtime.table()
        for key, item in value.items():
            if not isinstance(key, str):
                raise _BadOutput(
                    f"dict key {key!r} is not a string (state must be JSON-able)"
                )
            table[key] = _to_lua(runtime, item, depth + 1)
        return table
    raise _BadOutput(
        f"{type(value).__name__} is not convertible to Lua (state must be JSON-able)"
    )


def _cap_strings(value: Any) -> Any:
    """Truncate string arguments of capability calls at _MAX_STRING_ARG."""
    if isinstance(value, str):
        return value[:_MAX_STRING_ARG]
    if isinstance(value, dict):
        return {key: _cap_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_strings(item) for item in value]
    return value


def _make_bridge(kind: str, calls: list[CapabilityCall]) -> Any:
    def bridge(*args: Any) -> None:
        if len(calls) >= _MAX_CAPABILITY_CALLS:
            raise _BadOutput(
                f"too many capability calls in one hook run "
                f"(max {_MAX_CAPABILITY_CALLS})"
            )
        calls.append(
            CapabilityCall(kind, tuple(_to_python(_cap_strings(arg)) for arg in args))
        )

    return bridge


def _audited_table(runtime: lupa.LuaRuntime, host: Any) -> Any:
    """Build a table library containing only the audited subset."""
    source = host["table"]
    table_lib = runtime.table()
    for name in _AUDITED_TABLE:
        table_lib[name] = source[name]
    return table_lib


def _install_budget_hook(runtime: lupa.LuaRuntime) -> None:
    """Install the instruction-count hook on the runtime's main thread.

    The hook runs with the host's environment (the guest never sees
    ``debug``), so the guest cannot remove or observe it.  Errors raised by
    the hook cannot be caught by the guest: ``pcall`` is not whitelisted.
    """
    debug = runtime.globals()["debug"]
    hook = runtime.eval("function() error('instruction budget exceeded') end")
    debug.sethook(hook, "", INSTRUCTION_BUDGET)


def run_hook(code: str, hook: str, args: list[Any], state: dict[str, Any]) -> HookResult:
    """Run one Lua rule hook in the sandbox and return its result.  Never raises.

    ``code`` is the full rule source defining zero or more hook functions;
    ``hook`` is the name of the function to invoke; ``args`` are the
    positional values the hook receives; ``state`` is a dict with ``day``,
    ``hoard``, ``scores`` (name -> gold) and ``scratchpad`` (a JSON-able
    dict).  The hook is called as ``hook(*args, state)`` where ``state`` is
    a *native* Lua table built from ``state``: missing keys read as nil,
    lists are 1-based, and writes to ``day``/``hoard``/``scores`` never
    reach the engine -- only ``scratchpad`` is read back.  A missing hook
    is a silent no-op; any Lua error, budget overrun, or non-JSON-able
    output is contained in ``HookResult.error``.

    This is the in-process core; engine code must use ``run_hook_isolated``
    instead, which adds the C-call leash (CPU/memory rlimits in a child
    process).
    """
    scratchpad = None
    calls: list[CapabilityCall] = []
    try:
        if not isinstance(state, dict):
            raise _BadOutput(f"state must be a dict, got {type(state).__name__}")
        scratchpad = state["scratchpad"]
        runtime = lupa.LuaRuntime(
            register_eval=False,
            register_builtins=False,
            max_memory=MEMORY_BUDGET,
            attribute_filter=_deny_all_attributes,
        )
        host = runtime.globals()
        env = runtime.table()
        for name in _WHITELIST:
            env[name] = host[name]
        env["table"] = _audited_table(runtime, host)
        env["adjust_score"] = _make_bridge("adjust_score", calls)
        env["announce"] = _make_bridge("announce", calls)
        # Compile against the restricted environment (text-only mode).  The
        # chunk's top-level code runs with the budget hook already active.
        # On failure Lua's load returns nil plus a message, which lupa
        # surfaces as a tuple.
        loaded = host["load"](code, "rules.lua", "t", env)
        if isinstance(loaded, tuple) or loaded is None:
            message = loaded[1] if isinstance(loaded, tuple) and len(loaded) > 1 else (
                "could not compile rule code"
            )
            return HookResult(error=str(message), scratchpad=scratchpad)
        chunk = loaded
        _install_budget_hook(runtime)
        chunk()
        hook_fn = env[hook]
        if hook_fn is None:
            return HookResult(scratchpad=scratchpad)
        if not callable(hook_fn):
            return HookResult(
                error=f"hook {hook!r} is not a function", scratchpad=scratchpad
            )
        state_lua = _to_lua(runtime, state)
        args_lua = [_to_lua(runtime, arg) for arg in args]
        value = _to_python(hook_fn(*args_lua, state_lua))
        new_scratchpad = _to_python(state_lua["scratchpad"])
        if not isinstance(new_scratchpad, dict):
            return HookResult(
                error="state.scratchpad is not a JSON-able table",
                scratchpad=scratchpad,
            )
        return HookResult(value=value, scratchpad=new_scratchpad, calls=calls)
    except lupa.LuaMemoryError:
        # lupa's memory errors carry no message; make the error field
        # meaningful for the engine.
        return HookResult(
            error="rule exceeded the sandbox memory budget", scratchpad=scratchpad
        )
    except UnicodeDecodeError:
        # Lua strings are bytes; lupa decodes them as UTF-8.  A rule that
        # emits non-UTF-8 text (e.g. string.char(255)) is a hook error.
        return HookResult(error="rule produced non-UTF-8 text", scratchpad=scratchpad)
    except (lupa.LuaError, _BadOutput) as exc:
        return HookResult(error=str(exc) or "Lua error in rule code", scratchpad=scratchpad)
    except Exception as exc:
        # The contract is "never raises": malformed input (KeyError,
        # TypeError, ...) is an error result like any other.
        return HookResult(error=f"{type(exc).__name__}: {exc}", scratchpad=scratchpad)


def _isolated_memory_limit() -> int:
    """RLIMIT_AS for the sandbox child.

    A fork inherits the parent's address space, so a flat cap could kill
    the child at startup; instead cap the total at what we already occupy
    plus a fixed headroom -- the rule gets at most
    ``_ISOLATED_MEMORY_HEADROOM`` of additional memory.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024 + _ISOLATED_MEMORY_HEADROOM
    except OSError:
        pass
    return _ISOLATED_MEMORY_HEADROOM


def _hook_result_to_json(result: HookResult) -> str:
    return json.dumps(
        {
            "value": result.value,
            "scratchpad": result.scratchpad,
            "calls": [
                {"kind": call.kind, "args": list(call.args)} for call in result.calls
            ],
            "error": result.error,
        }
    )


def _hook_result_from_json(payload: str) -> HookResult:
    data = json.loads(payload)
    return HookResult(
        value=data.get("value"),
        scratchpad=data.get("scratchpad"),
        calls=[
            CapabilityCall(call["kind"], tuple(call["args"]))
            for call in data.get("calls", [])
        ],
        error=data.get("error"),
    )


def _isolated_child(
    conn: Any, code: str, hook: str, args: list[Any], state: dict[str, Any]
) -> None:
    """Child process body: apply rlimits, run the hook, report the result.

    If the rule burns its CPU budget or memory headroom the kernel kills
    the child (SIGKILL); the parent then sees a closed pipe and reports the
    time-budget error.  Anything else that goes wrong still sends a payload
    so the parent never waits for a dead child.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (_ISOLATED_CPU_BUDGET,) * 2)
        resource.setrlimit(resource.RLIMIT_AS, (_isolated_memory_limit(),) * 2)
        result = run_hook(code, hook, args, state)
        conn.send(_hook_result_to_json(result))
    except BaseException:
        try:
            conn.send(json.dumps({"error": "rule crashed the sandbox child"}))
        except Exception:
            pass
    finally:
        conn.close()


def run_hook_isolated(
    code: str, hook: str, args: list[Any], state: dict[str, Any]
) -> HookResult:
    """``run_hook`` behind process isolation: the C-call leash.

    Same signature and return contract as ``run_hook``, but the hook runs
    in a forked child with ``RLIMIT_CPU`` (2s) and ``RLIMIT_AS`` (256 MiB
    headroom above the inherited address space).  C functions such as
    ``table.move`` and pathological string patterns cannot be interrupted
    by ``debug.sethook``, so they are killed here instead; the parent waits
    ~5s wall-clock, terminates the child on overrun, and returns
    ``HookResult(error='rule exceeded the sandbox time budget')``.

    ENGINE CODE MUST USE THIS FUNCTION.
    """
    ctx = multiprocessing.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_isolated_child,
        args=(child_conn, code, hook, args, state),
        daemon=True,
    )
    process.start()
    child_conn.close()
    try:
        if not parent_conn.poll(_ISOLATED_WALL_BUDGET):
            process.terminate()
            process.join()
            return HookResult(error=_TIME_BUDGET_ERROR)
        try:
            payload = parent_conn.recv()
        except EOFError:
            # Child died without reporting (e.g. killed by its rlimits).
            return HookResult(error=_TIME_BUDGET_ERROR)
        process.join()
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
    try:
        return _hook_result_from_json(payload)
    except (ValueError, TypeError, KeyError):
        return HookResult(error="sandbox child returned an unreadable result")
