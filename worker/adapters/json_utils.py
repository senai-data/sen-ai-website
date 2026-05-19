"""Robust JSON extraction from LLM responses.

Replaces the brace-counter pattern duplicated across handlers
(persona_generator, question_generator, classify_question_intent, ...).
The naive brace counter doesn't respect string context: a `}` literal inside
a string value closes the JSON early. With LLM output where the prompt or
echoed user-typed content can contain `}` (French dialogue, code snippets,
LaTeX, etc.), this causes silent truncation or JSONDecodeError.

`json.JSONDecoder().raw_decode()` consumes one JSON value from the head of a
string while ignoring trailing prose — exactly what we need given that LLMs
occasionally produce leading/trailing markdown or commentary despite the
"JSON only" instruction.
"""

from __future__ import annotations

import json


def extract_json_object(text: str) -> dict:
    """Parse the first complete JSON object found in `text`.

    Strips optional ```json / ``` markdown fences and any leading prose
    before the first `{`. Trailing prose after the JSON is ignored.

    Raises:
        ValueError: no `{` found in text.
        json.JSONDecodeError: malformed JSON.
    """
    s = (text or "").strip()

    # Strip markdown fence — Haiku and Claude occasionally wrap JSON in
    # ```json ... ``` despite the "no markdown" rule. raw_decode would
    # otherwise fail on the leading backticks.
    if s.startswith("```"):
        # Drop opening fence (3 backticks + optional language tag)
        first_newline = s.find("\n")
        s = s[first_newline + 1:] if first_newline != -1 else s[3:]
        # Drop closing fence if still present
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()

    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in response")

    decoder = json.JSONDecoder()
    obj, _consumed = decoder.raw_decode(s[start:])
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object, got {type(obj).__name__}")
    return obj
