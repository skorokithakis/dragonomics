"""Thin client over the OpenAI SDK, with a log of every call.

Every call (including retries) is recorded as an ``LlmCall`` row: the
request messages, the raw response, and an error if the call or its parsing
failed. The transport is a plain callable; the module-level ``client``
object is what callers should use (``from main import llm; llm.client``),
so tests can swap it for a fake without touching the network:

    llm.client = LlmClient(transport=fake_transport)

The real transport reads ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``
from ``os.environ`` at call time, never at import time, so importing this
module or constructing ``LlmClient()`` requires no configuration.
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
# disabled explicitly; ``max_tokens`` remains as a backstop ceiling.
_TIMEOUT_SECONDS = 120.0
_MAX_OUTPUT_TOKENS = 1500


def _openai_transport(messages):
    """Send ``messages`` to the configured model and return the reply text."""
    client = OpenAI(
        api_key=os.environ.get("LLM_API_KEY"),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
        timeout=_TIMEOUT_SECONDS,
        max_retries=0,
    )
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=_MAX_OUTPUT_TOKENS,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return response.choices[0].message.content or ""


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
