"""Phase B intent taxonomy invariants.

Catches the silent drift bug where adding/removing intents in one of the two
sets (ALLOWED_CATEGORIES vs SAFETY_INTENTS) leaves the other untouched.
"""

from __future__ import annotations

import pytest

from services.intent_taxonomy import (
    ALLOWED_CATEGORIES,
    SAFETY_INTENTS,
    is_safety_intent,
)


class TestSubsetInvariant:
    def test_safety_intents_is_subset_of_allowed_categories(self):
        # Module-load assert already covers this; the explicit test makes
        # it visible in pytest output if it ever breaks.
        assert SAFETY_INTENTS.issubset(ALLOWED_CATEGORIES), (
            f"SAFETY_INTENTS \\ ALLOWED_CATEGORIES = "
            f"{SAFETY_INTENTS - ALLOWED_CATEGORIES}"
        )

    def test_allowed_categories_contains_other(self):
        # `other` is the fallback bucket when Haiku emits an unknown
        # category — removing it would re-introduce the NULL-on-unknown
        # bug that breaks downstream NULL-vs-classified logic.
        assert "other" in ALLOWED_CATEGORIES

    def test_allowed_categories_contains_promotional_fit(self):
        # `promotional_fit` is the legacy default per migration 035.
        # Removing it would silently turn every pre-classifier scan into
        # an "other" bucket and break re-runs of generate_opportunities.
        assert "promotional_fit" in ALLOWED_CATEGORIES


class TestIsSafetyIntent:
    @pytest.mark.parametrize("value", [None, "", "   ", "\t", "unknown_category"])
    def test_falsey_or_unknown_returns_false(self, value):
        assert is_safety_intent(value) is False

    @pytest.mark.parametrize("category", sorted(SAFETY_INTENTS))
    def test_each_safety_category_returns_true(self, category):
        assert is_safety_intent(category) is True

    def test_non_safety_categories_return_false(self):
        non_safety = ALLOWED_CATEGORIES - SAFETY_INTENTS
        for cat in non_safety:
            assert is_safety_intent(cat) is False, f"{cat} should not be safety"

    def test_whitespace_around_safety_category(self):
        # Real DB values can include trailing whitespace from CSV imports.
        assert is_safety_intent("  side_effects  ") is True

    def test_uppercase_is_treated_as_unknown(self):
        # Categories are lowercase by convention; we don't normalize here
        # because Haiku returns lowercase and any uppercase value is a
        # signal of pipeline corruption worth surfacing.
        assert is_safety_intent("SAFETY_WARNING") is False
