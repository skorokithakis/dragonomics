# The dragon's hoard: next steps

Date: 2026-08-09. Handoff note for the next working session.

## Where things stand

The game design is complete. `docs/dragons-hoard-spec.md` (draft 2) is the single canonical reference: every phase, channel, capability, and the win condition are pinned. `docs/dragons-hoard-decisions.md` records all 14 decision sets with reasoning and simulation evidence; consult it when a spec choice needs its "why". `docs/dragons-hoard-draft-1.md` is the original draft, kept for history. Two simulation scripts are saved alongside: `sim-threshold.py` (the original fixed-threshold sweep) and `sim-hazard.py` (the Monte Carlo for the hazard ramp; its results are in decisions section 7).

What remains is content and engineering, not game design. In order:

## 1. Write the ten persona sheets

Each sheet: a name, a one-line character, and one private goal with a machine-checkable condition and a gold payout (10 to 20 gold, per decisions section 14). Design intent: goals should pull different thieves in different directions at different times (early-game debts, mid-game accumulation targets, standing behavioural goals like the dragon-zealot), so conflict is seeded before any law exists. Keep goals checkable by the engine: conditions over scores, takes, days, and public events only. Existing seeds from draft 1: the coward, the zealot who worships the dragon, the debtor who needs 40 gold by day 20.

Also write the two or three **example proposals** that go in every agent's system prompt as teaching material for the blank statute book (spec: "The statute book").

## 2. Pin the engine spec

A short technical document that fixes, exactly:

* Hook signatures and when each fires: `on_day_start`, `on_public_message`, `on_night_theft`, `on_moot_end`, `validate_action` (remember: ballots never pass through `validate_action`; franchise is physics).
* The state object rules see, and the capability functions: `adjust_score(agent, amount, reason)`, `announce(text)`, parley interception, parley metadata reveal, and the scratchpad with its single reserved `inactive` key.
* The daily database row: proposal text, code diff, full rule set, scratchpad snapshot, model and prompt versions. Replay must work from rows alone.
* Sandbox specifics: lupa embedding, import whitelist, timeout values, and N (smoke-test retry count).
* The dawn report format (what agents see each morning) and the audience-plane log format (the audience sees everything; nothing flows back to agents).

## 3. Build step 1: engine and day loop

Python. Hardcoded rules, no agents, plain commons skin. Port `sim-hazard.py` into the engine's test suite as the first invariant check (same numbers must reproduce). Prove deterministic replay from database rows before adding anything else.

## 4. Build step 2: agents

Scheduling prompts, Moot rounds, parleys, ballots, night takes, daily diary refresh. Run against scripted fake proposals; no implementor yet. A human can play lawgiver by hand-editing rules between days.

## 5. Build step 3: the implementor

Strong model writes Lua hook edits from the winning proposal; reviewer model checks the diff against proposal and invariants; smoke test (simulate one synthetic day on yesterday's real state); on repeated failure, "beyond the guild's magic" and the old rules stand.

## 6. Build step 4: personas and narrator

Plug in the persona sheets, goal payouts, the narrator's daily chronicle, and the audience-plane outputs: the god's-eye log and the legislative diff log (prose beside enacted code), which together are the final artifact.

## Playtest watch-list

Carried from the spec's "Deferred to playtesting": all tuning numbers (hoard 250 / cap 300 / regrowth 12% / hazard 120 to 60 / takes 0 to 5 / goal payouts); the messaging caps (they set token cost almost directly); day-1 behaviour with the blank statute book; legislation volume under seconding plus the floor cap; whether politics feels tame without vote-gating; robustness of speech-act parsing.
