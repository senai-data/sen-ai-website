"""Canonical ClientBrand name normalization — single source of truth.

API-side mirror of worker/services/brand_name_norm.py. Same code,
duplicated for now because worker + api don't share a venv. See the
worker docstring for the full pattern + rationale ; quick summary :

    from services.brand_name_norm import normalize_brand_name
    canonical = normalize_brand_name(raw_name)
    # lookup by canonical_name == canonical, INSERT with canonical_name=canonical
    # display name stays raw via the `name` column.
"""

from __future__ import annotations

import re
import unicodedata


_WS_RE = re.compile(r"\s+")


def normalize_brand_name(name: str | None) -> str:
    """Return a stable canonical_name suitable for dedup matching."""
    if not name:
        return ""
    s = str(name).strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    s = _WS_RE.sub(" ", s)
    return s
