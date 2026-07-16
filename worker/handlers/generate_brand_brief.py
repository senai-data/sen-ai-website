"""Handler: generate per-brand brief stored on client_brands.brief JSONB.

Where the workspace ``client_brief`` describes the company as a whole, the
**brand brief** describes ONE specific primary brand within that company —
its identity, voice, audience, competitors, signature features, regulatory
posture. Surcharges the workspace brief per-field downstream via
``worker/adapters/brief_injector`` 2-level merge (brand wins, workspace
fills gaps).

Reads :
- client_brands row (FK: brand_id)
- client.apps['client_brief'] for industry / country context

Writes :
- client_brands.brief = BrandBrief JSON (validated via Pydantic)
- client_brands.brief_generated_at = utcnow
- client_brands.brief_generations_count += 1

Provider strategy mirrors generate_client_brief.py : OpenAI Responses +
web_search primary (best fact recovery on niche brands), Gemini + grounding
fallback, Claude (training-only) last resort.

Cap : MAX_BRAND_BRIEF_GENERATIONS = 3 per brand. Edited briefs
(``brief.edited_by_user == True``) are skipped — user must explicitly clear
the flag before regen. See feedback_cap_user_triggered_llm_ops.md.

Phase BB. See project_phase_brand_briefs.md for the full sync map.
"""

import json
import logging
import re
from datetime import datetime

import httpx
import openai
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from models import Client, ClientBrand, Job
from schemas import BrandBrief

logger = logging.getLogger(__name__)


# Per-brand cap on generate_brand_brief reruns. Suit pattern from scans.py
# MAX_PERSONA_QUESTIONS_GENERATIONS. UI surfaces the counter + cap.
MAX_BRAND_BRIEF_GENERATIONS = 3


BRAND_BRIEF_PROMPT = """You are producing a per-brand brief for a content-marketing platform. The brief surcharges the workspace's company-level brief on a per-brand basis — so the article generated for THIS brand reflects ITS voice, audience and competitors, distinct from sister brands in the same group.

Brand: {brand_name}
{brand_domain_block}
Workspace context:
{workspace_context}
{known_competitors_block}

Use web search to verify and enrich. Return ONLY valid JSON (no markdown, no commentary) with this exact structure :

{{
  "name": "{brand_name}",
  "parent_group": "Parent company / holding group name, or empty string when standalone",
  "description": "2-3 sentences describing the brand — what it makes, who it serves, what makes it distinctive",
  "founded_year": "Year as integer (e.g., 1736), or null if unknown",
  "headquarters": "City, Country — empty string when unknown",
  "languages": ["ISO-639 codes OR plain names for the markets this brand serves — e.g., fr, en, es, de, jp"],

  "heritage": "1-2 sentences on the brand's founding event / terroir / founder myth. Anchors content intros and conclusions. Different from founded_year — this is the *story*.",
  "brand_story": "3-5 sentences on what the brand has come to stand for, its evolution, its myth. Used by editorial blocks when the topic supports a 'brand chapter'.",

  "positioning_statement": "1 sentence on this brand's market positioning (NOT the parent group)",
  "taglines": ["Brand marketing slogans or hooks — verbatim if you know them, omit otherwise"],
  "differentiators": ["3-6 concrete things that set this brand apart from peers"],
  "price_tier": "Free-form tier description that fits the vertical. Examples by vertical : cosmetics → 'mass / premium / luxury / pharmacy-exclusive' ; finance → 'retail / institutional' ; B2B SaaS → 'SMB / mid-market / enterprise'. Empty string when unclear.",
  "distribution": ["Channels — e.g., 'pharmacy', 'e-commerce', 'department stores', 'direct B2B sales', 'dealer network'"],

  "editorial_voice": "1 sentence on tone for content written FOR this brand (e.g., 'expert, reassuring, science-led — never salesy or alarmist'). This is the high-value field — be specific.",
  "tonality": ["3-6 adjectives that ground the voice — e.g., 'expert', 'warm', 'evidence-driven'"],
  "tone_dos": ["6-12 verbs / postures / vocabulary that signal this brand's voice. E.g. pharma-grade dermo : 'soulager', 'apaiser', 'protéger', 'cliniquement prouvé', 'recommandé par les dermatologues'. Be verbatim — these go into copywriter guidance."],
  "tone_donts": ["6-12 forbidden vocabulary / framings. E.g. 'miracle', 'instantané', direct competitor comparison, 'guérit', 'lifestyle/glamour', hyperbole. Be specific to this brand's category and positioning."],
  "claims_guidelines": ["3-8 marketing-claim rules combining regulatory + brand voice. E.g. 'Never claim cures or treats without medical authorisation', 'Always disclose allergen list inline', 'Cite study reference when health claim is made', 'Do not promise transformation in <X days'."],

  "target_audience": "1-2 sentences on the brand's audience (age, demographic, need, mindset) — be specific to THIS brand, not its parent company's broader audience",
  "audience_segments": ["3-6 distinct audience segments served by the brand"],

  "product_lines": ["Named product ranges / gammes / sub-brands owned by this brand"],
  "hero_products": ["3-8 flagship SKUs that define the brand"],
  "signature_features": ["Vertical-neutral name — ingredients in cosmetics, components in automotive, capabilities in B2B SaaS, instruments in finance, etc. List the 3-8 most signature ones."],

  "direct_competitors": [
    {{ "name": "Competitor brand name", "products": ["Their hero products"], "domain": "competitor official domain or empty string" }}
  ],
  "indirect_competitors": ["2-6 adjacent brands that compete on share-of-wallet but not head-to-head"],

  "expertise_topics": ["6-12 topics where this brand wants to be authoritative — used to bias content-generation toward its territory"],
  "regulatory_constraints": ["Compliance / regulatory frameworks that apply to this brand IN ITS HOME MARKET — infer the jurisdiction from workspace country + industry. Examples : 'EU Cosmetic Regulation 1223/2009', 'FDA OTC monograph', 'AMF MIFID II', 'GDPR'. Empty list when no specific regulation applies."]
}}

Rules :
- Output JSON only. No markdown fences, no commentary.
- This brand is the {brand_name} brand SPECIFICALLY, not its parent group. If parent has 6 brands, your job is to differentiate THIS one from the other 5.
- Stay concise — this brief is read by other LLMs, not humans. Every word costs input tokens downstream.
- For unknown fields, return "" or [] — never invent.
- **direct_competitors MUST be BRANDS at the same level as {brand_name}, never parent companies.** If you're tempted to list "L'Oréal", instead list their relevant brand (La Roche-Posay, CeraVe, Vichy…). If the user provided a "Known competitors" list above, treat those as priority candidates and add web-verified ones on top.
- tone_dos / tone_donts / claims_guidelines : these are what a copywriter pins on the wall. Be concrete, verbatim, category-aware. Avoid generic platitudes.
- regulatory_constraints : infer the relevant frameworks from workspace industry + country. Do not list generic disclaimers; only frameworks that actually shape what the brand can claim or sell.
"""


CLAUDE_FALLBACK_PROMPT = """You are producing a per-brand brief based on training knowledge (no web access).

Brand: {brand_name}
{brand_domain_block}
Workspace context:
{workspace_context}
{known_competitors_block}

If you don't recognise this specific brand, infer conservatively from its name + workspace industry. Note uncertainty in the description rather than fabricating facts. Empty string / empty list is always acceptable.

Return ONLY valid JSON with the same structure as the web-search prompt :

{{
  "name": "{brand_name}",
  "parent_group": "...",
  "description": "...",
  "founded_year": null,
  "headquarters": "",
  "languages": [],
  "heritage": "",
  "brand_story": "",
  "positioning_statement": "...",
  "taglines": [],
  "differentiators": [],
  "price_tier": "",
  "distribution": [],
  "editorial_voice": "...",
  "tonality": [],
  "tone_dos": [],
  "tone_donts": [],
  "claims_guidelines": [],
  "target_audience": "...",
  "audience_segments": [],
  "product_lines": [],
  "hero_products": [],
  "signature_features": [],
  "direct_competitors": [],
  "indirect_competitors": [],
  "expertise_topics": [],
  "regulatory_constraints": []
}}

Rules : direct_competitors must be BRANDS, not parent companies. If a "Known competitors" list is provided above, prioritize those.

Output JSON only — no markdown.
"""


def _extract_json(text: str) -> dict | None:
    """Recover a JSON object from a possibly markdown-wrapped LLM response."""
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
    return None


def _resolve_known_competitors(brand: ClientBrand, db: Session) -> list[str]:
    """Resolve the brand-level competitors the user has already classified on this client.

    Reads ScanBrandClassification rows across every scan owned by the client,
    filters to classification='competitor', dedups by ClientBrand row, and
    excludes :
      - The brand itself
      - The brand's children (gammes — they're not competitors at the brand
        level)
      - Any brand that's currently a primary on the client (caught a couple
        of edge cases where a brand was both primary AND historically tagged
        competitor on an old scan)

    Returns a list of canonical brand names sorted by detection frequency
    (most-tagged first), capped at 12 to keep the prompt tight.
    """
    from collections import Counter
    from models import ScanBrandClassification, Scan
    from sqlalchemy import select

    client = db.query(Client).filter(Client.id == brand.client_id).first()
    primary_ids = {str(b) for b in (client.primary_brand_ids if client else []) or []}

    # All scans on this client
    scan_ids = [s.id for s in db.query(Scan).filter(Scan.client_id == brand.client_id).all()]
    if not scan_ids:
        return []

    # SBC rows classified competitor across those scans
    rows = (
        db.query(ScanBrandClassification.brand_id)
        .filter(
            ScanBrandClassification.scan_id.in_(scan_ids),
            ScanBrandClassification.classification == "competitor",
        )
        .all()
    )
    counter = Counter(str(r.brand_id) for r in rows if r.brand_id)
    if not counter:
        return []

    # Hydrate ClientBrand for the top N candidates (cap at 30 candidates to
    # bound the secondary lookup), exclude self / children / primaries.
    top_ids = [bid for bid, _ in counter.most_common(30)]
    child_ids = {
        str(c.id) for c in db.query(ClientBrand.id).filter(
            ClientBrand.parent_id == brand.id
        ).all()
    }
    candidates = (
        db.query(ClientBrand)
        .filter(ClientBrand.id.in_(top_ids))
        .all()
    )
    by_id = {str(c.id): c for c in candidates}

    out: list[str] = []
    for bid, _ in counter.most_common():
        if bid == str(brand.id) or bid in child_ids or bid in primary_ids:
            continue
        row = by_id.get(bid)
        if not row or not row.name:
            continue
        # Skip child brands (gammes) — they're not competitors at brand level.
        if row.parent_id is not None:
            continue
        out.append(row.name)
        if len(out) >= 12:
            break
    return out


def _format_known_competitors_block(names: list[str]) -> str:
    """Build the 'Known competitors' context block injected into the prompt.

    Empty string when no competitors are known — the prompt skips the block
    gracefully. When present, this anchors the LLM on the user's curated
    Competitors taxonomy (Gate 2) so direct_competitors comes back at the
    correct level (brand, not parent company).
    """
    if not names:
        return ""
    bullet = ", ".join(names)
    return (
        f"\nKnown competitors (already curated by the user in their workspace, "
        f"USE AS PRIORITY in direct_competitors — verify with web search and "
        f"rank by directness of competition):\n  {bullet}\n"
    )


def _format_workspace_context(client: Client) -> str:
    """One-block string describing the workspace's industry + country for prompt context.

    Pulled from ``client.apps['client_brief']``. Falls back to a single line
    when the workspace brief hasn't been generated yet — the prompt still
    works, just with less grounding.
    """
    apps = client.apps or {}
    brief = apps.get("client_brief") or {}
    industry = (brief.get("industry") or "").strip()
    country = (brief.get("country") or "").strip()
    overview = (brief.get("company_overview") or "").strip()

    lines = [f"Workspace: {client.name}"]
    if industry:
        lines.append(f"Industry: {industry}")
    if country:
        lines.append(f"Country: {country}")
    if overview:
        # Cap overview to keep token budget bounded — LLMs are noisy on this.
        lines.append(f"Company overview: {overview[:400]}")
    return "\n".join(lines)


def _try_openai(brand_name: str, brand_domain_block: str, workspace_context: str,
                known_competitors_block: str,
                api_key: str, model: str) -> tuple[dict | None, str, dict]:
    client = openai.OpenAI(api_key=api_key, timeout=60)
    prompt = BRAND_BRIEF_PROMPT.format(
        brand_name=brand_name,
        brand_domain_block=brand_domain_block,
        workspace_context=workspace_context,
        known_competitors_block=known_competitors_block,
    )
    # NW.2 - inject anti-AI-detection humanizer block (compact mode).
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    response = client.responses.create(
        model=model, tools=[{"type": "web_search"}],
        input=prompt, temperature=0.3,
    )
    text = response.output_text or ""
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0) or 0,
        "output_tokens": getattr(usage_obj, "output_tokens", 0) or 0,
    }
    return _extract_json(text), text, usage


def _try_gemini(brand_name: str, brand_domain_block: str, workspace_context: str,
                known_competitors_block: str,
                api_key: str, model: str) -> tuple[dict | None, str, dict]:
    # Via the factory, NOT LLMClient directly : it guarantees api_key is the
    # key actually used (the submodule's internal rotator would silently
    # override it with platform env keys - BYOK fix 2026-07-16).
    from adapters.llm_scanner import create_llm_client
    llm = create_llm_client("gemini", api_key, model=model)
    prompt = BRAND_BRIEF_PROMPT.format(
        brand_name=brand_name,
        brand_domain_block=brand_domain_block,
        workspace_context=workspace_context,
        known_competitors_block=known_competitors_block,
    )
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    response = llm.generate(
        prompt, temperature=0.3, max_tokens=8000, use_grounding=True,
        agent_name="generate_brand_brief_gemini",
    )
    text = response.get("text", "")
    usage_raw = response.get("usage", {}) or {}
    usage = {
        "input_tokens": usage_raw.get("prompt_tokens", 0) or usage_raw.get("input_tokens", 0),
        "output_tokens": usage_raw.get("completion_tokens", 0) or usage_raw.get("output_tokens", 0),
    }
    return _extract_json(text), text, usage


def _try_claude(brand_name: str, brand_domain_block: str, workspace_context: str,
                known_competitors_block: str,
                api_key: str, model: str) -> tuple[dict | None, str, dict]:
    prompt = CLAUDE_FALLBACK_PROMPT.format(
        brand_name=brand_name,
        brand_domain_block=brand_domain_block,
        workspace_context=workspace_context,
        known_competitors_block=known_competitors_block,
    )
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")
    payload = {
        "model": model, "max_tokens": 4096, "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    with httpx.Client(timeout=60) as http:
        resp = http.post("https://api.anthropic.com/v1/messages",
                         json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    text = data.get("content", [{}])[0].get("text", "")
    usage = data.get("usage", {})
    return _extract_json(text), text, {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    """Generate a per-brand brief. ``scan_id`` is unused (brand-scoped)."""
    brand_id = job_payload.get("brand_id")
    if not brand_id:
        raise ValueError("generate_brand_brief requires brand_id in job payload")

    brand = db.query(ClientBrand).filter(ClientBrand.id == brand_id).first()
    if not brand:
        raise ValueError(f"ClientBrand {brand_id} not found")

    client = db.query(Client).filter(Client.id == brand.client_id).first()
    if not client:
        raise ValueError(f"Client {brand.client_id} not found for brand {brand_id}")

    # Refuse to overwrite if user has manually edited the brief — they should
    # explicitly clear edited_by_user via PATCH before regen.
    existing = brand.brief or {}
    if existing.get("edited_by_user"):
        logger.info(f"Brand brief for {brand.name} ({brand_id}) edited by user — skipping regen")
        return {"status": "skipped", "reason": "user_edited", "brand_id": str(brand_id)}

    # Hard cap : MAX_BRAND_BRIEF_GENERATIONS reruns per brand. Successful runs
    # increment the counter; failures don't (they raise before the persist
    # block), so a wedged provider chain doesn't burn the user's budget.
    used = int(brand.brief_generations_count or 0)
    if used >= MAX_BRAND_BRIEF_GENERATIONS:
        logger.info(
            f"Brand brief for {brand.name} ({brand_id}) hit generation cap "
            f"({used}/{MAX_BRAND_BRIEF_GENERATIONS}) — skipping"
        )
        return {
            "status": "skipped", "reason": "cap_reached",
            "brand_id": str(brand_id),
            "generations_used": used, "cap": MAX_BRAND_BRIEF_GENERATIONS,
        }

    # Cap-then-call : single-brand brief runs ~$0.02-0.04 with OpenAI
    # web_search + small Claude fallback. Project $0.05 for safety.
    from services.llm_budget import assert_within_budget
    assert_within_budget(str(brand.client_id), db, projected_cost_usd=0.05)

    brand_domain_block = (
        f"Brand official domain: {brand.domain}" if brand.domain else
        "Brand official domain: (unknown — infer from web search)"
    )
    workspace_context = _format_workspace_context(client)

    # BB.8 : pre-seed the prompt with the brand-level competitors the user
    # already validated in Gate 2 / brand setup. Anchors direct_competitors
    # at the BRAND level (Bioderma, La Roche-Posay) instead of letting the
    # LLM drift to GROUP level (L'Oréal, Sanofi).
    known_competitors = _resolve_known_competitors(brand, db)
    known_competitors_block = _format_known_competitors_block(known_competitors)

    logger.info(
        f"Generating brand brief for {brand.name} (client={client.name}, "
        f"brand_id={brand_id}, attempt #{used + 1}/{MAX_BRAND_BRIEF_GENERATIONS}, "
        f"known_competitors={len(known_competitors)})"
    )

    parsed_brief = None
    used_provider = None
    raw_texts: dict[str, str] = {}

    # BYOK : resolve the 3 tiers once (raises before any spend when an org
    # key is configured but invalid/capped - no silent platform fallback).
    from services.byok import resolve_anthropic_key, resolve_openai_key, resolve_org_key
    openai_key, openai_src = resolve_openai_key(db, brand.client_id)
    gemini_org = resolve_org_key(db, brand.client_id, "gemini")
    anthropic_key, anthropic_src = resolve_anthropic_key(db, brand.client_id)

    # ── Tier 1: OpenAI + web_search ─────────────────────────────────────
    if openai_key:
        primary_model = settings.task_models.get("generate_brand_brief", "gpt-4.1-mini")
        try:
            parsed, raw, usage = _try_openai(
                brand.name, brand_domain_block, workspace_context,
                known_competitors_block,
                openai_key, primary_model,
            )
            raw_texts["openai"] = raw
            if parsed:
                parsed_brief = parsed
                used_provider = "openai"
                from adapters.llm_logger import log_llm_usage
                log_llm_usage(
                    db, provider="openai", model=primary_model,
                    operation="generate_brand_brief",
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    client_id=str(brand.client_id),
                    key_source=openai_src,
                )
            else:
                logger.warning(
                    f"OpenAI returned malformed JSON for brand {brand_id} "
                    f"({len(raw)} chars). Start: {raw[:200]}"
                )
        except Exception as e:
            logger.warning(f"OpenAI brand brief failed for {brand_id}: {e}")

    # ── Tier 2: Gemini with grounding ───────────────────────────────────
    from services.gemini_key_pool import get_gemini_pool
    gemini_pool = get_gemini_pool()
    if parsed_brief is None and (gemini_org is not None or gemini_pool.has_keys()):
        gemini_model = settings.task_models.get(
            "generate_brand_brief_gemini", "gemini-3.5-flash",
        )
        logger.warning(
            f"Falling back to Gemini ({gemini_model}) for brand brief {brand_id}"
        )
        gemini_key = gemini_org.api_key if gemini_org is not None else gemini_pool.next_key()
        gemini_src = "byok" if gemini_org is not None else "platform"
        try:
            parsed, raw, usage = _try_gemini(
                brand.name, brand_domain_block, workspace_context,
                known_competitors_block,
                gemini_key, gemini_model,
            )
            raw_texts["gemini"] = raw
            if parsed:
                parsed_brief = parsed
                used_provider = "gemini"
                from adapters.llm_logger import log_llm_usage
                log_llm_usage(
                    db, provider="gemini", model=gemini_model,
                    operation="generate_brand_brief_gemini",
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    client_id=str(brand.client_id),
                    key_source=gemini_src,
                )
        except Exception as e:
            err = str(e)
            if gemini_org is None and ("429" in err or "rate" in err.lower() or "RESOURCE_EXHAUSTED" in err):
                gemini_pool.mark_rate_limited(gemini_key)
            logger.warning(f"Gemini brand brief failed for {brand_id}: {e}")

    # ── Tier 3: Claude (training only) ──────────────────────────────────
    if parsed_brief is None and anthropic_key:
        claude_model = settings.task_models.get(
            "generate_brand_brief_claude", "claude-sonnet-4-6",
        )
        logger.warning(
            f"Falling back to Claude ({claude_model}) for brand brief {brand_id}"
        )
        try:
            parsed, raw, usage = _try_claude(
                brand.name, brand_domain_block, workspace_context,
                known_competitors_block,
                anthropic_key, claude_model,
            )
            raw_texts["claude"] = raw
            if parsed:
                parsed_brief = parsed
                used_provider = "claude"
                from adapters.llm_logger import log_llm_usage
                log_llm_usage(
                    db, provider="anthropic", model=claude_model,
                    operation="generate_brand_brief_claude",
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    client_id=str(brand.client_id),
                    key_source=anthropic_src,
                )
        except Exception as e:
            logger.warning(f"Claude brand brief failed for {brand_id}: {e}")

    if parsed_brief is None:
        sizes = {p: len(t) for p, t in raw_texts.items()}
        raise RuntimeError(
            f"Brand brief generation failed across all 3 providers for brand "
            f"{brand.name} ({brand_id}). Response sizes: {sizes}"
        )

    # Some LLMs drop the name field even though we re-state it in the prompt —
    # paste it back from the row to keep the Pydantic min_length=1 happy.
    parsed_brief.setdefault("name", brand.name)
    if not parsed_brief.get("name"):
        parsed_brief["name"] = brand.name

    # ── Pydantic validation (warn-then-store-best-effort) ───────────────
    try:
        validated = BrandBrief.model_validate(parsed_brief)
        brief_dict = validated.model_dump()
    except Exception as e:
        # If a provider returned a shape that doesn't validate, fail the job
        # so the caller can re-enqueue with a different provider mix. Don't
        # silently store a broken brief — downstream merge would propagate it.
        logger.exception(
            f"BrandBrief Pydantic validation failed for brand {brand_id} "
            f"(provider={used_provider}): {e}"
        )
        raise RuntimeError(
            f"BrandBrief validation failed for brand {brand_id}: {e}"
        ) from e

    # ── Persist on client_brands ────────────────────────────────────────
    brief_dict["generated_via"] = used_provider
    brief_dict["generated_at"] = datetime.utcnow().isoformat() + "Z"
    brief_dict["edited_by_user"] = False

    brand.brief = brief_dict
    brand.brief_generated_at = datetime.utcnow()
    brand.brief_generations_count = used + 1
    flag_modified(brand, "brief")
    db.commit()

    logger.info(
        f"Brand brief saved for {brand.name} via {used_provider} "
        f"({len(brief_dict.get('direct_competitors', []))} direct competitors, "
        f"{len(brief_dict.get('product_lines', []))} product lines, "
        f"{len(brief_dict.get('expertise_topics', []))} topics)"
    )

    return {
        "status": "ok", "provider": used_provider,
        "brand_id": str(brand_id),
        "generations_used": used + 1, "cap": MAX_BRAND_BRIEF_GENERATIONS,
        "competitors": len(brief_dict.get("direct_competitors", [])),
        "product_lines": len(brief_dict.get("product_lines", [])),
    }
