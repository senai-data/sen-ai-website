"""Post-generation sanitizer gates for LLM output.

5 independent gates, each addressing one recurring LLM tell. Callers pick
which gates to run via the `gates` tuple on each mode config (see modes.py).

Lifted from :
  - C.1.7 sanitizers in worker/handlers/generate_article.py (brackets,
    sources_aside, review_tables)
  - worker/seo_llm/src/geo_content_generator.py:12364+ (fake_experts,
    anonymous_blocks)

Each gate is best-effort : it catches its own exceptions and returns the
input unchanged on failure. A sanitizer crash must NEVER kill the LLM
output - that would lose the user's content_credit.
"""

from __future__ import annotations
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ── Gate 1 - strip [lowercase_word] placeholder leaks ──────────────────────
# The seo-llm 60K system prompt teaches `[plateforme]`, `[author]`, `[text]`
# syntax as a "fill-me-in" pattern. The LLM occasionally mimics it inside
# quotes when uncertain about a word (« le [flacon] »). Restrictive regex :
# lowercase single-word with accents, no digits, no uppercase. We don't
# touch `[MARQUE]` (uppercase placeholder we DO want to flag manually),
# `[1]` (footnote marker), or `[Note 2]` (multi-word reference).
_BRACKET_RX = re.compile(r"\[([a-zàâäçéèêëîïôöùûüÿ]{2,})\]")


def strip_placeholder_brackets(html: str) -> str:
    """Replace `[mot]` with `mot` (lowercase single word only)."""
    if not html:
        return html
    try:
        return _BRACKET_RX.sub(r"\1", html)
    except Exception:
        logger.exception("sanitizers: strip_placeholder_brackets failed")
        return html


# ── Gate 2 - dedupe Sources <aside> block ──────────────────────────────────
# seo_llm._inject_sources_section emits `<aside class="sources">…</aside>`
# unconditionally at the end of every article. The validation page already
# renders the canonical Sources panel from content_metadata.sources_used,
# so the inline version is pure duplication that confuses the outline.
_HEADING_SOURCES_LOWER = {"sources", "sources :", "références", "references"}


def dedupe_sources_aside(html: str) -> str:
    """Remove the inline Sources aside / section / heading+list block."""
    if not html:
        return html
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for aside in soup.find_all("aside", class_="sources"):
            aside.decompose()
        for sec in soup.find_all("section", class_="sources"):
            sec.decompose()
        # Plain `<h2>Sources</h2><ul>…</ul>` variant without wrapping aside.
        for h2 in list(soup.find_all(["h2", "h3"])):
            heading_text = (h2.get_text(strip=True) or "").lower()
            if heading_text not in _HEADING_SOURCES_LOWER:
                continue
            sibling = h2.find_next_sibling()
            if sibling and sibling.name in {"ul", "ol"}:
                sibling.decompose()
            h2.decompose()
        return str(soup)
    except Exception:
        logger.exception("sanitizers: dedupe_sources_aside failed")
        return html


# ── Gate 3 - relinkify "Voir les avis" cells in review tables ──────────────
# The seo-llm prebuilt review table (geo_content_generator.py:6055-6058)
# ships `<a href="{url}">Voir les avis</a>` but the LLM frequently drops
# the anchor when rewriting the section. We rebuild it by matching the
# row's platform-name <td> against the domain map collected from every
# other `<a href>` in the document.
_REVIEW_LINK_RX = re.compile(
    r"\b(voir|lire|consulter)\s+(les?\s+)?(avis|reviews?|t[ée]moignages?)\b",
    re.IGNORECASE,
)
_DOMAIN_IN_TEXT_RX = re.compile(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)+)\b", re.IGNORECASE)


def relinkify_review_tables(html: str) -> str:
    """Re-wrap text-only « Voir les avis » cells in <a href> when domain
    match is found elsewhere in the document."""
    if not html:
        return html
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        href_by_domain: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith("#"):
                continue
            try:
                host = (urlparse(href).netloc or "").lower()
            except Exception:
                continue
            host = host[4:] if host.startswith("www.") else host
            if host and host not in href_by_domain:
                href_by_domain[host] = href

        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            last_cell = cells[-1]
            if last_cell.find("a"):
                continue
            text = last_cell.get_text(strip=True)
            if not text or not _REVIEW_LINK_RX.search(text):
                continue
            target_url = None
            for c in cells[:-1]:
                cell_text = c.get_text(" ", strip=True).lower()
                if not cell_text:
                    continue
                for m in _DOMAIN_IN_TEXT_RX.finditer(cell_text):
                    cand = m.group(1).lower()
                    cand = cand[4:] if cand.startswith("www.") else cand
                    if cand in href_by_domain:
                        target_url = href_by_domain[cand]
                        break
                if target_url:
                    break
                first_token = cell_text.split()[0] if cell_text.split() else ""
                if first_token and len(first_token) >= 4:
                    for dom, url in href_by_domain.items():
                        if first_token in dom:
                            target_url = url
                            break
                if target_url:
                    break
            if target_url:
                new_a = soup.new_tag("a", href=target_url)
                new_a["target"] = "_blank"
                new_a["rel"] = "noopener"
                new_a.string = text
                last_cell.clear()
                last_cell.append(new_a)
        return str(soup)
    except Exception:
        logger.exception("sanitizers: relinkify_review_tables failed")
        return html


# ── Gate 4 - strip hallucinated expert names ───────────────────────────────
# Lifted verbatim from geo_content_generator.py:12364. Replaces Dr / Pr /
# Professeur / Docteur mentions whose name is NOT in the brand_content
# whitelist with the generic « les spécialistes ». Protects E-E-A-T
# (Google's quality criterion for YMYL content).
_EXPERT_FIND_RX = re.compile(
    r'(?:Dr\.?|Pr\.?|Professeur|Docteur)\s+'
    r'([A-ZÀ-Ü][a-zà-ü]+(?:[\s-][A-ZÀ-Ü][a-zà-ü]*)*)'
)
_EXPERT_REPLACE_RX = re.compile(
    r'(Dr\.?|Pr\.?|Professeur|Docteur)\s+'
    r'([A-ZÀ-Ü][a-zà-ü]+(?:[\s-][A-ZÀ-Ü][a-zà-ü.]*)*)'
)


def strip_fake_experts(html: str, brand_content: str = "") -> str:
    """Replace expert names absent from brand_content with « les spécialistes »."""
    if not html:
        return html
    try:
        known = set()
        if brand_content:
            for m in _EXPERT_FIND_RX.finditer(brand_content):
                known.add(m.group(1).strip())

        def _replace(m):
            name = m.group(2).strip().rstrip(".,;:")
            if any(k in name or name in k for k in known):
                return m.group(0)
            return "les spécialistes"

        return _EXPERT_REPLACE_RX.sub(_replace, html)
    except Exception:
        logger.exception("sanitizers: strip_fake_experts failed")
        return html


# ── Gate 5 - remove anonymous expert blockquotes ───────────────────────────
# Lifted from geo_content_generator.py:12388. Removes <blockquote> blocks
# whose <cite> attribution is generic / unnamed. The LLM sometimes invents
# « Conseil d'expert en santé bucco-dentaire » or « les spécialistes »
# attributions, useless for E-E-A-T credibility.

_GENERIC_TRIGGERS = (
    "conseil d'expert", "conseil d expert",
    "expert en", "expert du", "expert dans",
    "selon les spécialistes", "les spécialistes",
    "les dermatologues", "les dentistes", "les chirurgiens",
    "un expert", "un spécialiste", "un professionnel", "un médecin",
    "avis d'expert", "parole de spécialiste",
)

_GENERIC_FIRST_WORDS = {
    "expert", "experts",
    "spécialiste", "spécialistes", "specialiste", "specialistes",
    "professionnel", "professionnels",
    "conseil", "conseils",
    "avis",
    "dermatologue", "dermatologues",
    "dentiste", "dentistes",
    "médecin", "médecins", "medecin", "medecins",
    "chirurgien", "chirurgiens",
    "pharmacien", "pharmaciens",
    "un", "une", "les", "des", "selon",
    "équipe", "equipe",
}

_NAME_PATTERN_RX = re.compile(
    r'\b[A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ][a-zàâäçéèêëîïôöùûüÿ]{1,}'
    r'(?:[\s-][A-ZÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸ][a-zàâäçéèêëîïôöùûüÿ]{1,})+'
)


def _cite_is_valid(cite_text: str) -> bool:
    """True if the <cite> contains an acceptable named attribution."""
    stripped = cite_text.strip().lstrip("-—–").strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    # Rule 1 : academic title + name = always OK
    if re.search(r'\b(Dr\.?|Pr\.?|Professeur|Docteur)\s+[A-ZÀ-Ü]', stripped):
        return True
    # Rule 2 : first word is a generic title = REJECT even if a name follows
    first_word = re.split(r'[\s,;]', lowered.strip(), 1)[0].strip()
    if first_word in _GENERIC_FIRST_WORDS:
        return False
    # Rule 3 : generic trigger phrase anywhere = REJECT
    if any(trigger in lowered for trigger in _GENERIC_TRIGGERS):
        return False
    # Rule 4 : two-capitalized-words name pattern = OK
    if _NAME_PATTERN_RX.search(stripped):
        return True
    return False


def remove_anonymous_blockquotes(html: str) -> str:
    """Delete <blockquote> blocks with anonymous / generic <cite> attribution."""
    if not html:
        return html
    try:
        removed = 0

        def _process(match):
            nonlocal removed
            full = match.group(0)
            cite_match = re.search(
                r'<cite[^>]*>(.*?)</cite>', full, re.DOTALL | re.IGNORECASE
            )
            if not cite_match:
                return full  # blockquote without cite : keep (direct quote)
            cite_inner = re.sub(r'<[^>]+>', '', cite_match.group(1))
            if _cite_is_valid(cite_inner):
                return full
            removed += 1
            return ""

        cleaned = re.sub(
            r'<blockquote[^>]*>.*?</blockquote>',
            _process, html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if removed:
            logger.info(f"sanitizers: removed {removed} anonymous blockquote(s)")
        return cleaned
    except Exception:
        logger.exception("sanitizers: remove_anonymous_blockquotes failed")
        return html


# ── Dispatcher ─────────────────────────────────────────────────────────────
# Map gate name (string used in modes.ModeConfig.sanitizer_gates) to the
# function that implements it. Sanitize() runs only the gates listed in
# the order they appear in `gates` - order matters for some pairs :
#   - review_tables BEFORE sources_aside (relinkify uses URLs that might
#     only live in the aside)
#   - fake_experts BEFORE anonymous_blocks (whitelisted-name detection
#     happens first ; then anonymous-cite removal cleans the residue)
_GATE_FUNCTIONS = {
    "brackets":         strip_placeholder_brackets,
    "sources_aside":    dedupe_sources_aside,
    "review_tables":    relinkify_review_tables,
    "fake_experts":     strip_fake_experts,
    "anonymous_blocks": remove_anonymous_blockquotes,
}


def sanitize(html: str, gates: tuple, brand_content: str = "") -> str:
    """Run the requested sanitizer gates in sequence.

    Args:
        html: the LLM-generated HTML to clean.
        gates: tuple of gate names (see ModeConfig.sanitizer_gates).
            Unknown names are skipped with a warning - safe by default.
        brand_content: scraped brand site content, used by strip_fake_experts
            to whitelist known names. Optional (empty = no whitelist, all
            unverified expert names get replaced).

    Returns the cleaned HTML. Never raises - each gate handles its own
    exceptions and returns its input unchanged on failure.
    """
    if not html:
        return html
    result = html
    for gate in gates:
        fn = _GATE_FUNCTIONS.get(gate)
        if fn is None:
            logger.warning(f"sanitizers: unknown gate '{gate}' - skipping")
            continue
        if gate == "fake_experts":
            result = fn(result, brand_content=brand_content)
        else:
            result = fn(result)
    return result
