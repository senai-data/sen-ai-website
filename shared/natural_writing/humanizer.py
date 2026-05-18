"""Anti-AI-detection rules fetcher + cache, lifted from seo-llm CLI.

Two sources :
  1. GitHub blader/humanizer - 29 detectable patterns + alternatives
  2. Wikipedia "Signs of AI writing" - flagged vocabulary + patterns

Both are cached locally (7 days by default) and re-fetched on expiry so the
service automatically picks up new detector evolutions without a code change.

Adapted for the SaaS multi-container layout : cache dir is now configurable
via NW_CACHE_DIR env var (defaults to a path under /app/cache so the Docker
volume mount can persist it across container rebuilds). Otherwise the file
is a verbatim lift of worker/seo_llm/src/humanizer.py - we kept the original
fetcher / parser / formatter logic untouched so future seo-llm humanizer
patches can be re-lifted with a simple diff.
"""

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Cache dir is configurable per deployment. The worker and api containers
# share a Docker volume mounted at /app/cache/natural_writing so the GitHub +
# Wikipedia fetches are not duplicated across containers and survive
# container rebuilds. Override via NW_CACHE_DIR if running outside Docker.
CACHE_DIR = Path(os.getenv("NW_CACHE_DIR", "/app/cache/natural_writing")) / "humanizer"
CACHE_DAYS = int(os.getenv("NW_HUMANIZER_CACHE_DAYS", os.getenv("HUMANIZER_CACHE_DAYS", "7")))

# Sources dynamiques
GITHUB_README_URL = (
    "https://raw.githubusercontent.com/blader/humanizer/main/README.md"
)
GITHUB_SKILL_URL = (
    "https://raw.githubusercontent.com/blader/humanizer/main/SKILL.md"
)
WIKIPEDIA_URL = (
    "https://en.wikipedia.org/w/index.php?title=Wikipedia:Signs_of_AI_writing&action=raw"
)

# Timeout pour les requêtes HTTP (secondes)
HTTP_TIMEOUT = 30


# ── Cache helpers ────────────────────────────────────────────────────────────

def _cache_path(source_key: str) -> Path:
    """Chemin du fichier cache pour une source donnée."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = hashlib.md5(source_key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{source_key}_{safe_key}.json"


def _read_cache(source_key: str) -> str | None:
    """Lit le cache si valide (< CACHE_DAYS jours). Retourne le texte ou None."""
    path = _cache_path(source_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_days = (time.time() - data.get("timestamp", 0)) / 86400
        if age_days < CACHE_DAYS:
            logger.info(f"Humanizer cache hit [{source_key}] ({age_days:.1f}j)")
            return data["content"]
        logger.info(f"Humanizer cache expiré [{source_key}] ({age_days:.1f}j)")
    except Exception:
        pass
    return None


def _write_cache(source_key: str, content: str) -> None:
    """Écrit le contenu dans le cache."""
    path = _cache_path(source_key)
    try:
        path.write_text(
            json.dumps(
                {"timestamp": time.time(), "source": source_key, "content": content},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning(f"Humanizer: échec écriture cache [{source_key}]: {exc}")


# ── Fetchers ─────────────────────────────────────────────────────────────────

_HTTP_HEADERS = {
    "User-Agent": "seo-llm-humanizer/1.0 (content quality tool)",
}


def _fetch_url(url: str) -> str | None:
    """Fetch une URL avec gestion d'erreurs et User-Agent approprié."""
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning(f"Humanizer: fetch échoué ({url}): {exc}")
        return None


def _fetch_github_humanizer() -> str | None:
    """Fetch le contenu du repo blader/humanizer (SKILL.md prioritaire, fallback README)."""
    cached = _read_cache("github_humanizer")
    if cached is not None:
        return cached

    # Essayer SKILL.md d'abord (contenu le plus actionnable)
    content = _fetch_url(GITHUB_SKILL_URL)
    if not content:
        content = _fetch_url(GITHUB_README_URL)
    if content:
        _write_cache("github_humanizer", content)
    return content


def _fetch_wikipedia_signs() -> str | None:
    """Fetch le wikitext brut de la page Wikipedia Signs of AI writing."""
    cached = _read_cache("wikipedia_signs")
    if cached is not None:
        return cached

    content = _fetch_url(WIKIPEDIA_URL)
    if content:
        _write_cache("wikipedia_signs", content)
    return content


# ── Parsers ──────────────────────────────────────────────────────────────────

def _parse_github_patterns(raw_md: str) -> list[dict]:
    """
    Parse le SKILL.md du repo blader/humanizer.

    Format attendu (numéroté) :
        ### 1. Pattern Name
        **Words to watch:** word1, word2, ...
        **Problem:** description
        **Before:** > example
        **After:** > example

    Retourne une liste de dicts :
        {"pattern": str, "words_to_watch": list[str], "problem": str}
    """
    patterns = []
    if not raw_md:
        return patterns

    # Découper en sections par les headers ### numérotés
    sections = re.split(r'###\s+\d+\.\s+', raw_md)

    for section in sections[1:]:  # skip preamble
        lines = section.strip().split('\n')
        if not lines:
            continue

        # Nom du pattern = première ligne
        name = lines[0].strip().rstrip('#').strip()
        if not name:
            continue

        # Extraire "Words to watch"
        words_match = re.search(
            r'\*\*Words to watch:\*\*\s*(.+?)(?:\n\n|\n\*\*)',
            section,
            re.DOTALL,
        )
        words = []
        if words_match:
            raw_words = words_match.group(1).strip()
            # Séparer par virgule ou slash
            words = [
                w.strip().strip('"').strip("'")
                for w in re.split(r'[,/]', raw_words)
                if w.strip() and len(w.strip()) > 1
            ]

        # Extraire "Problem"
        problem_match = re.search(
            r'\*\*Problem:\*\*\s*(.+?)(?:\n\n|\n\*\*)',
            section,
            re.DOTALL,
        )
        problem = problem_match.group(1).strip()[:200] if problem_match else ""

        patterns.append({
            "pattern": name,
            "words_to_watch": words[:15],  # limiter par pattern
            "problem": problem,
        })

    # Extraire aussi la liste de mots IA haute fréquence si présente
    hf_match = re.search(
        r'\*\*(?:High-frequency AI words|AI vocabulary).*?:\*\*\s*(.+?)(?:\n\n|\n###|\Z)',
        raw_md,
        re.DOTALL | re.IGNORECASE,
    )
    if hf_match:
        hf_words = [
            w.strip().strip('"').strip("'")
            for w in re.split(r'[,;]', hf_match.group(1))
            if w.strip() and len(w.strip()) > 1
        ]
        if hf_words:
            patterns.append({
                "pattern": "AI Vocabulary (haute fréquence)",
                "words_to_watch": hf_words[:30],
                "problem": "Mots statistiquement sur-représentés dans le texte IA",
            })

    return patterns


def _parse_wikipedia_signs(raw_wikitext: str) -> dict:
    """
    Parse le wikitext brut (143K chars) de Wikipedia:Signs_of_AI_writing.

    Extrait :
      - vocabulary: mots/expressions en italique signalés comme marqueurs IA
      - patterns: sections nommées décrivant des patterns structurels

    Retourne un dict :
        {"vocabulary": list[str], "patterns": list[dict]}
    """
    result: dict = {"vocabulary": [], "patterns": []}
    if not raw_wikitext:
        return result

    # ── Vocabulaire IA ──
    # La page utilise ''italique'' pour les mots signalés
    # Cibler la section "AI vocabulary" et ses sous-sections
    vocab_sections = re.finditer(
        r'===?\s*"?(?:AI vocabulary|Vocabulary|"AI vocabulary")'
        r'.*?===?\s*\n(.*?)(?=\n===?[^=]|\Z)',
        raw_wikitext,
        re.DOTALL | re.IGNORECASE,
    )
    for match in vocab_sections:
        text = match.group(1)
        # Mots en italique wiki ''mot''
        words = re.findall(r"''([^']{2,40})''", text)
        result["vocabulary"].extend(w.strip() for w in words if len(w.strip()) > 1)

    # Aussi chercher les listes de mots dans les sections "Words to watch"
    # ou "Characteristic words" dans le wikitext
    watch_sections = re.finditer(
        r'(?:words?\s+to\s+watch|characteristic|overused|common\s+AI)\s*[:\]]*\s*(.+?)(?:\n\n|\n=)',
        raw_wikitext,
        re.DOTALL | re.IGNORECASE,
    )
    for match in watch_sections:
        text = match.group(1)
        words = re.findall(r"''([^']{2,40})''", text)
        result["vocabulary"].extend(w.strip() for w in words if len(w.strip()) > 1)

    # Fallback : extraire TOUS les mots en italique de la page
    # (la page en est truffée pour signaler du vocabulaire IA)
    if len(result["vocabulary"]) < 10:
        all_italic = re.findall(r"''([^']{2,40})''", raw_wikitext)
        # Filtrer les termes qui sont clairement du vocabulaire (pas de wikilinks, etc.)
        for w in all_italic:
            w = w.strip()
            if (
                len(w) > 1
                and not w.startswith('[')
                and not w.startswith('{')
                and '=' not in w
                and '|' not in w
            ):
                result["vocabulary"].append(w)

    # ── Patterns nommés (sections === ou ==) ──
    named_sections = re.finditer(
        r'===\s*(.+?)\s*===\s*\n(.*?)(?=\n===|\n==\s|\Z)',
        raw_wikitext,
        re.DOTALL,
    )
    for match in named_sections:
        title = match.group(1).strip()
        body = match.group(2).strip()
        # Garder seulement les sections pertinentes (pas trop courtes)
        if len(body) > 50 and not title.startswith('See also'):
            # Extraire une description courte (première phrase/ligne)
            first_line = body.split('\n')[0].strip()
            # Nettoyer le wikitext
            first_line = re.sub(r"'''?|<ref[^>]*>.*?</ref>|\[\[|\]\]|\{\{.*?\}\}", '', first_line)
            result["patterns"].append({
                "name": title,
                "description": first_line[:200],
            })

    # Dédupliquer le vocabulaire tout en préservant l'ordre
    seen: set[str] = set()
    deduped: list[str] = []
    for w in result["vocabulary"]:
        low = w.lower()
        if low not in seen:
            seen.add(low)
            deduped.append(w)
    result["vocabulary"] = deduped

    return result


# ── Formatage pour injection prompt ─────────────────────────────────────────

def get_humanizer_rules(language: str = "fr") -> dict:
    """
    Récupère et parse les règles d'humanisation depuis les deux sources.

    Gère le cache automatiquement : re-fetch si > CACHE_DAYS jours.

    Args:
        language: Langue cible ("fr" par défaut)

    Returns:
        dict avec :
            - "github_patterns": list[dict] (29 patterns du repo humanizer)
            - "wikipedia_vocab": list[str] (vocabulaire signalé)
            - "wikipedia_patterns": list[dict] (patterns structurels)
            - "last_fetch": float (timestamp du dernier fetch)
    """
    github_raw = _fetch_github_humanizer()
    wiki_raw = _fetch_wikipedia_signs()

    github_patterns = _parse_github_patterns(github_raw) if github_raw else []
    wiki_data = _parse_wikipedia_signs(wiki_raw) if wiki_raw else {}

    return {
        "github_patterns": github_patterns,
        "wikipedia_vocab": wiki_data.get("vocabulary", []),
        "wikipedia_patterns": wiki_data.get("patterns", []),
        "last_fetch": time.time(),
    }


def format_humanizer_prompt_section(
    rules: dict | None = None,
    language: str = "fr",
    max_patterns: int = 30,
    max_vocab: int = 40,
) -> str:
    """
    Formate les règles d'humanisation en section injectable dans un prompt LLM.

    Si rules est None, fetch automatiquement les données.

    Args:
        rules: Résultat de get_humanizer_rules() ou None
        language: Langue cible
        max_patterns: Nombre max de patterns GitHub à inclure
        max_vocab: Nombre max de mots Wikipedia à inclure

    Returns:
        Texte formaté prêt à injecter dans le system prompt
    """
    if rules is None:
        rules = get_humanizer_rules(language)

    parts = []

    # ── Section 1 : Patterns de détection IA (GitHub humanizer) ──
    github_patterns = rules.get("github_patterns", [])
    if github_patterns:
        parts.append("## ANTI-DETECTION IA -- Patterns a eviter (source: humanizer)")
        parts.append(
            "Ces patterns sont detectes par les outils anti-IA. Les EVITER absolument :"
        )

        for p in github_patterns[:max_patterns]:
            name = p["pattern"]
            words = p.get("words_to_watch", [])
            problem = p.get("problem", "")
            if words:
                words_str = ", ".join(words[:8])
                parts.append(f"- NON: {name} -- Mots a eviter: {words_str}")
            elif problem:
                parts.append(f"- NON: {name} ({problem[:100]})")
            else:
                parts.append(f"- NON: {name}")

    # ── Section 2 : Vocabulaire IA (Wikipedia) ──
    wiki_vocab = rules.get("wikipedia_vocab", [])
    if wiki_vocab:
        parts.append("")
        parts.append("## VOCABULAIRE IA SIGNALÉ (source: Wikipedia)")
        parts.append(
            "Ces mots/expressions sont identifiés comme marqueurs d'écriture IA "
            "par la communauté Wikipedia. Les éviter ou les remplacer :"
        )
        # Limiter et formater
        vocab_display = wiki_vocab[:max_vocab]
        # Grouper par lignes de ~5 mots pour lisibilité
        lines = []
        for i in range(0, len(vocab_display), 5):
            chunk = vocab_display[i : i + 5]
            lines.append("  " + ", ".join(f'"{w}"' for w in chunk))
        parts.extend(lines)

    # ── Section 3 : Règles globales d'humanisation ──
    parts.append("")
    parts.append("## RÈGLES D'ÉCRITURE NATURELLE")
    if language == "fr":
        parts.extend([
            "- Privilégier les phrases courtes et directes, varier les longueurs",
            "- Utiliser « est », « a » au lieu de « représente », « incarne », « constitue »",
            "- PAS de listes de 3 systématiques (règle de trois) — varier 2, 4, 5 éléments",
            "- PAS de tirets cadratins (—) excessifs — utiliser virgules ou points",
            "- PAS de gras excessif (**mot**) — réserver aux vrais termes clés",
            "- PAS de conclusions génériques (« l'avenir s'annonce prometteur »)",
            "- PAS de phrases creuses (« il est important de noter que », « il convient de »)",
            "- PAS de synonymes forcés pour éviter les répétitions — répéter le terme clair",
            "- PAS d'inflation de significativité (« moment charnière », « rôle crucial »)",
            "- PAS de fausses attributions (« les experts estiment ») sans source nommée",
            "- PAS de hedging excessif (« pourrait potentiellement ») — un seul qualificatif",
            "- PAS de signposting (« plongeons dans », « voyons maintenant »)",
            "- Écrire comme un journaliste santé expérimenté, pas comme un chatbot",
        ])
    else:
        parts.extend([
            "- Use short, direct sentences; vary sentence lengths",
            "- Use 'is', 'has' instead of 'represents', 'embodies', 'constitutes'",
            "- NO systematic rule of three — vary list lengths (2, 4, 5 items)",
            "- NO excessive em dashes (—) — use commas or periods",
            "- NO excessive bold (**word**) — reserve for truly key terms",
            "- NO generic conclusions ('the future looks bright')",
            "- NO filler phrases ('it is important to note', 'it should be noted')",
            "- NO forced synonyms to avoid repetition — repeat the clearest term",
            "- NO significance inflation ('pivotal moment', 'crucial role')",
            "- NO vague attribution ('experts believe') without named sources",
            "- NO excessive hedging ('could potentially') — one qualifier only",
            "- NO signposting ('let's dive in', 'here's what you need to know')",
            "- Write like an experienced journalist, not a chatbot",
        ])

    return "\n".join(parts)


# ── API publique simplifiée ──────────────────────────────────────────────────

# Cache en mémoire pour éviter les lectures disque répétées au sein d'un même run
_rules_cache: dict | None = None
_rules_cache_ts: float = 0.0


def get_humanizer_prompt(language: str = "fr", compact: bool = False) -> str:
    """
    Point d'entrée principal — retourne la section prompt humanizer prête à l'emploi.

    Utilise un cache mémoire intra-run + cache disque inter-runs.
    Les sources sont re-fetchées automatiquement quand le cache disque expire.

    Args:
        language: Langue cible
        compact: Si True, réduit la sortie (~15 patterns, ~20 vocab)
                 pour les prompts courts comme FAQ. Par défaut False (mode complet).
    """
    global _rules_cache, _rules_cache_ts

    # Cache mémoire : 1h intra-run
    if _rules_cache and (time.time() - _rules_cache_ts) < 3600:
        return format_humanizer_prompt_section(
            _rules_cache, language,
            max_patterns=15 if compact else 30,
            max_vocab=20 if compact else 40,
        )

    rules = get_humanizer_rules(language)
    _rules_cache = rules
    _rules_cache_ts = time.time()

    return format_humanizer_prompt_section(
        rules, language,
        max_patterns=15 if compact else 30,
        max_vocab=20 if compact else 40,
    )
