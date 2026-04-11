"""Handler: generate domain brief via OpenAI web search.

Produces a structured business-intelligence document about the scanned domain.
Stores in scan.config.domain_brief. Pre-populates Gate 2 with competitors from brief.
"""

import json
import logging
from datetime import datetime

import openai
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from models import Scan, ClientBrand, ScanBrandClassification

logger = logging.getLogger(__name__)

BRIEF_PROMPT = """Research the website {domain} using web search and provide structured business intelligence.

You MUST search the web to find accurate, up-to-date information about this website/company.

Return ONLY valid JSON (no markdown, no explanation) with this exact structure:
{{
  "company": "Full company name with parent group if applicable",
  "description": "2-3 sentence description of what the company does, what they sell, through which channels",
  "industry": "Industry / Sub-industry",
  "country": "Primary market country",
  "brands": ["Brand names owned by this company"],
  "product_lines": ["Product line name (purpose/category)" for each major product range],
  "services": ["Any services offered beyond products"],
  "competitors": [
    {{"name": "Competitor Name", "products": ["Their competing product lines"]}}
  ],
  "topics": ["Key themes/topics the website covers"],
  "target_audience": "Description of who their customers are, demographics, needs"
}}

Be thorough and specific. For competitors, list 5-10 direct competitors with their key product lines.
For product_lines, list the actual product range names, not generic categories.
"""


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
    logger.info(f"Generating domain brief for {domain} via OpenAI web search")

    scan.progress_message = f"Researching {domain}..."
    db.commit()

    # Call OpenAI Responses API with web_search
    api_key = settings.openai_api_key
    if not api_key:
        raise ValueError("OPENAI_API_KEY not configured")

    client = openai.OpenAI(api_key=api_key, timeout=60)
    prompt = BRIEF_PROMPT.format(domain=domain)

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            tools=[{"type": "web_search"}],
            input=prompt,
            temperature=0.3,
        )
        text = response.output_text or ""
    except Exception as e:
        logger.error(f"OpenAI web search failed for {domain}: {e}")
        raise

    # Parse JSON from response
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].strip()

    try:
        brief = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting JSON from response
        import re
        match = re.search(r'\{', text)
        if match:
            depth = 0
            for i in range(match.start(), len(text)):
                if text[i] == '{': depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            brief = json.loads(text[match.start():i + 1])
                        except json.JSONDecodeError:
                            brief = None
                        break
            else:
                brief = None
        else:
            brief = None

    if not brief:
        logger.error(f"Could not parse brief JSON from response ({len(text)} chars): {text[:500]}")
        raise ValueError(f"Could not parse domain brief from OpenAI response ({len(text)} chars)")

    logger.info(f"Brief generated for {domain}: {brief.get('company', '?')} — {brief.get('industry', '?')}")

    # Store in scan.config
    config = dict(scan.config or {})
    config["domain_brief"] = brief
    scan.config = config
    flag_modified(scan, "config")
    scan.updated_at = datetime.utcnow()
    db.commit()

    # Pre-populate Gate 2 with competitors from brief
    competitors_created = 0
    for comp in brief.get("competitors", []):
        comp_name = (comp.get("name") or "").strip()
        if not comp_name:
            continue

        # Find or create ClientBrand
        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            func.lower(ClientBrand.name) == comp_name.lower(),
        ).first()

        if not existing:
            brand = ClientBrand(
                client_id=scan.client_id,
                name=comp_name,
                canonical_name=comp_name,
                detected_in_scan_id=scan_id,
                auto_detected=True,
                validated_by_user=False,
                last_seen_at=datetime.utcnow(),
            )
            db.add(brand)
            db.flush()
        else:
            brand = existing
            existing.last_seen_at = datetime.utcnow()

        # Upsert ScanBrandClassification
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.brand_id == brand.id,
        ).first()
        if not sbc:
            db.add(ScanBrandClassification(
                scan_id=scan_id,
                brand_id=brand.id,
                classification="competitor",
                is_focus=False,
                classified_by="brief",
                source="brief",
            ))
            competitors_created += 1
        elif sbc.classification == "unclassified":
            sbc.classification = "competitor"
            sbc.classified_by = "brief"
            sbc.source = "brief"
            competitors_created += 1

    # Also pre-populate own brands
    for own_brand_name in brief.get("brands", []):
        own_brand_name = (own_brand_name or "").strip()
        if not own_brand_name:
            continue

        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            func.lower(ClientBrand.name) == own_brand_name.lower(),
        ).first()

        if not existing:
            brand = ClientBrand(
                client_id=scan.client_id,
                name=own_brand_name,
                canonical_name=own_brand_name,
                detected_in_scan_id=scan_id,
                auto_detected=True,
                validated_by_user=False,
                last_seen_at=datetime.utcnow(),
            )
            db.add(brand)
            db.flush()
        else:
            brand = existing

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

    db.commit()
    logger.info(f"Gate 2 pre-populated with {competitors_created} competitors from brief")

    return {
        "status": "completed",
        "company": brief.get("company"),
        "competitors_created": competitors_created,
    }
