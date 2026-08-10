"""Thin client over the OpenAI SDK, with a log of every call.

Every call (including retries) is recorded as an ``LlmCall`` row: the
request messages, the raw response, and an error if the call or its parsing
failed. The transport is a plain callable; the module-level ``client``
(thieves) and ``implementor_client`` (the implementor/reviewer model)
objects are what callers should use (``from main import llm; llm.client``),
so tests can swap them for a fake without touching the network:

    llm.client = LlmClient(transport=fake_transport)

The real transports read their configuration from ``os.environ`` at call
time, never at import time, so importing this module or constructing
``LlmClient()`` requires no configuration. The thief transport reads
``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``; the implementor
transport reads ``LLM_IMPLEMENTOR_API_KEY`` / ``LLM_IMPLEMENTOR_BASE_URL`` /
``LLM_IMPLEMENTOR_MODEL``.
"""

import json
import os

from openai import OpenAI

from main.models import LlmCall


class LlmError(Exception):
    """The LLM returned unparseable JSON twice in a row."""


# A hung call must fail fast and fall back to the beat's safe default rather
# than block the whole beat (and the transaction) for the SDK's default of
# 10 minutes per attempt, times two retries. The model also reasons by
# default, which can starve the actual answer: it would spend the whole
# output budget thinking and reply with an empty string (observed against
# the real API), and a capped runaway is a failure anyway. Thinking is
# disabled explicitly; ``max_tokens`` remains as a backstop ceiling. The
# implementor writes whole Lua files, so its ceiling is higher; the
# Anthropic endpoint rejects the DeepSeek thinking knob as an unknown field,
# so the implementor transport sends no ``extra_body`` at all.
_TIMEOUT_SECONDS = 120.0
_MAX_OUTPUT_TOKENS = 1500
_MAX_IMPLEMENTOR_OUTPUT_TOKENS = 8000


def _request_kwargs(messages, *, model, max_tokens, extra_body=None):
    """Build the ``chat.completions.create`` kwargs for one model.

    Kept separate from the transports so tests can inspect exactly what
    would be sent to the API without any network access.
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if extra_body is not None:
        kwargs["extra_body"] = extra_body
    return kwargs


def _call_model(
    messages,
    *,
    api_key_env,
    base_url_env,
    base_url_default,
    model_env,
    model_default,
    max_tokens,
    extra_body=None,
):
    """Send ``messages`` to the model and return the reply text.

    The API key, base URL and model are read from ``os.environ`` at call
    time; ``base_url_default`` / ``model_default`` apply when their
    variables are unset.
    """
    client = OpenAI(
        api_key=os.environ.get(api_key_env),
        base_url=os.environ.get(base_url_env, base_url_default),
        timeout=_TIMEOUT_SECONDS,
        max_retries=0,
    )
    response = client.chat.completions.create(
        **_request_kwargs(
            messages,
            model=os.environ.get(model_env, model_default),
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
    )
    return response.choices[0].message.content or ""


def _openai_transport(messages):
    """Send ``messages`` to the thief model and return the reply text.

    Configured by ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``. This
    transport talks to DeepSeek and disables its thinking explicitly.
    """
    return _call_model(
        messages,
        api_key_env="LLM_API_KEY",
        base_url_env="LLM_BASE_URL",
        base_url_default="https://api.deepseek.com",
        model_env="LLM_MODEL",
        model_default="deepseek-v4-flash",
        max_tokens=_MAX_OUTPUT_TOKENS,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _implementor_transport(messages):
    """Send ``messages`` to the implementor model and return the reply text.

    Configured by ``LLM_IMPLEMENTOR_API_KEY`` / ``LLM_IMPLEMENTOR_BASE_URL``
    / ``LLM_IMPLEMENTOR_MODEL``, pointing at Anthropic's OpenAI-compatible
    endpoint. Unlike the thief transport it sends no ``extra_body``: the
    endpoint rejects unknown fields such as DeepSeek's thinking knob.
    """
    return _call_model(
        messages,
        api_key_env="LLM_IMPLEMENTOR_API_KEY",
        base_url_env="LLM_IMPLEMENTOR_BASE_URL",
        base_url_default="https://api.anthropic.com/v1/",
        model_env="LLM_IMPLEMENTOR_MODEL",
        model_default="claude-fable-5",
        max_tokens=_MAX_IMPLEMENTOR_OUTPUT_TOKENS,
    )


def _extract_json(text):
    """Parse ``text`` as JSON, tolerating a markdown code fence."""
    text = text.strip()
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return json.loads("\n".join(lines).strip())


class LlmClient:
    def __init__(self, transport=_openai_transport):
        self.transport = transport

    def _call(self, messages, *, game, thief, day, phase, purpose):
        """Run one transport call and log it; returns (response, log row)."""
        error = ""
        response = ""
        try:
            response = self.transport(messages)
        except Exception as err:
            error = str(err)
            raise
        finally:
            row = LlmCall.objects.create(
                game=game,
                thief=thief,
                day=day,
                phase=phase,
                purpose=purpose,
                messages=list(messages),
                response=response,
                error=error,
            )
        return response, row

    def chat(
        self, messages, *, game=None, thief=None, day=None, phase=None, purpose=""
    ):
        """Send ``messages`` to the model and return the reply text."""
        return self._call(
            messages, game=game, thief=thief, day=day, phase=phase, purpose=purpose
        )[0]

    def ask_json(
        self,
        system,
        user,
        schema_hint,
        *,
        game=None,
        thief=None,
        day=None,
        phase=None,
        purpose="",
    ):
        """Ask for a JSON decision, retrying once if the reply does not parse.

        Returns the parsed JSON on success. On a second failure raises
        ``LlmError`` so callers can fall back to a safe default. Each attempt
        (including the retry) is logged as its own ``LlmCall`` row.
        """
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"{user}\n\nRespond with JSON only, "
                f"matching this schema: {schema_hint}",
            },
        ]
        for attempt in (1, 2):
            response, row = self._call(
                messages, game=game, thief=thief, day=day, phase=phase, purpose=purpose
            )
            try:
                return _extract_json(response)
            except ValueError as err:
                row.error = f"invalid JSON ({err}): {response!r}"
                row.save(update_fields=["error"])
                if attempt == 2:
                    raise LlmError(
                        f"LLM returned unparseable JSON twice for {purpose!r}: {err}"
                    ) from err
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous response was not valid JSON "
                        f"({err}). Reply with JSON only.",
                    }
                )
        raise AssertionError("unreachable")


client = LlmClient()
implementor_client = LlmClient(transport=_implementor_transport)
