import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from jose import jwt
import httpx

from config import settings
from models import Client, User, UserClient, get_db
from services.auth_service import get_current_user
from services.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _validate_password(password: str):
    """Enforce password complexity: min 8 chars, 1 uppercase, 1 lowercase, 1 digit."""
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if not any(c.isupper() for c in password):
        raise HTTPException(400, "Password must contain at least 1 uppercase letter")
    if not any(c.islower() for c in password):
        raise HTTPException(400, "Password must contain at least 1 lowercase letter")
    if not any(c.isdigit() for c in password):
        raise HTTPException(400, "Password must contain at least 1 digit")


@router.post("/register", response_model=TokenResponse)
@limiter.limit("5/minute")
async def register(request: Request, req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    _validate_password(req.password)
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(400, "Email already registered")

    user = User(
        email=req.email,
        name=req.name,
        password_hash=pwd_context.hash(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token(str(user.id), user.email)
    # Same HttpOnly cookie logic as /login — overwrites any stale session cookie.
    response.set_cookie(
        "token", token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return TokenResponse(access_token=token)


@router.post("/logout")
async def logout(response: Response):
    """Clear the HttpOnly token cookie server-side.

    A client-side `document.cookie = 'token=; max-age=0'` CANNOT delete an
    HttpOnly cookie, so without this endpoint users remain stuck on a stale
    session even after clicking "logout" or submitting a different login form.
    """
    response.delete_cookie("token", path="/")
    return {"ok": True}


@router.delete("/me")
async def delete_account(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """H7 / GDPR Art.17 — self-service account deletion.

    Hard-deletes the user. For each client the user has access to:
      * If the user is the SOLE member of that client → delete the client
        too. Migration 008 cascades the deletion through scans, brands,
        credits, sub-tables and jobs, so a single DELETE wipes everything.
      * If the client has other members → only the user_clients link is
        removed (cascade from user delete). Client and its data are
        preserved for the remaining members.

    Scans authored by this user on multi-member clients keep their rows but
    `created_by` becomes NULL (audit trail anonymized — see migration 008).

    The auth cookie is cleared in the response so the browser session ends
    immediately. The action is logged at WARNING level for compliance audit.
    """
    user_id = user.id
    user_email = user.email

    # Identify clients where this user is the sole member.
    # `having count(...) = 1` ensures only solo-owned clients are picked.
    sole_client_ids = (
        db.query(UserClient.client_id)
        .group_by(UserClient.client_id)
        .having(func.count(UserClient.user_id) == 1)
        .filter(
            UserClient.client_id.in_(
                db.query(UserClient.client_id).filter(UserClient.user_id == user_id)
            )
        )
        .all()
    )
    sole_client_ids = [row[0] for row in sole_client_ids]

    # Delete sole-owned clients first. Migration 008's CASCADE chain wipes
    # user_clients, scans (and all scan_* children), brands, credits,
    # api_keys, modules, subscriptions for each one.
    if sole_client_ids:
        db.query(Client).filter(Client.id.in_(sole_client_ids)).delete(
            synchronize_session=False
        )

    # Delete the user. user_clients rows referencing the user (on
    # multi-member clients) cascade away; scans.created_by becomes NULL.
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()

    logger.warning(
        f"GDPR account deletion: user={user_id} email={user_email} "
        f"sole_clients_dropped={len(sole_client_ids)}"
    )

    response.delete_cookie("token", path="/")
    return {
        "deleted": True,
        "sole_clients_dropped": len(sole_client_ids),
    }


@router.get("/me/export")
async def export_account_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """H8 / GDPR Art.15 (right of access) + Art.20 (data portability).

    Returns a structured JSON dump of every record tied to the requesting
    user. Scope per client they have access to: client metadata, the user's
    role on that client, scans + their full content (topics, personas,
    questions, LLM results, opportunities, brand classifications, content
    items), brands seen for that client, and the credit ledger.

    The returned shape is stable so a user can reliably re-import their
    data elsewhere (Art.20). For multi-tenant clients we expose the same
    payload regardless of which member is asking — every member of a
    client has equal access to that client's data already.
    """
    # Local imports keep cold-start cheap and avoid pulling these models
    # into other auth code paths.
    from models import (
        ClientCredit,
        ClientBrand,
        Scan,
        ScanBrandClassification,
        ScanBrandTopic,
        ScanContentItem,
        ScanKeyword,
        ScanLLMResult,
        ScanOpportunity,
        ScanPersona,
        ScanQuestion,
        ScanTopic,
    )

    def serialize_row(row, fields: list[str]) -> dict:
        out = {}
        for f in fields:
            v = getattr(row, f, None)
            if isinstance(v, datetime):
                out[f] = v.isoformat()
            elif hasattr(v, "hex"):  # UUID
                out[f] = str(v)
            else:
                out[f] = v
        return out

    # User profile (no password hash, no Google ID — those are credentials,
    # not portable data the user is entitled to under Art.20)
    user_payload = {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "auth_methods": {
            "password": user.password_hash is not None,
            "google_oauth": user.google_id is not None,
        },
    }

    # Clients the user has access to
    links = (
        db.query(UserClient).filter(UserClient.user_id == user.id).all()
    )

    clients_payload = []
    for link in links:
        client = db.query(Client).filter(Client.id == link.client_id).first()
        if not client:
            continue

        # Brands seen for this client
        brands = db.query(ClientBrand).filter(ClientBrand.client_id == client.id).all()

        # Credit ledger for this client
        credits = (
            db.query(ClientCredit)
            .filter(ClientCredit.client_id == client.id)
            .order_by(ClientCredit.created_at)
            .all()
        )

        # Scans for this client (with all nested children)
        scans_payload = []
        scans = db.query(Scan).filter(Scan.client_id == client.id).order_by(Scan.created_at).all()
        for scan in scans:
            scan_id = scan.id
            scans_payload.append({
                "id": str(scan_id),
                "name": scan.name,
                "domain": scan.domain,
                "status": scan.status,
                "run_index": scan.run_index,
                "parent_scan_id": str(scan.parent_scan_id) if scan.parent_scan_id else None,
                "config": scan.config,
                "summary": scan.summary,
                "created_at": scan.created_at.isoformat() if scan.created_at else None,
                "completed_at": scan.completed_at.isoformat() if scan.completed_at else None,
                "topics": [
                    serialize_row(t, ["id", "name", "description", "keyword_count"])
                    for t in db.query(ScanTopic).filter(ScanTopic.scan_id == scan_id).all()
                ],
                "personas": [
                    serialize_row(p, ["id", "name", "data", "is_active"])
                    for p in db.query(ScanPersona).filter(ScanPersona.scan_id == scan_id).all()
                ],
                "questions": [
                    serialize_row(q, ["id", "persona_id", "question", "type_question", "is_active"])
                    for q in db.query(ScanQuestion).filter(ScanQuestion.scan_id == scan_id).all()
                ],
                "keywords": [
                    serialize_row(k, ["id", "url", "keyword", "position", "traffic", "search_volume"])
                    for k in db.query(ScanKeyword).filter(ScanKeyword.scan_id == scan_id).all()
                ],
                "llm_results": [
                    serialize_row(r, [
                        "id", "question_id", "provider", "model", "response_text",
                        "citations", "target_cited", "target_position",
                        "brand_mentions", "brand_analysis", "created_at",
                    ])
                    for r in db.query(ScanLLMResult).filter(ScanLLMResult.scan_id == scan_id).all()
                ],
                "opportunities": [
                    serialize_row(o, [
                        "id", "question_id", "topic_name", "persona_name",
                        "brand_cited", "brand_position", "best_competitor_name",
                        "priority", "opportunity_score", "recommended_action", "target_url",
                    ])
                    for o in db.query(ScanOpportunity).filter(ScanOpportunity.scan_id == scan_id).all()
                ],
                "brand_classifications": [
                    serialize_row(b, ["id", "brand_id", "classification", "is_focus", "classified_by"])
                    for b in db.query(ScanBrandClassification).filter(ScanBrandClassification.scan_id == scan_id).all()
                ],
                "brand_topics": [
                    serialize_row(bt, ["id", "brand_id", "topic_id"])
                    for bt in db.query(ScanBrandTopic).filter(ScanBrandTopic.scan_id == scan_id).all()
                ],
                "content_items": [
                    serialize_row(ci, [
                        "id", "content_type", "topic_name", "persona_name",
                        "target_url", "target_question", "content_html",
                        "status", "created_at",
                    ])
                    for ci in db.query(ScanContentItem).filter(ScanContentItem.scan_id == scan_id).all()
                ],
            })

        clients_payload.append({
            "id": str(client.id),
            "name": client.name,
            "brand": client.brand,
            "user_role": link.role,
            "created_at": client.created_at.isoformat() if client.created_at else None,
            "brands": [
                serialize_row(b, [
                    "id", "name", "canonical_name", "category", "domain",
                    "first_detected_at", "detection_source", "validated_by_user",
                ])
                for b in brands
            ],
            "credit_ledger": [
                serialize_row(c, [
                    "id", "credit_type", "amount", "balance_after",
                    "description", "stripe_session_id", "scan_id", "created_at",
                ])
                for c in credits
            ],
            "scans": scans_payload,
        })

    return {
        "export_format": "sen-ai.fr GDPR data export v1",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user": user_payload,
        "clients": clients_payload,
    }


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.password_hash:
        raise HTTPException(401, "Invalid credentials")
    if not pwd_context.verify(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")

    token = create_token(str(user.id), user.email)

    # Set cookie server-side (HttpOnly) so it OVERWRITES any existing HttpOnly
    # token from a prior session (e.g. Google OAuth). Without this, the browser
    # keeps the old HttpOnly cookie and JS cannot overwrite it → user sees the
    # previous account. Matches the /google/callback cookie attributes.
    response.set_cookie(
        "token", token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return TokenResponse(access_token=token)


@router.get("/google")
async def google_login():
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/auth?{query}")


@router.get("/google/callback")
async def google_callback(code: str, response: Response, db: Session = Depends(get_db)):
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(400, "Google OAuth failed")
        tokens = token_resp.json()

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        userinfo = userinfo_resp.json()

    user = db.query(User).filter(User.google_id == userinfo["id"]).first()
    if not user:
        user = db.query(User).filter(User.email == userinfo["email"]).first()
        if user:
            user.google_id = userinfo["id"]
        else:
            user = User(
                email=userinfo["email"],
                name=userinfo.get("name", ""),
                google_id=userinfo["id"],
            )
            db.add(user)
        db.commit()
        db.refresh(user)

    token = create_token(str(user.id), user.email)
    resp = RedirectResponse("/dashboard")
    resp.set_cookie(
        "token", token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
    )
    return resp
