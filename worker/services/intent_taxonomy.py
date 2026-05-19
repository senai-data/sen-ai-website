"""Phase B intent classification taxonomy — single source of truth.

Co-locates `ALLOWED_CATEGORIES` (what the Haiku classifier may output) and
`SAFETY_INTENTS` (the subset where third-party brand placement is editorially
inappropriate) so both move together. Prior to this module the two sets lived
in separate files (handlers/classify_question_intent.py + handlers/
generate_opportunities.py) and could drift silently — e.g. an intent removed
from ALLOWED but still present in SAFETY would create a phantom filter that
never triggers, with no signal.

The module-load assert at the bottom catches that drift on worker boot.

Changes here require coordinated updates in:
  - api/migrations/035_scan_question_intent_category.sql (doc / VARCHAR length)
  - worker/handlers/classify_question_intent.py (prompt enum + tests)
  - src/pages/app/content/[id].astro (UI chip color map)
"""

from __future__ import annotations

# All categories the Haiku classifier may return. Anything outside this set is
# coerced to "other" by the handler (see classify_question_intent._classify_batch).
ALLOWED_CATEGORIES: frozenset[str] = frozenset({
    "promotional_fit",
    "informational_neutral",
    "safety_warning",
    "side_effects",
    "contre_indication",
    "complaint_sav",
    "other",
})

# Intents where weaving a commercial brand recommendation reads awkwardly
# (pharma compliance / editorial fit). The opportunity scorer drops critique
# opportunities on these — see generate_opportunities.execute().
SAFETY_INTENTS: frozenset[str] = frozenset({
    "safety_warning",
    "side_effects",
    "contre_indication",
    "complaint_sav",
})


def is_safety_intent(intent_category: str | None) -> bool:
    """True iff intent blocks third-party brand placement.

    Treats None / empty / whitespace / unknown values as non-safety (= legacy
    promotional_fit default, consistent with migration 035 documentation).
    """
    if not intent_category:
        return False
    return str(intent_category).strip() in SAFETY_INTENTS


# Module-load invariant — fail fast on category-set drift between this module
# and any caller that adds intents. See tests/test_intent_taxonomy.py.
assert SAFETY_INTENTS.issubset(ALLOWED_CATEGORIES), (
    f"Phase B taxonomy invariant broken: SAFETY_INTENTS \\ ALLOWED_CATEGORIES "
    f"= {SAFETY_INTENTS - ALLOWED_CATEGORIES} — every safety intent must be a "
    f"category the classifier can output, otherwise the filter never matches."
)
