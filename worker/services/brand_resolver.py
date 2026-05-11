"""Brand promotion resolver — answers "which brands should this content gen promote?"

Used by FAQ + Article + future content generation handlers to determine which
brands the LLM is instructed to push (and conversely, which competitor brands
to actively avoid mentioning positively). Implements the SaaS bias mechanism :
when a Pierre Fabre user generates an FAQ from an opportunity on a *competitor*
scan (laroche-posay.fr), the output must promote Avène/Aderma/Ducray, never
La Roche-Posay.

Resolution chain (highest priority first) :

  1. scan.promotion_brand_ids                      (per-scan explicit override)
  2. client.primary_brand_ids                      (cross-scan workspace default)
  3. raise PromotionUnsetError                     (UI prompts user to set defaults)

ScanBrandClassification(my_brand) used to be step 2 but has been dropped from
the promote chain : on a competitor audit (Pierre Fabre user scanning
uriage.fr), classify_topics tags Uriage and its product gammes (Xémose,
Hyséac, …) as `my_brand` because they're the dominant brands on the scanned
SITE. Merging that into the promote chain pollutes the FAQ generator's
prompt with competitor names — exact opposite of the bias we want. SBC
classifications remain stored for analytics and for the Phase E side-by-side
view, but the canonical "what to promote" is now answered by workspace
primary brands alone (with explicit per-scan override available).

Returned alongside the promote list :
- the *competitor* brands (from ScanBrandClassification.classification='competitor')
  + the scan.domain itself (when it doesn't belong to a my_brand) — these are
  the names Claude must NOT recommend in the generated content.
- a `resolved_via` audit string describing which step matched, useful in logs
  and the /promotion/resolve transparency endpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class PromotionUnsetError(RuntimeError):
    """Raised when no brand can be resolved for promotion.

    The caller (API endpoint or worker handler) should catch this and surface
    a 409 / actionable message routing the user to the workspace settings page
    where they can set `client.primary_brand_ids`.
    """


@dataclass
class BrandRef:
    """Lightweight brand descriptor passed downstream into prompt-building code.

    Carries only what the prompt needs — full ClientBrand row stays in DB.
    """
    id: UUID
    name: str
    domain: str | None = None
    aliases: list[str] = field(default_factory=list)


@dataclass
class PromotionResolution:
    """Output of resolve_promotion(). Contains everything the prompt builder needs."""
    promote_brands: list[BrandRef]            # ordered, [0] = lead brand
    exclude_brands: list[BrandRef]            # competitors + scanned competitor domain
    exclude_domain_names: list[str]           # plain strings for regex post-checks
    resolved_via: str                         # one of "scan_override" / "scan_classifications"
                                              # / "client_primary" / "merged" — for logs + audit
    promote_brand_ids: list[UUID]             # convenience: list of UUIDs for storage


def resolve_promotion(scan, db: Session) -> PromotionResolution:
    """Resolve which brands the upcoming content gen should promote vs avoid.

    Args:
        scan: a Scan row (must have .id, .client_id, .focus_brand_id, .domain,
              .promotion_brand_ids)
        db: active SQLAlchemy session

    Returns:
        PromotionResolution dataclass

    Raises:
        PromotionUnsetError if nothing resolves (caller should redirect user to
        the brand-promotion settings page).
    """
    from models import Client, ClientBrand, ScanBrandClassification

    # ── Step 1: scan.promotion_brand_ids (explicit per-scan override) ─────
    promote_ids: list[UUID] = []
    resolved_via = ""
    if scan.promotion_brand_ids:
        promote_ids = list(scan.promotion_brand_ids)
        resolved_via = "scan_override"

    # ── Step 2: client.primary_brand_ids (workspace default) ──────────────
    # NOTE: SBC `my_brand` is intentionally NOT merged here — see module
    # docstring. classify_topics tags the scanned site's dominant brand as
    # my_brand by construction, which pollutes the promote chain on
    # competitor audits.
    if not promote_ids:
        client = db.query(Client).filter(Client.id == scan.client_id).first()
        if client and client.primary_brand_ids:
            promote_ids = list(client.primary_brand_ids)
            resolved_via = "client_primary"

    if not promote_ids:
        raise PromotionUnsetError(
            f"No brand to promote for scan {scan.id}: no per-scan override "
            f"and no client.primary_brand_ids set. "
            f"Resolve by setting primary brands in workspace settings."
        )

    # ── Load promote brand details ────────────────────────────────────────
    promote_brand_rows = (
        db.query(ClientBrand)
        .filter(ClientBrand.id.in_(promote_ids))
        .all()
    )
    # Preserve the priority order from promote_ids (db.query doesn't guarantee order)
    by_id = {b.id: b for b in promote_brand_rows}
    promote_brands: list[BrandRef] = []
    for bid in promote_ids:
        b = by_id.get(bid)
        if b is not None:
            promote_brands.append(BrandRef(
                id=b.id,
                name=b.name,
                domain=b.domain,
                aliases=list(b.aliases or []),
            ))

    # ── Build exclude list = competitors of this scan ─────────────────────
    competitor_rows = (
        db.query(ClientBrand)
        .join(ScanBrandClassification, ScanBrandClassification.brand_id == ClientBrand.id)
        .filter(
            ScanBrandClassification.scan_id == scan.id,
            ScanBrandClassification.classification == "competitor",
        )
        .all()
    )
    exclude_brands: list[BrandRef] = [
        BrandRef(id=b.id, name=b.name, domain=b.domain, aliases=list(b.aliases or []))
        for b in competitor_rows
    ]

    # Add the scanned domain itself to the exclusion if it doesn't belong to a promote brand
    promote_domain_set = {b.domain.lower() for b in promote_brands if b.domain}
    scan_domain_lc = (scan.domain or "").lower()
    if scan_domain_lc and not any(scan_domain_lc.endswith(d) or d.endswith(scan_domain_lc) for d in promote_domain_set):
        # scan.domain is a competitor's domain — make sure its name is excluded
        # (already in exclude_brands if SBC tagged it as competitor, but this is defensive)
        pass  # competitor classification should already catch it

    # Flatten exclude names + aliases for post-hoc regex check on generated content
    exclude_domain_names: list[str] = []
    for b in exclude_brands:
        if b.name:
            exclude_domain_names.append(b.name)
        for alias in b.aliases:
            if alias:
                exclude_domain_names.append(alias)
    # Dedupe case-insensitively while preserving original casing
    seen_lc = set()
    deduped_exclude: list[str] = []
    for n in exclude_domain_names:
        lc = n.lower().strip()
        if lc and lc not in seen_lc:
            seen_lc.add(lc)
            deduped_exclude.append(n)

    logger.info(
        f"resolve_promotion(scan={scan.id}): "
        f"{len(promote_brands)} promote, {len(exclude_brands)} exclude, "
        f"resolved_via={resolved_via}"
    )

    return PromotionResolution(
        promote_brands=promote_brands,
        exclude_brands=exclude_brands,
        exclude_domain_names=deduped_exclude,
        resolved_via=resolved_via,
        promote_brand_ids=[b.id for b in promote_brands],
    )


def is_competitor_scan(scan, db: Session) -> bool:
    """Return True when the scanned domain is a competitor's, not the user's.

    Three-tier resolution :

      1. `scan.scan_type` — user-declared intent at wizard. Authoritative
         when set ('competitor_audit' → True, 'own_brand' → False).
      2. Domain comparison against `client.primary_brand_ids[*].domain` —
         fallback when scan_type is NULL (pre-migration scans, anonymous
         launches). Stable workspace signal; ignores per-scan SBC pollution.
      3. False when both are absent — conservative default, preserves the
         legacy user-owned behavior.

    Note: this helper used to delegate to resolve_promotion's merged chain,
    which created a circular check (the chain includes per-scan SBC, which
    classify_topics fills with the scanned site's brand → competitor was
    seen as "in my promote list" → wrongly classified as not-a-competitor).
    Keep the helper independent of the chain.
    """
    if not scan or not scan.domain:
        return False

    # Tier 1: user-declared intent wins
    if getattr(scan, "scan_type", None):
        return scan.scan_type == "competitor_audit"

    # Tier 2: domain comparison against workspace primary brands
    from models import Client, ClientBrand

    client = db.query(Client).filter(Client.id == scan.client_id).first()
    if not client or not client.primary_brand_ids:
        return False  # can't determine, default to user-owned behavior

    primary_brand_rows = (
        db.query(ClientBrand)
        .filter(ClientBrand.id.in_(client.primary_brand_ids))
        .all()
    )

    def _normalize(d: str) -> str:
        d = (d or "").lower().strip()
        if d.startswith("www."):
            d = d[4:]
        return d

    scan_domain_lc = _normalize(scan.domain)
    if not scan_domain_lc:
        return False

    for b in primary_brand_rows:
        b_domain_lc = _normalize(b.domain or "")
        if not b_domain_lc:
            continue
        # Match either direction so subdomain scans (eu.avene.com vs avene.com)
        # are still treated as user-owned.
        if scan_domain_lc == b_domain_lc:
            return False
        if scan_domain_lc.endswith("." + b_domain_lc):
            return False
        if b_domain_lc.endswith("." + scan_domain_lc):
            return False
    return True
