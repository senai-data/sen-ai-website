"""JSON extraction edge cases — the brace-counter regressions the new
util (`extract_json_object`) fixes vs the old approach scattered across
handlers.
"""

from __future__ import annotations

import json
import pytest

from adapters.json_utils import extract_json_object


class TestBraceInsideString:
    def test_closing_brace_inside_string_value(self):
        # The whole point of using raw_decode: a `}` literal in a value
        # MUST NOT close the JSON. The old brace counter truncated here.
        text = '{"q": "is this safe? }really?"}'
        assert extract_json_object(text) == {"q": "is this safe? }really?"}

    def test_brace_pair_inside_string(self):
        text = '{"q": "use {tag} like this"}'
        assert extract_json_object(text) == {"q": "use {tag} like this"}

    def test_nested_braces_in_string_and_real(self):
        text = '{"a": "}", "b": {"c": 1}}'
        assert extract_json_object(text) == {"a": "}", "b": {"c": 1}}


class TestMarkdownFence:
    def test_json_fence(self):
        text = '```json\n{"x": 1}\n```'
        assert extract_json_object(text) == {"x": 1}

    def test_bare_fence(self):
        text = '```\n{"x": 1}\n```'
        assert extract_json_object(text) == {"x": 1}

    def test_fence_with_prose_inside(self):
        text = '```json\n{"x": 1}\nsome note\n```'
        # raw_decode stops at the end of the first JSON value; trailing
        # prose inside the fence is ignored.
        assert extract_json_object(text) == {"x": 1}


class TestSurroundingProse:
    def test_leading_prose(self):
        text = 'Here is the JSON:\n{"x": 1}'
        assert extract_json_object(text) == {"x": 1}

    def test_trailing_prose(self):
        text = '{"x": 1}\nThat is all.'
        assert extract_json_object(text) == {"x": 1}

    def test_both_sides(self):
        text = 'Sure! {"x": 1} let me know if you need more.'
        assert extract_json_object(text) == {"x": 1}


class TestErrors:
    def test_empty_text_raises(self):
        with pytest.raises(ValueError, match="No JSON object"):
            extract_json_object("")

    def test_no_brace_raises(self):
        with pytest.raises(ValueError, match="No JSON object"):
            extract_json_object("just prose, no JSON here")

    def test_malformed_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            extract_json_object('{"x": 1, "y":}')

    def test_top_level_array_rejected(self):
        # We only return dicts; an array at the top level would silently
        # break callers that expect .get(...). The util short-circuits on
        # the missing `{` so the ValueError message is the generic one.
        with pytest.raises(ValueError):
            extract_json_object('[1, 2, 3]')


class TestRealisticPayloads:
    def test_intent_classifier_response_with_french_quote(self):
        text = '''```json
{
  "classifications": [
    {"id": "abc", "intent_category": "safety_warning"},
    {"id": "def", "intent_category": "side_effects"}
  ]
}
```'''
        result = extract_json_object(text)
        assert len(result["classifications"]) == 2
        assert result["classifications"][0]["intent_category"] == "safety_warning"

    def test_persona_with_brace_in_question(self):
        # The kind of input that broke the old brace-counter: a generated
        # question containing a `}` (e.g. paraphrased prompt or code).
        text = '''{
  "personas": [{
    "nom": "Sophie",
    "questions": [{
      "question": "Comment fermer une div ? <div>...}</div>",
      "type_question": "technique"
    }]
  }]
}'''
        result = extract_json_object(text)
        assert result["personas"][0]["questions"][0]["question"].endswith("}</div>")
