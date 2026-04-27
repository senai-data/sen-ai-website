"""Disk operations for client deliverable reports.

Reports live at /opt/sen-ai/reports/{client}/{period}/{slug}/{filename}.html
with a flat symlink at /opt/sen-ai/reports/_serve/{slug}/ that Nginx alias-serves
under /r/{slug}/{filename}.html.

The `_serve/` symlink isolates the public URL from the on-disk organization:
the slug never reveals client or period, and the human-organized hierarchy is
private to anyone with VPS access.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import TypedDict

REPORTS_BASE = Path(os.environ.get("REPORTS_BASE", "/opt/sen-ai/reports"))
SERVE_DIR = REPORTS_BASE / "_serve"

_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_ROBOTS_META = '<meta name="robots" content="noindex, nofollow">'


class WriteResult(TypedDict):
    slug: str
    filename: str
    real_path: str
    file_size: int


def gen_slug(length: int = 12) -> str:
    """Crypto-secure alphanumeric slug. ~71 bits of entropy at length 12."""
    return "".join(secrets.choice(_ALPHA) for _ in range(length))


def slugify(s: str) -> str:
    """ASCII kebab-case. Strips diacritics, replaces non-alnum with '-'."""
    if not s:
        return ""
    norm = unicodedata.normalize("NFD", s.lower())
    stripped = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    cleaned = re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")
    return cleaned


def inject_robots_meta(html: str) -> str:
    """Inject <meta name=robots noindex,nofollow> into <head> if absent.

    Defense-in-depth: Nginx already sets X-Robots-Tag, but the meta tag
    survives if the file is downloaded and re-served elsewhere.
    """
    if re.search(r'(?is)<meta\s+name\s*=\s*"robots"', html):
        return html
    if re.search(r"(?is)<head[^>]*>", html):
        return re.sub(r"(?is)(<head[^>]*>)", r"\1\n    " + _ROBOTS_META, html, count=1)
    return _ROBOTS_META + "\n" + html


def write_report(
    html_bytes: bytes,
    original_filename: str,
    client: str,
    period: str,
) -> WriteResult:
    """Persist a report to disk + create the _serve/ symlink.

    Raises ValueError on invalid input.
    """
    try:
        text = html_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"File is not valid UTF-8: {e}") from e
    if "<html" not in text.lower():
        raise ValueError("File does not contain an <html> tag")

    text = inject_robots_meta(text)
    payload = text.encode("utf-8")

    client_slug = slugify(client)
    period_slug = slugify(period)
    if not client_slug:
        raise ValueError("Invalid client label (empty after slugification)")
    if not period_slug:
        raise ValueError("Invalid period label (empty after slugification)")

    name_stem = slugify(Path(original_filename).stem) or "report"
    final_name = f"{name_stem}.html"

    # Slug retry loop (collision is astronomically unlikely but cheap to handle)
    for _ in range(8):
        slug = gen_slug()
        real_dir = REPORTS_BASE / client_slug / period_slug / slug
        if not real_dir.exists():
            break
    else:
        raise RuntimeError("Failed to allocate unique slug after 8 attempts")

    real_dir.mkdir(parents=True, exist_ok=True)
    real_dir.chmod(0o755)
    real_path = real_dir / final_name
    real_path.write_bytes(payload)
    real_path.chmod(0o644)

    SERVE_DIR.mkdir(parents=True, exist_ok=True)
    SERVE_DIR.chmod(0o755)
    serve_link = SERVE_DIR / slug
    serve_target = Path("..") / client_slug / period_slug / slug
    if serve_link.is_symlink() or serve_link.exists():
        serve_link.unlink()
    serve_link.symlink_to(serve_target)

    return WriteResult(
        slug=slug,
        filename=final_name,
        real_path=str(real_path),
        file_size=len(payload),
    )


def remove_report(slug: str, real_path: str) -> None:
    """Remove the _serve/ symlink and the {slug}/ directory.

    Idempotent: missing files are silently OK. Parent client/period folders
    are intentionally NOT removed (they may still hold other reports).
    """
    serve_link = SERVE_DIR / slug
    if serve_link.is_symlink() or serve_link.exists():
        try:
            serve_link.unlink()
        except FileNotFoundError:
            pass

    real = Path(real_path)
    # Only remove the {slug}/ directory — the file's parent must literally be
    # named {slug} and live somewhere under REPORTS_BASE. Defensive guard.
    slug_dir = real.parent
    if (
        slug_dir.is_dir()
        and slug_dir.name == slug
        and REPORTS_BASE in slug_dir.resolve().parents
    ):
        shutil.rmtree(slug_dir, ignore_errors=True)
