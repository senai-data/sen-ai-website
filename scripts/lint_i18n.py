#!/usr/bin/env python3
"""Pre-commit linter that flags language drift between the French marketing
site and the English app.

Why this exists : between March and July 2026 the public nav drifted from
100% French (Solution / Fonctionnalites / Resultats / Tarifs) to 6 English
labels beside 1 French one, and French strings leaked into the English app.
Nobody decided that - it happened one page at a time. This catches the next
one at commit time instead of six months later.

Usage :

    python scripts/lint_i18n.py            # staged files only
    python scripts/lint_i18n.py --all      # audit the whole repo
    python scripts/lint_i18n.py --strict   # exit 1 on any hit

Rules, mirroring memory `project_i18n_strategy` :

  R1  src/pages/app/** is ENGLISH-ONLY chrome. A French UI string there is a
      defect. Only user-facing copy is inspected - JSX text nodes and quoted
      strings. Data rendered at runtime (personas, questions, LLM answers) is
      in the scan's language and is not the linter's business.

  R2  Nav.astro / Footer.astro are FRENCH, because chrome describes
      destinations and the destinations are French. An English label added
      there is drift.

Deliberately NOT flagged, because they produced only false positives :
CSS (`var(--font-sans)`), regex literals matching French user content,
URL paths (`href="/pricing"`), and identifiers / data keys.

Escape hatch : append  i18n-ignore  in a comment on the offending line.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
R1_DIR = "src/pages/app"
R2_FILES = {"src/components/Nav.astro", "src/components/Footer.astro"}

# Matched against ACCENT-STRIPPED candidate copy, so "Confirmee" catches
# "Confirmée" and "lance" catches "lancé". Function words and UI verbs only -
# domain nouns (marque, concurrent, recherche) appear in legitimate French
# data examples and would drown the signal.
FRENCH = re.compile(
    r"\b("
    r"aucun|aucune|votre|vos|notre|nos|cette|cet|ces|"
    r"avec|pour|dans|depuis|chez|vous|nous|"
    r"veuillez|desole|desolee|"
    r"afficher|masquer|ajouter|modifier|supprimer|enregistrer|annuler|"
    r"valider|rechercher|chargement|telecharger|envoyer|creer|lancer|"
    r"fermer|ouvrir|choisir|selectionner|rejeter|"
    r"erreur|reussi|reussie|termine|terminee|echec|"
    r"confirmee|rejetee|livree|lance|lancee|"
    r"parametres|donnees|semaine"
    r")\b"
)

ENGLISH = re.compile(
    r"\b("
    r"pricing|log ?in|log ?out|sign ?up|sign ?in|start free|free trial|"
    r"for agencies|our story|resources|methodology|"
    r"settings|dashboard|features|about us|learn more|get started|"
    r"read more|see more|contact us|book a demo"
    r")\b"
)

# Candidate user-facing copy : JSX text nodes, and quoted strings holding a
# sentence (a space plus two consecutive letters).
JSX_TEXT = re.compile(r">([^<>{}]{4,}?)<")
QUOTED = re.compile(r"""['"]([^'"]*[A-Za-z]{2}[^'"]*\s[^'"]*)['"]""")

# Lines that are never user-facing copy.
SKIP_LINE = re.compile(
    r"^\s*(import|export|//|/\*|\*)"      # imports and comments
    r"|https?://"                          # URLs
    r"|/\\[bws]"                            # regex literals like /\bdois-je/
    r"|var\(--"                            # CSS custom properties
    r"|^\s*[.#&][\w-]+\s*\{"               # CSS selectors
    r"|^\s*[\w-]+\s*:\s*[^=]+;"            # CSS declarations
)

# Attribute values that are paths, not copy.
STRIP_ATTRS = re.compile(r"""(href|src|action|to)=['"][^'"]*['"]""")


def deaccent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def candidates(line: str) -> list[str]:
    line = STRIP_ATTRS.sub(" ", line)
    out = [m.group(1) for m in JSX_TEXT.finditer(line)]
    out += [m.group(1) for m in QUOTED.finditer(line)]
    return [c.strip() for c in out if c.strip()]


def staged() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            capture_output=True, text=True, cwd=ROOT, check=True,
        ).stdout
    except Exception:
        return []
    return [ROOT / l.strip() for l in out.splitlines() if l.strip().endswith(".astro")]


def every() -> list[Path]:
    paths = list((ROOT / R1_DIR).rglob("*.astro"))
    paths += [ROOT / f for f in R2_FILES]
    return [p for p in paths if p.exists()]


def rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def check(path: Path) -> list[tuple[int, str, str]]:
    relpath = rel(path)
    in_app = relpath.startswith(R1_DIR)
    is_chrome = relpath in R2_FILES
    if not (in_app or is_chrome):
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    hits: list[tuple[int, str, str]] = []
    in_style = False
    for n, raw in enumerate(lines, 1):
        low = raw.lower()
        if "<style" in low:
            in_style = True
        if "</style" in low:
            in_style = False
            continue
        if in_style or "i18n-ignore" in raw or SKIP_LINE.search(raw):
            continue

        for cand in candidates(raw):
            flat = deaccent(cand).lower()
            if in_app:
                m = FRENCH.search(flat)
                if m:
                    hits.append((n, "R1", f"French in the English app : {cand[:70]!r}"))
                    break
            if is_chrome:
                m = ENGLISH.search(flat)
                if m:
                    hits.append((n, "R2", f"English label in French chrome : {cand[:70]!r}"))
                    break
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    total = 0
    for path in sorted(set(every() if args.all else staged())):
        for n, rule, msg in check(path):
            print(f"{rel(path)}:{n}  [{rule}] {msg}")
            total += 1

    if total:
        print(f"\n{total} language-drift hit(s).")
        print("Doctrine : chrome public FR, app EN (memory project_i18n_strategy).")
        print("Append 'i18n-ignore' on a line to accept one.")
    elif args.all:
        print("No language drift found.")
    return 1 if (total and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
