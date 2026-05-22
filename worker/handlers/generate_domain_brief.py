"""Handler: generate domain brief with multi-provider fallback chain.

Produces a structured business-intelligence document about the scanned domain.
Stores in scan.config.domain_brief. Pre-populates Gate 2 with competitors from brief.

Provider strategy (3-tier fallback):
  1. Primary       — OpenAI Responses API + web_search tool (current/up-to-date data)
  2. Fallback #1   — Gemini with grounding (also web-aware, alternative provider)
  3. Last resort   — Anthropic Claude (training-knowledge only, no web access)
                     Quality lower for very recent/niche sites but useful for
                     well-known brands which is the dominant use case.
  4. All 3 fail    — RAISE. Three independent providers returning malformed JSON
                     in the same attempt is almost certainly a code/prompt bug, not
                     a transient provider issue. Worker retries up to max_attempts
                     (3); if still failing across all 9 calls, scan is marked failed
                     and the user sees a real error to investigate.

The brief is OPTIONAL context injected into 5 downstream prompts. We try hard
to produce one, but ultimately a parse failure is treated as a real bug rather
than silently skipped — the user explicitly asked for visibility on this case.
"""

import json
import logging
import re
from datetime import datetime

import httpx
import openai
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from models import Scan, ClientBrand, ScanBrandClassification
from schemas import DomainBrief, validate_object

logger = logging.getLogger(__name__)

WEB_BRIEF_PROMPT = """Research the website {domain} using web search and provide structured business intelligence.

You MUST search the web to find accurate, up-to-date information about this website/company.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "company": "Full company name with parent group if applicable",
  "description": "2-3 sentence description of what the company does, what they sell, through which channels",
  "industry": "Industry / Sub-industry",
  "country": "Primary market country",
  "brands": ["The brand of THIS website ONLY — not its parent group's other brands"],
  "product_lines": ["Product line name (purpose/category)" for each major product range],
  "services": ["Any services offered beyond products"],
  "competitors": [
    {{"name": "Competitor Name", "domain": "their-official-site.com", "products": ["Their ACTUAL named product ranges / sub-lines — NOT generic categories"]}}
  ],
  "topics": ["Key themes/topics the website covers"],
  "target_audience": "Description of who their customers are, demographics, needs",
  "noise_patterns": [
    "list of 15-30 lowercase prefixes/terms that should NEVER be classified as brands in this industry"
  ]
}}

# ⚠ Critical rule on "brands" vs "competitors" for grouped brands
If this website belongs to a corporate group (e.g. L'Oréal owns CeraVe + La Roche-Posay + Vichy; Pierre Fabre owns Avène + Ducray + Klorane; Beiersdorf owns Eucerin + Nivea):
  - `brands` MUST contain ONLY the brand of the scanned domain. Example: scan = ducray.com → brands = ["Ducray"], NOT ["Ducray", "Avène", "Klorane", …]. The sister brands are siblings, not "owned by Ducray".
  - The sister brands of the SAME parent group MUST appear in `competitors` if they compete on overlapping therapeutic areas / categories. Example: scan = ducray.com → competitors include Klorane (chute de cheveux competes Anaphase), Avène (Dermatite atopique competes Dexyane), René Furterer (chute capillaire), etc. Same-group brands are direct competitors on the shelf even if they share a parent.

For competitors, list 8-15 direct competitors. For each competitor's `products`, use their ACTUAL named product ranges / sub-lines (the specific line names a customer would recognise on the shelf, e.g. the named ranges — not "shampoo", "face care", "soins capillaires" or other generic categories) — same rule as `product_lines` below. These named ranges are what LLM answers actually cite, so generic categories are useless for competitor-mention detection.
IMPORTANT — relevance filter: list ONLY the competitor ranges that compete in the SAME categories as THIS brand's own product_lines / topics. Omit a competitor's ranges in categories this brand does NOT operate in (e.g. if this brand is hair-care + baby, skip a rival's acne, eczema or anti-ageing ranges — keep only its hair-care and baby ranges). Drop any competitor that has no overlapping range at all. The goal is a competitive set where every listed range is a true alternative to one of this brand's own ranges.
Include same-group sister brands when they compete on overlapping categories.
For product_lines, list the actual product range names of the scanned brand only, not generic categories.

For `noise_patterns`, list lowercase terms specific to this industry that an over-eager brand extractor would mistakenly tag as brands. Include:
- Generic product categories ("crème", "shampoo", "huile moteur", "smartphone")
- Common ingredients / materials ("acide hyaluronique", "vitamine c", "lithium", "coton")
- Sector-specific publications / magazines / institutions ("60 millions de consommateurs", "Que Choisir", "JD Power")
- Common acronyms in the field ("SPF", "OBD2", "ABS")
- Generic services or technical terms ("livraison", "abonnement", "open-source")
DO NOT include actual brand names. Match the language of the country (French for .fr, English for .com, etc.).
Examples by vertical:
- Cosmetics FR: ["crème", "gel", "sérum", "shampooing", "acide hyaluronique", "rétinol", "spf", "huile", "lait", "lotion", "bb crème", "60 millions de consommateurs", "que choisir"]
- Automotive: ["moteur", "freins", "huile moteur", "pneus", "obd2", "abs", "esp", "boite vitesse"]
- Food: ["huile d'olive", "sel", "sucre", "beurre", "farine", "bio", "label rouge"]
- Generic SaaS/B2B: ["subscription", "freemium", "open-source", "saas", "api", "white-label"]
"""

CLAUDE_FALLBACK_PROMPT = """Based on your training knowledge, provide structured business intelligence about the website {domain}.

If you don't have specific information about this exact domain, infer from the domain name and any related companies/brands you know about. Make conservative inferences and note uncertainty in the description rather than fabricating specifics.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "company": "Full company name with parent group if applicable",
  "description": "2-3 sentence description of what the company does, what they sell, through which channels",
  "industry": "Industry / Sub-industry",
  "country": "Primary market country",
  "brands": ["The brand of THIS website ONLY — not its parent group's other brands"],
  "product_lines": ["Product line names"],
  "services": ["Any services offered beyond products"],
  "competitors": [
    {{"name": "Competitor Name", "domain": "their-official-site.com", "products": ["Their ACTUAL named product ranges / sub-lines — NOT generic categories"]}}
  ],
  "topics": ["Key themes/topics the website covers"],
  "target_audience": "Description of who their customers are",
  "noise_patterns": [
    "lowercase prefixes/terms in this industry that should NEVER be classified as brands"
  ]
}}

# ⚠ Critical rule on "brands" vs "competitors" for grouped brands
If this website belongs to a corporate group (e.g. L'Oréal owns CeraVe + La Roche-Posay + Vichy; Pierre Fabre owns Avène + Ducray + Klorane; Beiersdorf owns Eucerin + Nivea):
  - `brands` MUST contain ONLY the brand of the scanned domain.
  - Sister brands of the SAME parent group MUST appear in `competitors` if they compete on overlapping categories. Even when they share a parent, they're direct competitors on the shelf.

For competitors, list 8-15 direct competitors. For each competitor's `products`, use their ACTUAL named product ranges / sub-lines (the specific line names), NOT generic product categories — these named ranges are what LLM answers cite.
IMPORTANT — relevance filter: list ONLY the competitor ranges that compete in the SAME categories as THIS brand's own product_lines / topics. Omit ranges in categories this brand does not operate in, and drop any competitor with no overlapping range. Every listed range should be a true alternative to one of this brand's own ranges.
Include same-group sister brands when relevant.

For noise_patterns, list 15-30 lowercase terms specific to this industry that an over-eager brand extractor would wrongly tag as brands (generic product categories, ingredients, sector publications, common acronyms). DO NOT include actual brand names. Match the country language.
"""


def _extract_json(text: str) -> dict | None:
    """Robust JSON extraction: strips markdown fences, falls back to brace-counter."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{', text)
    if not match:
        return None

    depth = 0
    for i in range(match.start(), len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[match.start():i + 1])
                except json.JSONDecodeError:
                    return None

    # Salvage a TRUNCATED object (provider stopped mid-output): the braces never
    # balanced. Walk the tail, drop the trailing partial token, and append the
    # missing closers. Recovers the common "cut between fields/array items" case;
    # gives up if truncation lands inside a string value.
    return _salvage_truncated_json(text[match.start():])


def _salvage_truncated_json(frag: str) -> dict | None:
    """Best-effort recovery of an unterminated JSON object.

    Cut at the last top-level separator (comma) — where every preceding value is
    complete — then append the closers for whatever containers were open AT that
    point. Snapshotting the stack at the cut (not at EOF) is essential.
    """
    stack: list[str] = []
    in_str = False
    escape = False
    cut_at = -1
    cut_stack: list[str] | None = None
    for i, ch in enumerate(frag):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack:
                stack.pop()
        elif ch == ",":
            cut_at = i  # values before a comma are complete
            cut_stack = list(stack)
    # cut_at is always a structural comma (commas inside strings are skipped), so
    # an unterminated string in the discarded tail doesn't matter here.
    if cut_at < 0 or not cut_stack:
        return None
    candidate = frag[:cut_at] + "".join(reversed(cut_stack))
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _try_openai(domain: str, api_key: str, model: str) -> tuple[dict | None, str, dict]:
    """Primary: OpenAI Responses API + web_search. Returns (parsed_or_None, raw_text, usage)."""
    client = openai.OpenAI(api_key=api_key, timeout=60)
    prompt = WEB_BRIEF_PROMPT.format(domain=domain)
    # NW.2 - inject anti-AI-detection humanizer block (compact mode).
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search"}],
        input=prompt,
        temperature=0.3,
        # Explicit ceiling so the richer named-gammes brief can't get clipped by a
        # low default (truncated JSON → silent fallback to the sloppier Gemini).
        max_output_tokens=8000,
    )
    text = response.output_text or ""
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
    }
    return _extract_json(text), text, usage


def _try_gemini(domain: str, api_key: str, model: str) -> tuple[dict | None, str, dict]:
    """Fallback #1: Gemini with grounding (web-aware). Returns (parsed_or_None, raw_text, usage)."""
    from seo_llm.src.llm_client import LLMClient
    client = LLMClient(provider="gemini", api_key=api_key, model=model)
    prompt = WEB_BRIEF_PROMPT.format(domain=domain)
    # NW.2 - inject anti-AI-detection humanizer block (compact mode).
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    response = client.generate(
        prompt,
        temperature=0.3,
        max_tokens=8000,
        use_grounding=True,
        agent_name="generate_domain_brief_gemini",
    )
    text = response.get("text", "")
    usage = {
        "input_tokens": response.get("usage", {}).get("prompt_tokens", 0)
                        or response.get("usage", {}).get("input_tokens", 0),
        "output_tokens": response.get("usage", {}).get("completion_tokens", 0)
                         or response.get("usage", {}).get("output_tokens", 0),
    }
    return _extract_json(text), text, usage


def _try_claude(domain: str, api_key: str, model: str) -> tuple[dict | None, str, dict]:
    """Last resort: Claude with training knowledge. Returns (parsed_or_None, raw_text, usage)."""
    prompt = CLAUDE_FALLBACK_PROMPT.format(domain=domain)
    # NW.2 - inject anti-AI-detection humanizer block (compact mode).
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(
            "https://api.anthropic.com/v1/messages",
            json=payload, headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    text = data.get("content", [{}])[0].get("text", "")
    return _extract_json(text), text, data.get("usage", {})


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise ValueError(f"Scan {scan_id} not found")

    # Skip if user already edited the brief
    existing_brief = (scan.config or {}).get("domain_brief")
    if existing_brief and existing_brief.get("edited_by_user"):
        logger.info(f"Brief already edited by user for scan {scan_id}, skipping generation")
        return {"status": "skipped", "reason": "user_edited"}

    domain = scan.domain
    scan.progress_message = f"Researching {domain}..."
    db.commit()

    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    brief = None
    used_provider = None
    raw_texts = {}

    # ── Tier 1: OpenAI + web_search ─────────────────────────────────────
    primary_model = settings.task_models["generate_domain_brief"]
    logger.info(f"Generating brief for {domain} via OpenAI ({primary_model}) + web_search")
    try:
        parsed, raw, usage = _try_openai(domain, settings.openai_api_key, primary_model)
        raw_texts["openai"] = raw
        if parsed:
            brief = parsed
            used_provider = "openai"
            from adapters.llm_logger import log_llm_usage
            log_llm_usage(
                db, provider="openai", model=primary_model,
                operation="generate_domain_brief",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                scan_id=scan_id, client_id=str(scan.client_id),
            )
        else:
            logger.warning(
                f"OpenAI returned malformed JSON for {domain} ({len(raw)} chars). "
                f"Raw start: {raw[:200]}"
            )
    except Exception as e:
        logger.warning(f"OpenAI request threw exception for {domain}: {e}")

    # ── Tier 2: Gemini with grounding ───────────────────────────────────
    from services.gemini_key_pool import get_gemini_pool
    gemini_pool = get_gemini_pool()
    if brief is None and gemini_pool.has_keys():
        gemini_model = settings.task_models["generate_domain_brief_gemini"]
        logger.warning(
            f"OpenAI did not produce a usable brief for {domain}, "
            f"falling back to Gemini ({gemini_model}) with grounding"
        )
        gemini_key = gemini_pool.next_key()
        try:
            parsed, raw, usage = _try_gemini(domain, gemini_key, gemini_model)
            raw_texts["gemini"] = raw
            if parsed:
                brief = parsed
                used_provider = "gemini"
                from adapters.llm_logger import log_llm_usage
                log_llm_usage(
                    db, provider="gemini", model=gemini_model,
                    operation="generate_domain_brief_gemini",
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    scan_id=scan_id, client_id=str(scan.client_id),
                )
            else:
                logger.warning(
                    f"Gemini also returned malformed JSON for {domain} ({len(raw)} chars). "
                    f"Raw start: {raw[:200]}"
                )
        except Exception as e:
            err = str(e)
            if "429" in err or "rate" in err.lower() or "RESOURCE_EXHAUSTED" in err:
                gemini_pool.mark_rate_limited(gemini_key)
            logger.warning(f"Gemini request threw exception for {domain}: {e}")

    # ── Tier 3: Claude (training only, no web) ──────────────────────────
    if brief is None and settings.anthropic_api_key:
        claude_model = settings.task_models["generate_domain_brief_claude"]
        logger.warning(
            f"Gemini did not produce a usable brief for {domain}, "
            f"falling back to Claude ({claude_model}, training-knowledge only)"
        )
        try:
            parsed, raw, usage = _try_claude(domain, settings.anthropic_api_key, claude_model)
            raw_texts["claude"] = raw
            if parsed:
                brief = parsed
                used_provider = "claude"
                from adapters.llm_logger import log_llm_usage
                log_llm_usage(
                    db, provider="anthropic", model=claude_model,
                    operation="generate_domain_brief_claude",
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    scan_id=scan_id, client_id=str(scan.client_id),
                )
            else:
                logger.warning(
                    f"Claude also returned malformed JSON for {domain} ({len(raw)} chars). "
                    f"Raw start: {raw[:200]}"
                )
        except Exception as e:
            logger.warning(f"Claude request threw exception for {domain}: {e}")

    # ── All 3 failed → raise (real bug, not transient) ──────────────────
    if brief is None:
        sizes = {p: len(t) for p, t in raw_texts.items()}
        raise RuntimeError(
            f"Brief generation failed across all 3 providers for {domain}. "
            f"Response sizes: {sizes}. This is likely a prompt/code bug — "
            f"three independent providers don't produce malformed JSON simultaneously."
        )

    # ── Pydantic validation: skip on failure (don't fail the scan) ──────
    try:
        brief_validated = validate_object(brief, DomainBrief, "generate_domain_brief")
        brief = brief_validated.model_dump()
    except RuntimeError as e:
        logger.warning(
            f"Brief validation failed for {domain} (provider={used_provider}), "
            f"scan continues without brief: {e}"
        )
        return {
            "status": "skipped",
            "reason": "validation_failed",
            "provider": used_provider,
        }

    logger.info(
        f"Brief generated for {domain} (provider={used_provider}): "
        f"{brief.get('company', '?')} — {brief.get('industry', '?')}"
    )

    # ── Persist + pre-populate Gate 2 ───────────────────────────────────
    # Increment the regen counter (success-only — failed runs don't burn the
    # budget). API endpoint caps at MAX_DOMAIN_BRIEF_GENERATIONS via the same
    # field. See feedback_cap_user_triggered_llm_ops.
    config = dict(scan.config or {})
    prev_brief = config.get("domain_brief") or {}
    brief["generations_count"] = int(prev_brief.get("generations_count") or 0) + 1
    config["domain_brief"] = brief
    config["domain_brief_provider"] = used_provider  # for audit/debug
    scan.config = config
    flag_modified(scan, "config")
    scan.updated_at = datetime.utcnow()
    db.commit()

    # Pre-populate Gate 2 with competitors + their product lines from brief.
    # Deduplication is essential: the LLM-generated brief frequently lists the
    # same competitor twice (e.g. "La Roche-Posay" appearing in both a primary
    # and an extended block). We track names case-insensitively to skip dupes
    # within a single brief and across re-generations (idempotent).
    competitors_created = 0
    gammes_created = 0

    def _classify_as_competitor(brand_id):
        """Upsert SBC=competitor. Never overwrite my_brand or focus rows."""
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.brand_id == brand_id,
        ).first()
        if not sbc:
            db.add(ScanBrandClassification(
                scan_id=scan_id,
                brand_id=brand_id,
                classification="competitor",
                is_focus=False,
                classified_by="brief",
                source="brief",
            ))
            return True
        # Existing row — never demote my_brand and never strip focus.
        if sbc.classification == "my_brand" or sbc.is_focus:
            return False
        if sbc.classification == "unclassified":
            sbc.classification = "competitor"
            sbc.classified_by = "brief"
            sbc.source = "brief"
            return True
        return False

    from services.brand_name_norm import normalize_brand_name
    seen_brands: set[str] = set()
    for comp in brief.get("competitors", []):
        comp_name = (comp.get("name") or "").strip()
        comp_norm = normalize_brand_name(comp_name)
        if not comp_norm or comp_norm in seen_brands:
            continue
        seen_brands.add(comp_norm)

        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            ClientBrand.canonical_name == comp_norm,
        ).first()

        # The LLM may have given us the competitor's official domain — set it
        # on the catalog row so the UI shows "bioderma.fr" under "Bioderma"
        # consistently. Empty string from the LLM = unknown → leave alone.
        comp_domain = (comp.get("domain") or "").strip().lower() or None

        if not existing:
            brand = ClientBrand(
                client_id=scan.client_id,
                name=comp_name,
                canonical_name=comp_norm,
                domain=comp_domain,
                detected_in_scan_id=scan_id,
                auto_detected=True,
                validated_by_user=False,
                detection_source="brief",
                last_seen_at=datetime.utcnow(),
            )
            db.add(brand)
            db.flush()
        else:
            brand = existing
            existing.last_seen_at = datetime.utcnow()
            # Backfill domain when missing — never overwrite a domain the
            # user may have curated. Same idea as parent_id reparent: only
            # touch rows that are still in their auto-detected default.
            if comp_domain and not existing.domain:
                existing.domain = comp_domain

        if _classify_as_competitor(brand.id):
            competitors_created += 1

        # If the root brand is already my_brand / focus / ignored, skip its
        # products: the LLM occasionally hallucinates the user's own brand
        # into the competitors list (e.g. "Avène" with products Cleanance,
        # Soins Solaires) — creating those as competitor children would
        # poison the visibility metrics by counting Avène product mentions
        # as competitor mentions.
        root_sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.brand_id == brand.id,
        ).first()
        if root_sbc is None or root_sbc.classification not in ("competitor", "unclassified"):
            continue

        # Product lines (gammes) as children of the competitor brand.
        seen_gammes: set[str] = set()
        for prod_name in (comp.get("products") or []):
            prod_name = (prod_name or "").strip()
            prod_norm = normalize_brand_name(prod_name)
            if not prod_norm or prod_norm in seen_gammes:
                continue
            # Drop echoes of the parent name the LLM sometimes injects.
            if prod_norm == comp_norm:
                continue
            seen_gammes.add(prod_norm)

            existing_gamme = db.query(ClientBrand).filter(
                ClientBrand.client_id == scan.client_id,
                ClientBrand.canonical_name == prod_norm,
            ).first()
            if not existing_gamme:
                gamme = ClientBrand(
                    client_id=scan.client_id,
                    name=prod_name,
                    canonical_name=prod_norm,
                    parent_id=brand.id,
                    detected_in_scan_id=scan_id,
                    auto_detected=True,
                    validated_by_user=False,
                    detection_source="brief",
                    last_seen_at=datetime.utcnow(),
                )
                db.add(gamme)
                db.flush()
                gammes_created += 1
            else:
                gamme = existing_gamme
                existing_gamme.last_seen_at = datetime.utcnow()
                # Re-parent ONLY if the row is currently a root orphan
                # (parent_id IS NULL). This is the common case for brands
                # created by detect_competitors earlier in the pipeline,
                # which has no hierarchy knowledge — the brief fills it.
                # If parent_id IS NOT NULL, the row already belongs under
                # some parent (potentially via a manual user reclassif), so
                # we leave it alone to preserve that reorganisation.
                if existing_gamme.parent_id is None:
                    existing_gamme.parent_id = brand.id

            _classify_as_competitor(gamme.id)

    # Pre-populate own brands
    own_gammes_created = 0
    primary_own_brand = None
    for own_brand_name in brief.get("brands", []):
        own_brand_name = (own_brand_name or "").strip()
        own_norm = normalize_brand_name(own_brand_name)
        if not own_norm:
            continue

        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            ClientBrand.canonical_name == own_norm,
        ).first()

        if not existing:
            brand = ClientBrand(
                client_id=scan.client_id,
                name=own_brand_name,
                canonical_name=own_norm,
                detected_in_scan_id=scan_id,
                auto_detected=True,
                validated_by_user=False,
                last_seen_at=datetime.utcnow(),
            )
            db.add(brand)
            db.flush()
        else:
            brand = existing

        if primary_own_brand is None:
            primary_own_brand = brand

        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.brand_id == brand.id,
        ).first()
        if not sbc:
            db.add(ScanBrandClassification(
                scan_id=scan_id,
                brand_id=brand.id,
                classification="my_brand",
                is_focus=False,
                classified_by="brief",
                source="brief",
            ))

    # Own product lines (gammes) → my_brand children of the primary own brand.
    # Mirror of the competitor-gamme pre-population: without this the focus brand
    # has no tracked sub-lines while every competitor does. product_lines arrive
    # as "Name (purpose/category)" strings — keep only the line name. Attribute to
    # the first own brand (single-brand briefs); portfolio scans that share a
    # domain curate their own-brand gammes via a dedicated fix script instead.
    if primary_own_brand is not None:
        seen_own_gammes: set[str] = set()
        for pl in (brief.get("product_lines") or []):
            pl_name = re.split(r"\s*\(", (pl or "").strip(), maxsplit=1)[0].strip()
            pl_norm = normalize_brand_name(pl_name)
            if (not pl_norm or pl_norm in seen_own_gammes
                    or pl_norm == primary_own_brand.canonical_name):
                continue
            seen_own_gammes.add(pl_norm)

            existing_g = db.query(ClientBrand).filter(
                ClientBrand.client_id == scan.client_id,
                ClientBrand.canonical_name == pl_norm,
            ).first()
            if not existing_g:
                g = ClientBrand(
                    client_id=scan.client_id,
                    name=pl_name,
                    canonical_name=pl_norm,
                    parent_id=primary_own_brand.id,
                    detected_in_scan_id=scan_id,
                    auto_detected=True,
                    validated_by_user=False,
                    detection_source="brief",
                    last_seen_at=datetime.utcnow(),
                )
                db.add(g)
                db.flush()
                own_gammes_created += 1
            else:
                g = existing_g
                existing_g.last_seen_at = datetime.utcnow()
                if existing_g.parent_id is None:
                    existing_g.parent_id = primary_own_brand.id

            # Classify my_brand — but never flip an existing competitor/focus row.
            g_sbc = db.query(ScanBrandClassification).filter(
                ScanBrandClassification.scan_id == scan_id,
                ScanBrandClassification.brand_id == g.id,
            ).first()
            if g_sbc is None:
                db.add(ScanBrandClassification(
                    scan_id=scan_id, brand_id=g.id, classification="my_brand",
                    is_focus=False, classified_by="brief", source="brief",
                ))
            elif g_sbc.classification == "unclassified":
                g_sbc.classification = "my_brand"
                g_sbc.classified_by = "brief"
                g_sbc.source = "brief"

    db.commit()
    logger.info(
        f"Gate 2 pre-populated from brief: "
        f"{competitors_created} competitors + {gammes_created} product lines "
        f"+ {own_gammes_created} own product lines"
    )

    return {
        "status": "completed",
        "provider": used_provider,
        "company": brief.get("company"),
        "competitors_created": competitors_created,
    }
