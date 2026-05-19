"""Noise filter for LLM-extracted brand names — multi-vertical, brief-driven.

The brand_analyzer over-extracts "brand-like" strings from LLM responses:
ingredients ("acide hyaluronique"), product types ("BB crème"), publications
("60 millions de consommateurs"), domain names ("aderma.fr"), random tokens.

Two layers:

1. **Technical filter** (always on, industry-agnostic): domains, hash-like
   blobs, leading-digit phrases, stopwords, length thresholds. Generic
   regex — works identically for cosmetics, automotive, food, B2B SaaS.

2. **Vertical filter** (optional, opt-in via param): a list of lowercase
   prefixes/terms produced by `generate_domain_brief` for the scan's
   vertical. The brief's `noise_patterns` field carries cosmetics-specific
   noise on a cosmetics scan, automotive-specific noise on an automotive
   scan, etc. Callers pass this list explicitly so the filter stays
   stateless and multi-tenant safe.

Callers should look up `scan.config.domain_brief.noise_patterns` and pass it
as `noise_prefixes=` — see `worker/handlers/run_llm_tests.py` and
`worker/adapters/brand_classifier.py`.

The filter is intentionally conservative — false negatives (let noise
through) are preferable to false positives (drop a real brand). Cleanup
downstream catches what slips through.
"""

from __future__ import annotations

import re
from typing import Sequence

# ── Industry-agnostic technical patterns ──────────────────────────────────

# Domain TLDs that signal a URL extracted as brand name.
_DOMAIN_TLD_RE = re.compile(
    r"\.(com|fr|net|org|io|eu|co|tv|me|de|es|it|be|ch|ca|uk)\b", re.IGNORECASE
)

# Looks like a hash / random alphanumeric jumble — require 16+ chars AND at
# least one digit, otherwise long alpha brand names ("Beiersdorf", "Spfectacular")
# would false-positive. Real LLM-echoed hashes are typically 24+ chars and
# always contain digits.
_HASH_LIKE_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9]{16,}$")

# Pure number or starts with digit + word (e.g. "60 millions de consommateurs",
# "4-methylbenzylidene camphor"). Real brands rarely lead with a digit.
_LEADING_DIGIT_PHRASE_RE = re.compile(r"^\d+[\s\-]")

# Common articles / connectors that shouldn't be a brand on their own. Kept
# minimal across FR + EN since stopwords are largely universal across
# Romance/Germanic languages we serve.
_STOPWORDS: frozenset[str] = frozenset({
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "ou", "ce",
    "cette", "ces", "mon", "ma", "mes", "son", "sa", "ses", "votre", "notre",
    "the", "a", "an", "and", "or", "el", "los", "las", "der", "die", "das",
})

# Length thresholds.
_MIN_LEN = 2
_MAX_LEN = 60


def is_noise_brand_name(
    name: str,
    noise_prefixes: Sequence[str] | None = None,
) -> bool:
    """Return True if `name` looks like LLM noise rather than a real brand.

    Args:
        name: candidate brand name to test.
        noise_prefixes: optional list of lowercase prefixes/terms that are
            vertical-specific noise. Typically passed from
            `scan.config.domain_brief.noise_patterns`. None or empty list =
            technical-only filtering.

    Caller should SKIP creating a `client_brands` row when this returns True.
    """
    if not name:
        return True
    s = name.strip()
    if len(s) < _MIN_LEN or len(s) > _MAX_LEN:
        return True

    low = s.lower()

    # Single-token stop word
    if low in _STOPWORDS:
        return True

    # Domain / URL
    if _DOMAIN_TLD_RE.search(low):
        return True

    # Hash-like blob
    if _HASH_LIKE_RE.match(s):
        return True

    # Leading digit phrase
    if _LEADING_DIGIT_PHRASE_RE.match(s):
        return True

    # Vertical noise — passed in from the brief. Match by prefix (e.g.
    # "crème" matches "crème hydratante", "crème réparatrice") OR exact
    # (single-word patterns like "spf" should only match the token itself,
    # not the start of a real brand). Heuristic: if the pattern contains
    # a space, prefix-match; otherwise exact-word match anywhere in the
    # name to avoid false positives like "Spfectacular" being flagged
    # because the pattern is "spf".
    if noise_prefixes:
        tokens = set(re.findall(r"\b[\wÀ-ÿ\-]+\b", low))
        for p in noise_prefixes:
            p_low = (p or "").strip().lower()
            if not p_low:
                continue
            if " " in p_low:
                if low.startswith(p_low + " ") or low == p_low:
                    return True
            else:
                if p_low in tokens:
                    return True

    # Pure punctuation / single chars after strip
    if not re.search(r"[A-Za-zÀ-ÿ]", s):
        return True

    return False


def filter_noise(
    names: list[str],
    noise_prefixes: Sequence[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split a list of candidate names into (real, noise) using `is_noise_brand_name`.

    Returns the lists in input order so callers can preserve side metadata.
    """
    real: list[str] = []
    noise: list[str] = []
    for n in names or []:
        (noise if is_noise_brand_name(n, noise_prefixes) else real).append(n)
    return real, noise
