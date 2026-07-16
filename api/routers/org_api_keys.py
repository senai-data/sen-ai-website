"""BYOK - org-level LLM API keys (beta). Migration 060.

Organizations can bring their own OpenAI / Anthropic / Gemini / Mistral keys.
Keys are Fernet-encrypted with OAUTH_FERNET_KEY (services/token_manager.py,
same infra as oauth_connections) and validated with one free models-list ping
before being stored (services/llm_key_validator.py).

Auth model :
- GET endpoints : any org member (masked data only - members need key status
  for the cap/invalid banners).
- PUT / DELETE / POST validate : org owner/admin (_require_org_manager),
  rate-limited 5/minute (the validation ping is an external call - cf.
  feedback_cap_user_triggered_llm_ops).

Security invariants : the plaintext key is never logged, never echoed back,
and only key_hint ('sk-pr...abc4') is ever returned.

Runtime resolution lives worker-side in worker/services/byok.py : org key if
present -> platform pool otherwise. A stored key with status='invalid' or a
reached monthly cap BLOCKS jobs for that provider (no silent platform
fallback) until the key is re-validated, the cap raised, or the key deleted.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    Client, LlmUsageLog, Organization, OrganizationApiKey, OrganizationUser, get_db,
)
from routers.organizations import _require_org_manager
from services.auth_service import get_current_user
from services.llm_key_validator import BYOK_PROVIDERS, make_key_hint, validate_llm_key
from services.rate_limit import limiter
from services.token_manager import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)

router = APIRouter()


class OrgApiKeyUpsert(BaseModel):
    # api_key None/absent + monthly_cap_usd present = cap-only update
    # (users must not have to re-paste the secret to change a cap).
    api_key: str | None = Field(None, min_length=10, max_length=512)
    monthly_cap_usd: float | None = Field(None, ge=0.0, le=100000.0)


def _require_org_member(org_id: str, user, db: Session) -> Organization:
    """Any org member (any role). GETs return masked data only."""
    try:
        org_uuid = uuid.UUID(org_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Malformed organization_id")
    org = db.query(Organization).filter(Organization.id == org_uuid).first()
    if not org:
        raise HTTPException(404, "Organization not found")
    membership = (
        db.query(OrganizationUser)
        .filter(
            OrganizationUser.organization_id == org.id,
            OrganizationUser.user_id == user.id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(403, "You are not a member of this organization")
    return org


def _month_start_utc() -> datetime:
    now = datetime.utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _mtd_byok_cost_by_provider(db: Session, org_id) -> dict[str, float]:
    """Month-to-date BYOK spend per provider across the org's clients.

    Counts ONLY key_source='byok' rows : the cap means "don't spend more than
    $X/month on MY provider account", so platform-key spend never counts.
    """
    rows = (
        db.query(LlmUsageLog.provider, func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0))
        .join(Client, Client.id == LlmUsageLog.client_id)
        .filter(
            Client.organization_id == org_id,
            LlmUsageLog.key_source == "byok",
            LlmUsageLog.created_at >= _month_start_utc(),
        )
        .group_by(LlmUsageLog.provider)
        .all()
    )
    return {provider: float(total or 0.0) for provider, total in rows}


def _serialize_key(provider: str, row: OrganizationApiKey | None, mtd: dict[str, float]) -> dict:
    mtd_cost = round(mtd.get(provider, 0.0), 4)
    if row is None:
        return {
            "provider": provider,
            "configured": False,
            "key_hint": None,
            "status": None,
            "monthly_cap_usd": None,
            "mtd_cost_usd": mtd_cost,
            "cap_reached": False,
            "last_validated_at": None,
            "last_error": None,
            "created_at": None,
            "updated_at": None,
        }
    cap = float(row.monthly_cap_usd) if row.monthly_cap_usd is not None else None
    return {
        "provider": provider,
        "configured": True,
        "key_hint": row.key_hint,
        "status": row.status,
        "monthly_cap_usd": cap,
        "mtd_cost_usd": mtd_cost,
        "cap_reached": cap is not None and mtd_cost >= cap,
        "last_validated_at": row.last_validated_at.isoformat() if row.last_validated_at else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _maybe_grant_byok_bonus(db: Session, org: Organization, active_client_id: str | None) -> dict | None:
    """BYOK beta bonus : one-time 200 scan credits per org at first COMPLETE
    setup (active keys for openai + gemini + anthropic). Idempotent via
    organizations.byok_bonus_granted_at (row-locked against a double PUT).
    Credited to the active workspace when it belongs to the org, else the
    org's oldest client. Org without any client : silently retried on the
    next PUT/validate (pattern: welcome bonus, routers/clients.py).
    """
    from services.byok_preflight import is_byok_complete
    if org.byok_bonus_granted_at is not None:
        return None
    if not is_byok_complete(db, org.id):
        return None
    org_locked = (
        db.query(Organization)
        .filter(Organization.id == org.id)
        .with_for_update()
        .first()
    )
    if org_locked is None or org_locked.byok_bonus_granted_at is not None:
        return None
    target = None
    if active_client_id:
        target = (
            db.query(Client)
            .filter(Client.id == active_client_id, Client.organization_id == org.id)
            .first()
        )
    if target is None:
        target = (
            db.query(Client)
            .filter(Client.organization_id == org.id)
            .order_by(Client.created_at.asc())
            .first()
        )
    if target is None:
        return None
    from routers.stripe import add_credits
    add_credits(
        client_id=str(target.id),
        credit_type="scan",
        amount=200,
        description="BYOK beta bonus - 200 free scan credits",
        db=db,
    )
    org_locked.byok_bonus_granted_at = datetime.utcnow()
    db.commit()
    logger.info("BYOK bonus granted: org=%s client=%s (+200 scan credits)", org.id, target.id)
    return {"amount": 200, "credit_type": "scan", "client_id": str(target.id)}


def _get_key_row(db: Session, org_id, provider: str) -> OrganizationApiKey | None:
    return (
        db.query(OrganizationApiKey)
        .filter(
            OrganizationApiKey.organization_id == org_id,
            OrganizationApiKey.provider == provider,
        )
        .first()
    )


@router.get("/{org_id}/api-keys")
async def list_org_api_keys(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """All 4 providers, always (stable UI grid), masked. Any org member."""
    org = _require_org_member(org_id, user, db)
    rows = {
        r.provider: r
        for r in db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.organization_id == org.id)
        .all()
    }
    mtd = _mtd_byok_cost_by_provider(db, org.id)
    return {"providers": [_serialize_key(p, rows.get(p), mtd) for p in BYOK_PROVIDERS]}


@router.get("/{org_id}/api-keys/usage")
async def org_api_keys_usage(
    org_id: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Month-to-date BYOK spend per provider. Any org member (banner source)."""
    org = _require_org_member(org_id, user, db)
    rows = {
        r.provider: r
        for r in db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.organization_id == org.id)
        .all()
    }
    mtd = _mtd_byok_cost_by_provider(db, org.id)
    out = []
    for p in BYOK_PROVIDERS:
        item = _serialize_key(p, rows.get(p), mtd)
        out.append({
            "provider": p,
            "configured": item["configured"],
            "key_hint": item["key_hint"],
            "status": item["status"],
            "monthly_cap_usd": item["monthly_cap_usd"],
            "mtd_cost_usd": item["mtd_cost_usd"],
            "cap_reached": item["cap_reached"],
        })
    return {"month_start": _month_start_utc().isoformat(), "providers": out}


@router.put("/{org_id}/api-keys/{provider}")
@limiter.limit("5/minute")
async def upsert_org_api_key(
    org_id: str,
    provider: str,
    req: OrgApiKeyUpsert,
    request: Request,
    active_client_id: str | None = Cookie(default=None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Save/replace a key (validate-then-encrypt) or update the cap only."""
    org, _caller = _require_org_manager(org_id, user, db)
    if provider not in BYOK_PROVIDERS:
        raise HTTPException(400, f"Unsupported provider '{provider}' (expected one of: {', '.join(BYOK_PROVIDERS)})")

    row = _get_key_row(db, org.id, provider)
    cap_provided = "monthly_cap_usd" in req.model_fields_set

    if req.api_key is None:
        # Cap-only update path.
        if not cap_provided:
            raise HTTPException(400, "Provide api_key, monthly_cap_usd, or both")
        if row is None:
            raise HTTPException(404, f"No {provider} key configured for this organization")
        row.monthly_cap_usd = req.monthly_cap_usd
        row.updated_at = datetime.utcnow()
        db.commit()
    else:
        api_key = req.api_key.strip()
        ok, error_msg = await validate_llm_key(provider, api_key)
        if not ok:
            # Nothing stored on failure - the previous key (if any) stays as-is.
            raise HTTPException(400, {
                "error": "key_validation_failed",
                "provider": provider,
                "message": error_msg,
            })
        now = datetime.utcnow()
        if row is None:
            row = OrganizationApiKey(
                organization_id=org.id,
                provider=provider,
                created_by_user_id=user.id,
            )
            db.add(row)
        row.api_key_encrypted = encrypt_token(api_key)
        row.key_hint = make_key_hint(api_key)
        row.status = "active"
        row.last_validated_at = now
        row.last_error = None
        row.created_by_user_id = user.id
        row.updated_at = now
        if cap_provided:
            row.monthly_cap_usd = req.monthly_cap_usd
        db.commit()
        logger.info("BYOK key saved: org=%s provider=%s by user=%s", org.id, provider, user.id)

    # One-time beta bonus check (no-op unless the org just became complete).
    bonus = _maybe_grant_byok_bonus(db, org, active_client_id)

    db.refresh(row)
    mtd = _mtd_byok_cost_by_provider(db, org.id)
    out = _serialize_key(provider, row, mtd)
    if bonus:
        out["bonus_granted"] = bonus
    return out


@router.delete("/{org_id}/api-keys/{provider}", status_code=204)
async def delete_org_api_key(
    org_id: str,
    provider: str,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hard delete. Next jobs fall back to platform keys (the sanctioned way
    to go back to platform)."""
    org, _caller = _require_org_manager(org_id, user, db)
    if provider not in BYOK_PROVIDERS:
        raise HTTPException(400, f"Unsupported provider '{provider}'")
    row = _get_key_row(db, org.id, provider)
    if row is None:
        raise HTTPException(404, f"No {provider} key configured for this organization")
    db.delete(row)
    db.commit()
    logger.info("BYOK key deleted: org=%s provider=%s by user=%s", org.id, provider, user.id)
    return Response(status_code=204)


@router.post("/{org_id}/api-keys/{provider}/validate")
@limiter.limit("5/minute")
async def revalidate_org_api_key(
    org_id: str,
    provider: str,
    request: Request,
    active_client_id: str | None = Cookie(default=None),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-ping the stored key. Recovery path after a runtime invalidation
    (e.g. the user fixed billing on the provider side) without re-pasting."""
    org, _caller = _require_org_manager(org_id, user, db)
    if provider not in BYOK_PROVIDERS:
        raise HTTPException(400, f"Unsupported provider '{provider}'")
    row = _get_key_row(db, org.id, provider)
    if row is None:
        raise HTTPException(404, f"No {provider} key configured for this organization")

    api_key = decrypt_token(row.api_key_encrypted)
    ok, error_msg = await validate_llm_key(provider, api_key)
    now = datetime.utcnow()
    row.last_validated_at = now
    row.updated_at = now
    if ok:
        row.status = "active"
        row.last_error = None
    else:
        row.status = "invalid"
        row.last_error = error_msg
    db.commit()

    # Re-activation can complete the org's BYOK setup - bonus check here too.
    bonus = _maybe_grant_byok_bonus(db, org, active_client_id) if ok else None

    db.refresh(row)
    mtd = _mtd_byok_cost_by_provider(db, org.id)
    out = _serialize_key(provider, row, mtd)
    if bonus:
        out["bonus_granted"] = bonus
    return out
