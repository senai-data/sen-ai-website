"""GEO content patterns analyzer - heuristic checks based on Aggarwal et al.
"GEO: Generative Engine Optimization" (KDD '24, https://arxiv.org/abs/2311.09735).

The paper identifies seven content patterns that lift the chance of a page
being cited by an LLM. The three that help most often (the paper is explicit
that the size of the effect varies by domain, so no headline figure here) :
  - Cite Sources : authoritative external links inside the body.
  - Quotation Addition : direct expert / clinical quotes.
  - Statistics Addition : numerical claims (percentages, counts, ratios).

The four others (Authoritative Phrasing, Fluency, Easy-to-understand, Unique
Words) move the needle by a few points each, and are kept here so the audit
score is multi-dimensional rather than a single 'add stats' nag.

Everything below runs from plain HTML / text. No LLM call, no API key. That
keeps the audit free and deterministic - re-running it tomorrow on the same
page returns the same scores, which is what the UI Doherty-feedback loop and
the issue ticketing need.

Public surface :
    analyze_page(html, url, page_domain) -> dict
        Returns the {signals, scores, issues} structure described in
        api/migrations/047_scan_page_audits.sql.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Princeton GEO weights for the composite score. Top-three patterns
# weighted 2x because that's where the paper's measured lift lives.
PATTERN_WEIGHTS = {
    "statistics_addition":   2.0,
    "cite_sources":          2.0,
    "quotation_addition":    2.0,
    "authoritative_phrasing": 1.0,
    "fluency":               1.0,
    "easy_to_understand":    1.0,
    "unique_words":          1.0,
}

# Heuristic vocabulary for "authoritative phrasing". Bilingual (EN/FR) because
# our v1 client base mixes both. Word boundaries handled by the regex compile.
AUTHORITY_TERMS = (
    # Credentials
    r"Dr\.", r"Prof\.", r"Pr\.", r"PhD", r"M\.D\.",
    r"docteur", r"professeur", r"professeure",
    # Research vocabulary
    r"study", r"studies", r"étude", r"études",
    r"clinical trial", r"essai clinique",
    r"research shows", r"research found", r"recherches montrent",
    r"according to", r"selon",
    r"meta[- ]analysis", r"méta[- ]analyse",
    r"randomized", r"randomisé",
    # Expert phrasing
    r"expert", r"spécialiste",
    r"dermatologue", r"dermatologist",
    r"pharmacien(?:ne)?", r"pharmacist",
    r"recommended by", r"recommandé par",
    r"approved by", r"approuvé par",
)
_AUTHORITY_RE = re.compile(
    r"\b(" + "|".join(AUTHORITY_TERMS) + r")\b",
    re.IGNORECASE | re.UNICODE,
)

# Statistics : numbers tied to a unit (percentage, currency, time, count) or
# clinical-style claims. We deliberately exclude bare digits ("1, 2, 3 list")
# because those don't carry the GEO signal the paper measures.
_STAT_PATTERNS = (
    r"\d+(?:[.,]\d+)?\s*%",                          # percentages
    r"\d+(?:[.,]\d+)?\s*(?:patients?|cas|cases|sujets?|subjects?|participants?)",
    r"\d+(?:[.,]\d+)?\s*(?:semaines?|weeks?|jours?|days?|mois|months?|heures?|hours?|minutes?)",
    r"\d+(?:[.,]\d+)?\s*(?:fois|times)\s+(?:plus|moins|more|less)",
    r"\d+\s*sur\s*\d+",                              # "9 sur 10"
    r"\d+\s*(?:out\s*of|of)\s*\d+",                  # "9 out of 10"
    r"x\s*\d+",                                      # "x3", "x10"
    r"\d+(?:[.,]\d+)?\s*(?:mg|g|kg|ml|l|cl|cm|mm|°c|°f)",
)
_STAT_RE = re.compile("|".join(_STAT_PATTERNS), re.IGNORECASE | re.UNICODE)

# Quotation : block of text in straight or curly quotes, French guillemets,
# of at least 5 words. Excludes inline punctuation noise like "ok" or "5".
_QUOTE_PATTERNS = (
    r"\"([^\"]{20,500})\"",
    r"“([^”]{20,500})”",
    r"«\s*([^»]{20,500})\s*»",
)
_QUOTE_RES = [re.compile(p, re.UNICODE | re.DOTALL) for p in _QUOTE_PATTERNS]

# Sentence splitter, deliberately simple. Real NLP would use spaCy but that's
# a 600 MB dep for a one-line gain.
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]+", re.UNICODE)

# Words : letter sequences, allowing apostrophes and hyphens.
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'-]+", re.UNICODE)


def _same_site(href: str, page_domain: str) -> bool:
    """Treat anchor / relative / same-host links as internal."""
    if not href or not page_domain:
        return True
    if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
        return True
    try:
        h = urlparse(href).netloc.lower().removeprefix("www.")
        if not h:
            return True
        d = page_domain.lower().removeprefix("www.")
        return h == d or h.endswith("." + d) or d.endswith("." + h)
    except Exception:  # noqa: BLE001
        return True


def _extract_text(soup: BeautifulSoup) -> str:
    """Strip script/style and collapse whitespace. Returns the visible text."""
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return re.sub(r"\s+", " ", text).strip()


def _flesch_reading_ease(text: str) -> float | None:
    """Flesch Reading Ease (Kincaid 1948). Higher = easier to read.
    60-70 ≈ plain English ; under 30 = academic / dense.

    We implement it inline rather than pull `textstat` to avoid the lib's
    deps. Syllable estimation is approximate but good enough for a 0-100 chip.
    """
    sentences = [s for s in _SENTENCE_RE.findall(text) if s.strip()]
    words = _WORD_RE.findall(text)
    if not sentences or not words:
        return None

    def _syllables(word: str) -> int:
        w = word.lower()
        w = re.sub(r"[^a-zàâäéèêëîïôöùûüç]", "", w)
        if not w:
            return 0
        # Vowel groups, with French nasals counted once.
        groups = re.findall(r"[aàâäeéèêëiîïoôöuùûüy]+", w)
        n = len(groups)
        if w.endswith("e") and n > 1:
            n -= 1
        return max(1, n)

    n_sentences = len(sentences)
    n_words = len(words)
    n_syllables = sum(_syllables(w) for w in words)
    asl = n_words / n_sentences
    asw = n_syllables / n_words
    return round(206.835 - 1.015 * asl - 84.6 * asw, 1)


def _signals(soup: BeautifulSoup, text: str, page_domain: str) -> dict:
    """Compute the raw pattern signals before scoring."""
    # Cite Sources : count <a href> pointing OFF-site (excluding social /
    # tracking junk - we only count auth-looking domains).
    external = 0
    SOCIAL_HOSTS = {
        "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
        "youtube.com", "tiktok.com", "pinterest.com", "snapchat.com",
    }
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if _same_site(href, page_domain):
            continue
        try:
            h = urlparse(href).netloc.lower().removeprefix("www.")
            if h in SOCIAL_HOSTS:
                continue
            external += 1
        except Exception:  # noqa: BLE001
            continue

    # Statistics : count UNIQUE numerical claims (dedup the regex hits so
    # a templated repeat doesn't game the score).
    stat_hits = list({m.group(0).lower() for m in _STAT_RE.finditer(text)})
    statistics_count = len(stat_hits)

    # Quotations
    quote_hits: list[str] = []
    for rx in _QUOTE_RES:
        quote_hits.extend(m.group(1).strip() for m in rx.finditer(text))
    quotation_count = len(quote_hits)

    # Authoritative phrasing
    auth_hits = list({m.group(0).lower() for m in _AUTHORITY_RE.finditer(text)})
    authoritative_phrases_count = len(auth_hits)

    # Word / sentence stats
    words = _WORD_RE.findall(text)
    word_count = len(words)
    sentences = [s for s in _SENTENCE_RE.findall(text) if s.strip()]
    avg_sentence_length = round(word_count / max(1, len(sentences)), 1) if sentences else 0.0
    unique_terms_ratio = round(len({w.lower() for w in words}) / max(1, word_count), 2)
    flesch = _flesch_reading_ease(text)

    return {
        "statistics_count": statistics_count,
        "statistics_examples": stat_hits[:5],
        "external_citations_count": external,
        "quotation_count": quotation_count,
        "quotation_examples": [q[:120] for q in quote_hits[:3]],
        "authoritative_phrases_count": authoritative_phrases_count,
        "authoritative_examples": auth_hits[:5],
        "word_count": word_count,
        "sentence_count": len(sentences),
        "avg_sentence_length": avg_sentence_length,
        "flesch_score": flesch,
        "unique_terms_ratio": unique_terms_ratio,
    }


def _score_pattern(signal: float, thresholds: tuple[float, float, float]) -> int:
    """Map a raw signal value to a 0-100 score using 3-step thresholds :
    (weak_ceiling, thin_ceiling, strong_floor). Values at or above strong_floor
    score 100 ; values below weak_ceiling score under 30 ; thin sits in between.
    """
    weak_max, thin_max, strong_min = thresholds
    if signal >= strong_min:
        return 100
    if signal >= thin_max:
        # interpolate between 70 and 100
        return int(70 + 30 * (signal - thin_max) / max(1e-6, strong_min - thin_max))
    if signal >= weak_max:
        # interpolate between 30 and 70
        return int(30 + 40 * (signal - weak_max) / max(1e-6, thin_max - weak_max))
    # 0 .. 30 linear
    return int(30 * signal / max(1e-6, weak_max))


def _flesch_score_to_100(flesch: float | None) -> int:
    """Map Flesch reading ease to a 0-100 quality score.
    60-80 is the sweet spot (plain English). Above 90 is too simple (kids
    book) ; under 30 is academic dense."""
    if flesch is None:
        return 0
    if 60 <= flesch <= 80:
        return 100
    if 50 <= flesch < 60 or 80 < flesch <= 90:
        return 75
    if 30 <= flesch < 50 or 90 < flesch <= 100:
        return 50
    return 25


def _sentence_length_score(asl: float) -> int:
    """Sentence length sweet spot : 12-22 words. Penalize both extremes."""
    if asl == 0:
        return 0
    if 12 <= asl <= 22:
        return 100
    if 9 <= asl < 12 or 22 < asl <= 28:
        return 70
    if 6 <= asl < 9 or 28 < asl <= 35:
        return 40
    return 20


def _scores(s: dict) -> dict:
    """Convert raw signals to 0-100 scores per pattern."""
    return {
        "statistics_addition":   _score_pattern(s["statistics_count"],   (1, 4, 8)),
        "cite_sources":          _score_pattern(s["external_citations_count"], (1, 3, 6)),
        "quotation_addition":    _score_pattern(s["quotation_count"],    (1, 2, 4)),
        "authoritative_phrasing": _score_pattern(s["authoritative_phrases_count"], (1, 3, 6)),
        "fluency":               _sentence_length_score(s["avg_sentence_length"]),
        "easy_to_understand":    _flesch_score_to_100(s["flesch_score"]),
        "unique_words":          _score_pattern(s["unique_terms_ratio"], (0.25, 0.40, 0.55)),
    }


def _issues(signals: dict, scores: dict) -> list[dict]:
    """Translate weak / thin scores into concrete issues the UI can ticket."""
    out: list[dict] = []

    def add(pattern: str, severity: str, message: str) -> None:
        out.append({"pattern": pattern, "severity": severity, "message": message})

    if scores["statistics_addition"] < 40:
        add(
            "statistics_addition",
            "high" if scores["statistics_addition"] < 20 else "medium",
            f"Only {signals['statistics_count']} numerical claim(s) found. "
            "Add 3-5 statistics (percentages, patient counts, time-to-effect) - "
            "one of the patterns the Princeton GEO paper finds most effective, "
            "though the size of the effect varies by domain.",
        )
    if scores["cite_sources"] < 40:
        add(
            "cite_sources",
            "high" if scores["cite_sources"] < 20 else "medium",
            f"Only {signals['external_citations_count']} external authoritative link(s). "
            "Cite 2-3 sources (ANSM, HAS, journals, peer-reviewed studies) "
            "to boost LLM trust.",
        )
    if scores["quotation_addition"] < 40:
        add(
            "quotation_addition",
            "medium",
            f"Only {signals['quotation_count']} direct quote(s). "
            "Add 1-2 expert / clinical quotes (dermatologist, pharmacist, study lead).",
        )
    if scores["authoritative_phrasing"] < 40:
        add(
            "authoritative_phrasing",
            "low",
            f"Authoritative phrasing thin ({signals['authoritative_phrases_count']} hits). "
            "Reference studies, credentials, professional bodies more explicitly.",
        )
    if scores["fluency"] < 40:
        asl = signals["avg_sentence_length"]
        add(
            "fluency",
            "low",
            f"Average sentence length is {asl} words "
            f"({'too long' if asl > 22 else 'too short'}). "
            "Aim for 12-22 words per sentence.",
        )
    if scores["easy_to_understand"] < 40:
        add(
            "easy_to_understand",
            "low",
            f"Flesch reading ease score {signals['flesch_score']}. "
            "Aim for 60-80 (plain language). Break long sentences, prefer "
            "common words.",
        )
    if signals["word_count"] < 300:
        add(
            "thin_content",
            "high",
            f"Page body is only {signals['word_count']} words. "
            "Thin content gets de-prioritized by LLM retrieval. Aim for 600+ "
            "words of substantive content.",
        )
    return out


def _composite_score(scores: dict) -> int:
    """Weighted average across the seven patterns."""
    total_weight = sum(PATTERN_WEIGHTS.values())
    weighted = sum(scores[k] * PATTERN_WEIGHTS[k] for k in PATTERN_WEIGHTS)
    return round(weighted / total_weight)


def analyze_page(html: str, url: str, page_domain: str | None = None) -> dict:
    """Run the full 7-pattern Princeton GEO audit on a page.

    Args:
        html: raw HTML string of the page.
        url:  the URL we fetched (used as fallback for page_domain).
        page_domain: registered domain of the brand, used to classify
            internal vs external links. If None, derived from the URL host.

    Returns:
        {
          "signals": {...},   # raw counts, examples, page metadata
          "scores":  {...},   # 0-100 per pattern
          "issues":  [...],   # ticketable items
          "geo_score": int,   # composite 0-100
          "title": "...",     # <title> tag content
          "lang":  "fr",      # <html lang> attribute
        }
    """
    soup = BeautifulSoup(html or "", "html.parser")
    text = _extract_text(soup)
    if not page_domain:
        try:
            page_domain = urlparse(url).netloc
        except Exception:  # noqa: BLE001
            page_domain = ""

    sig = _signals(soup, text, page_domain or "")
    sc = _scores(sig)
    iss = _issues(sig, sc)
    composite = _composite_score(sc)

    title_tag = soup.find("title")
    html_tag = soup.find("html")
    return {
        "signals": sig,
        "scores": sc,
        "issues": iss,
        "geo_score": composite,
        "title": (title_tag.get_text(strip=True) if title_tag else None),
        "lang": (html_tag.get("lang") if html_tag else None),
    }
