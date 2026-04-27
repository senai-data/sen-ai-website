"""Input sanitization helpers — defense in depth against stored XSS.

All user-facing text inputs (scan names, topic names, persona names,
question text, brand names, etc.) should pass through strip_tags()
before being persisted to the database.
"""

from __future__ import annotations

import re
from typing import Optional

_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(text: Optional[str]) -> Optional[str]:
    """Remove HTML tags from a string. Returns None if input is None."""
    if text is None:
        return None
    return _TAG_RE.sub("", text).strip()
