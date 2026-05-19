"""Canonical ClientBrand name normalization — single source of truth.

`ClientBrand.canonical_name` is the dedup key for the (client_id, brand)
hierarchy. Pre-migration 038 it was set = raw `name`, which meant two
LLM responses disagreeing on case ("Avène" vs "avène") or accents
("Avène" vs "Avene") produced two distinct rows. After migration 038
the column has a UNIQUE index on (client_id, canonical_name) — every
caller MUST normalize via this helper before INSERT to avoid IntegrityError.

Pattern at call sites :

    from services.brand_name_norm import normalize_brand_name
    canonical = normalize_brand_name(raw_name)
    existing = (
        db.query(ClientBrand)
          .filter(ClientBrand.client_id == cid,
                  ClientBrand.canonical_name == canonical)
          .first()
    )
    if not existing:
        db.add(ClientBrand(
            client_id=cid,
            name=raw_name.strip(),       # display version, original capitalisation
            canonical_name=canonical,     # dedup key
            ...
        ))

A parallel copy lives at api/services/brand_name_norm.py — same code,
duplicated for now because worker + api don't share a venv. Update both
when the rule changes.
"""

from __future__ import annotations

import re
import unicodedata


_WS_RE = re.compile(r"\s+")


def normalize_brand_name(name: str | None) -> str:
    """Return a stable canonical_name suitable for dedup matching.

    Rules :
      - strip surrounding whitespace
      - lowercase
      - collapse internal whitespace runs to a single space
      - strip combining diacritics (NFD decomposition + drop Mn category)

    Empty input returns empty string ; callers should treat that as
    "skip this row" rather than insert a row with canonical_name="".
    """
    if not name:
        return ""
    s = str(name).strip().lower()
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    s = _WS_RE.sub(" ", s)
    return s
