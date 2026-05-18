#!/usr/bin/env python3
"""Pre-commit linter that flags AI-tell vocabulary + signposting + em-dashes
in staged user-facing copy.

Usage :

    # Manual run on staged files (default - git diff --cached)
    python scripts/lint_ai_tells.py

    # Audit whole repo (baseline measurement)
    python scripts/lint_ai_tells.py --all

    # Run as pre-commit hook (add to .git/hooks/pre-commit)
    python scripts/lint_ai_tells.py && git diff-index --cached --quiet HEAD

Exit code is 0 (warnings only) by default so commits aren't blocked - this
is a nudge, not a gate. Pass --strict to exit 1 on any hit.

Scope :
- Files matched : .astro, .py, .md (excluding docs/STYLE_GUIDE.md itself,
  shared/natural_writing/ since it INTENTIONALLY contains the blacklist,
  worker/seo_llm/ since it's a submodule we don't own).
- String literals only (skips imports, regex patterns, URL strings).
- Triple-quoted block comments included (often where docstrings sit).

The rules mirror docs/STYLE_GUIDE.md - if you tweak one, tweak the other.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Force UTF-8 stdout so Unicode tokens (em-dash, accents) print on Windows cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # Python 3.7+
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Exclusions ────────────────────────────────────────────────────────────
EXCLUDE_PATHS = {
    "docs/STYLE_GUIDE.md",
    "shared/natural_writing/humanizer.py",
    "shared/natural_writing/sanitizers.py",
    "shared/natural_writing/modes.py",
    "shared/natural_writing/prompts.py",
    "shared/natural_writing/__init__.py",
    "scripts/lint_ai_tells.py",
}

EXCLUDE_DIRS = (
    "node_modules", ".git", "dist", "build", ".astro",
    "__pycache__", ".pytest_cache", ".venv", "venv",
    "worker/seo_llm",  # submodule
)

INCLUDE_EXTS = (".astro", ".py", ".md", ".ts", ".tsx", ".js", ".jsx")

# ── AI tells ──────────────────────────────────────────────────────────────
# Lowercase tokens flagged inside string literals. We do whole-word match
# to avoid false-positives in compound words.
AI_TELLS_EN = {
    "delve", "delving", "leverage", "leveraging", "navigate", "navigating",
    "tapestry", "nuanced", "intricate", "crucial", "pivotal", "vital",
    "seamlessly", "furthermore", "moreover",
    "enhance", "enhances", "enhancing", "foster", "fostering", "embark",
    "unleash", "unlock", "endeavor", "endeavour", "myriad", "plethora",
    "glean", "harness", "testament",
}

AI_TELLS_FR = {
    "représente", "représentent", "incarne", "incarnent",
    "constitue", "constituent",
    "incontournable", "indéniablement", "indubitablement",
    "néanmoins", "par ailleurs",
}

# Multi-word phrases flagged on substring match (case-insensitive).
AI_PHRASES = (
    "align with", "dive into", "dive deep",
    "it is important to note", "it should be noted",
    "in essence", "at its core", "the future looks", "looking ahead",
    "il est important de noter", "il convient de", "il est essentiel de",
    "il est crucial de", "il est fondamental de", "en conclusion",
    "force est de constater", "l'avenir s'annonce",
    "dans cet article", "nous allons explorer",
    "plongeons dans", "découvrons ensemble", "voyons maintenant",
    "méritent une attention particulière", "n'hésitez pas à",
    "les experts estiment",
    "let's dive", "let us dive", "let's explore", "let us explore",
    "let's look at", "here's what you need to know",
    "moment charnière", "rôle crucial",
)

# Em-dash + en-dash are anti-AI rule 5. Match literal Unicode chars.
EM_DASH = "—"
EN_DASH = "–"

# ── String-literal extraction ─────────────────────────────────────────────
# Naive but effective : pull anything between matching quote marks. Skips
# obvious code patterns (imports, regex backslashes, URLs).
#
# Triple-quoted strings are skipped for .py files (those are docstrings =
# developer documentation, not user copy - flagging em-dashes in module
# docstrings would be noise). For .astro/.ts/.tsx/.jsx we keep template
# strings (backticks) since those ARE often user-facing (UI labels).
STRING_RX_PY = re.compile(
    r'''
    (?<![A-Za-z_])
    (
        "(?:[^"\\\n]|\\.)*"
      | '(?:[^'\\\n]|\\.)*'
    )
    ''',
    re.DOTALL | re.VERBOSE,
)

STRING_RX_WEB = re.compile(
    r'''
    (?<![A-Za-z_])
    (
        "{3}.*?"{3}
      | '{3}.*?'{3}
      | "(?:[^"\\\n]|\\.)*"
      | '(?:[^'\\\n]|\\.)*'
      | `(?:[^`\\]|\\.)*`
    )
    ''',
    re.DOTALL | re.VERBOSE,
)

URL_RX = re.compile(r'https?://|www\.|\.com|\.fr|\.org|\.io')
CODE_NOISE_RX = re.compile(
    r'^(import |from |#!/|<\?xml|<!DOCTYPE|class=|className=)'
    r'|^[A-Z_]+_[A-Z_]+'   # ALL_CAPS_CONSTANTS
)

WORD_RX = re.compile(r"\b([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]{2,})\b")


def _is_user_facing(text: str) -> bool:
    """Heuristic : skip strings that look like code / config / URLs."""
    t = text.strip(' \'"`')
    if not t or len(t) < 8:
        return False
    if URL_RX.search(t):
        return False
    if CODE_NOISE_RX.match(t):
        return False
    # Skip if 70%+ of chars are non-letters (likely regex / data)
    letters = sum(1 for c in t if c.isalpha())
    if letters / max(1, len(t)) < 0.5:
        return False
    return True


def lint_text(text: str) -> list[tuple[str, str]]:
    """Return list of (rule, evidence) hits in a single string literal."""
    hits: list[tuple[str, str]] = []
    if not _is_user_facing(text):
        return hits
    low = text.lower()

    # Rule : em-dash / en-dash
    if EM_DASH in text:
        hits.append(("em-dash", EM_DASH))
    if EN_DASH in text:
        hits.append(("en-dash", EN_DASH))

    # Rule : multi-word AI phrases
    for phrase in AI_PHRASES:
        if phrase in low:
            hits.append(("phrase", phrase))

    # Rule : single-word AI tells
    words_in_text = {m.group(1).lower() for m in WORD_RX.finditer(text)}
    for tell in (AI_TELLS_EN | AI_TELLS_FR) & words_in_text:
        hits.append(("word", tell))

    return hits


def lint_file(path: Path) -> list[tuple[int, str, str, str]]:
    """Return list of (line_no, rule, evidence, snippet) hits for a file."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    hits: list[tuple[int, str, str, str]] = []
    rx = STRING_RX_PY if path.suffix == ".py" else STRING_RX_WEB
    for match in rx.finditer(source):
        literal = match.group(1)
        for rule, evidence in lint_text(literal):
            line_no = source[: match.start()].count("\n") + 1
            snippet = literal.replace("\n", " ").strip()[:80]
            hits.append((line_no, rule, evidence, snippet))
    return hits


def _is_excluded(rel_path: str) -> bool:
    if rel_path in EXCLUDE_PATHS:
        return True
    for ex_dir in EXCLUDE_DIRS:
        if rel_path.startswith(ex_dir + "/") or "/" + ex_dir + "/" in rel_path:
            return True
    return False


def collect_staged_files() -> list[Path]:
    """git diff --cached --name-only - returns staged files only."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    return [
        REPO_ROOT / line.strip() for line in out.splitlines()
        if line.strip() and line.endswith(INCLUDE_EXTS)
    ]


def collect_all_files() -> list[Path]:
    """All files in the repo matching INCLUDE_EXTS, minus EXCLUDE_DIRS."""
    files: list[Path] = []
    for ext in INCLUDE_EXTS:
        files.extend(REPO_ROOT.rglob(f"*{ext}"))
    # Filter out excluded dirs
    out: list[Path] = []
    for f in files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        if _is_excluded(rel):
            continue
        out.append(f)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flag AI-tell words / phrases / em-dashes in user-facing copy."
    )
    ap.add_argument(
        "--all", action="store_true",
        help="Scan the entire repo (baseline). Default = staged files only.",
    )
    ap.add_argument(
        "--strict", action="store_true",
        help="Exit 1 on any hit (block commit). Default = warn-only.",
    )
    ap.add_argument(
        "--max-per-file", type=int, default=20,
        help="Cap warnings per file in the output (default 20).",
    )
    args = ap.parse_args()

    files = collect_all_files() if args.all else collect_staged_files()
    if not files:
        print("lint_ai_tells: nothing to scan", file=sys.stderr)
        return 0

    total_hits = 0
    files_with_hits = 0

    for f in files:
        rel = f.relative_to(REPO_ROOT).as_posix()
        if _is_excluded(rel):
            continue
        hits = lint_file(f)
        if not hits:
            continue
        files_with_hits += 1
        total_hits += len(hits)
        print(f"\n{rel}  ({len(hits)} hit{'s' if len(hits) != 1 else ''})")
        for line_no, rule, evidence, snippet in hits[: args.max_per_file]:
            print(f"  L{line_no:<5} [{rule:6}] {evidence!r}  in: {snippet}")
        if len(hits) > args.max_per_file:
            print(f"  ... and {len(hits) - args.max_per_file} more (use --max-per-file=N to see)")

    if total_hits:
        print(
            f"\nlint_ai_tells: {total_hits} hit(s) across {files_with_hits} file(s). "
            f"See docs/STYLE_GUIDE.md for the rules.",
            file=sys.stderr,
        )
        return 1 if args.strict else 0
    print("lint_ai_tells: 0 hits - copy is clean.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
