"""Org invitation flow — Phase E.C.4.

5 endpoints :
  POST   /api/organizations/{org_id}/invitations    create + email
  GET    /api/organizations/{org_id}/invitations    list pending
  DELETE /api/organizations/{org_id}/invitations/{id}    revoke
  GET    /api/invitations/{token}/preview           unauth peek (accept page)
  POST   /api/invitations/{token}/accept            auth required, creates org_user

Mounted twice in main.py : once under `/api/organizations` (the
list/create/delete trio) and once under `/api/invitations` (the
token-scoped preview/accept). Keeping them in one module keeps the
shared `_send_invitation_email` helper local.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from models import (
    Client, Invitation, Organization, OrganizationUser, OrgUserClient,
    User, UserClient, get_db,
)
from services.auth_service import get_current_user
from services.rate_limit import limiter

logger = logging.getLogger(__name__)

org_scoped_router = APIRouter()
token_scoped_router = APIRouter()


# Default invitation lifetime — long enough that recipients don't miss it
# while away from email, short enough that a leaked link from a year ago
# can't still grant access.
_INVITATION_TTL_DAYS = 7
_INVITER_ROLES = {"owner", "admin"}
_ALLOWED_TARGET_ROLES = {"owner", "admin", "member"}


def _require_org_inviter(org_id: str, user, db: Session) -> OrganizationUser:
    """Return the membership row IFF the user can invite into this org.

    Only owners and admins can manage invitations ; members are read-only
    on the membership surface. Returns the membership row so the caller
    can decide on edge cases (e.g., admin cannot promote to owner).
    """
    try:
        org_uuid = uuid.UUID(org_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Malformed organization_id")

    if not db.query(Organization).filter(Organization.id == org_uuid).first():
        raise HTTPException(404, "Organization not found")

    membership = (
        db.query(OrganizationUser)
        .filter(
            OrganizationUser.organization_id == org_uuid,
            OrganizationUser.user_id == user.id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(403, "You are not a member of this organization")
    if membership.role not in _INVITER_ROLES:
        raise HTTPException(
            403,
            f"Only owners and admins can manage invitations (your role: '{membership.role}')",
        )
    return membership


def _grant_all_org_clients(db: Session, organization_id, user_id) -> int:
    """Give a newly-joined org owner/admin manager access to every current
    client workspace of the org. Returns the number of grants written.

    Rationale : the members-grid endpoints (PUT /members/{uid}/clients/{cid})
    let any org owner/admin set ANY member's workspace access, including
    their own - so a 'No access' default for an owner/admin is friction,
    not security (observed live : invited admin accepted, landed on an
    empty dashboard with their own row at 'No access' and concluded the
    accept had failed). Plain members stay no-access by design - they
    cannot self-grant, so the manual grant IS the security boundary.

    Mirrors the S15.3 create-client pattern : writes both OrgUserClient
    (modern) and UserClient (legacy fallback) rows. Idempotent - existing
    rows are left untouched (never downgrade a role someone already set).
    """
    granted = 0
    clients = db.query(Client).filter(Client.organization_id == organization_id).all()
    for client in clients:
        ouc = (
            db.query(OrgUserClient)
            .filter(
                OrgUserClient.organization_id == organization_id,
                OrgUserClient.user_id == user_id,
                OrgUserClient.client_id == client.id,
            )
            .first()
        )
        if not ouc:
            db.add(OrgUserClient(
                organization_id=organization_id,
                user_id=user_id,
                client_id=client.id,
                role="manager",
            ))
            granted += 1
        legacy = (
            db.query(UserClient)
            .filter(UserClient.user_id == user_id, UserClient.client_id == client.id)
            .first()
        )
        if not legacy:
            db.add(UserClient(user_id=user_id, client_id=client.id, role="manager"))
    return granted


def _send_invitation_email(
    *, to_email: str, org_name: str, inviter_name: str | None,
    accept_url: str, message: str | None,
) -> bool:
    """Send the invitation email via Resend. Falls back to log if not
    configured (so local dev still works)."""
    if not settings.resend_api_key:
        logger.warning(
            f"RESEND_API_KEY not set — invitation URL for {to_email}: {accept_url}"
        )
        return False
    try:
        import resend
        resend.api_key = settings.resend_api_key
        inviter_line = (
            f"<p><strong>{inviter_name}</strong> invited you to join "
            f"<strong>{org_name}</strong> on sen-ai.fr.</p>"
            if inviter_name else
            f"<p>You've been invited to join <strong>{org_name}</strong> on sen-ai.fr.</p>"
        )
        message_block = (
            f'<blockquote style="margin:16px 0;padding:12px 16px;'
            f'border-left:3px solid #E8604C;color:#555;font-style:italic;">'
            f'{message}</blockquote>'
            if (message or "").strip() else ""
        )
        resend.Emails.send({
            "from": settings.resend_from_email,
            "to": [to_email],
            "subject": f"You've been invited to {org_name} on sen-ai.fr",
            "html": (
                f"{inviter_line}"
                f"{message_block}"
                f'<p><a href="{accept_url}" style="display:inline-block;padding:12px 24px;'
                f'background:#E8604C;color:white;border-radius:8px;text-decoration:none;'
                f'font-weight:bold;">Accept invitation</a></p>'
                f"<p>This link expires in {_INVITATION_TTL_DAYS} days. "
                f"If you weren't expecting this, you can safely ignore the email — "
                f"no account will be created unless you click Accept.</p>"
                f"<p>— sen-ai.fr</p>"
            ),
        })
        return True
    except Exception:
        logger.exception(f"Failed to send invitation email to {to_email}")
        return False


# ───────── Org-scoped routes : create / list / revoke ─────────

class CreateInvitationRequest(BaseModel):
    email: EmailStr
    org_role: str = "member"
    message: str | None = None


class InvitationResponse(BaseModel):
    id: str
    email: str
    org_role: str
    invited_by_email: str | None
    created_at: str
    expires_at: str
    accepted_at: str | None
    revoked_at: str | None
    is_pending: bool


@org_scoped_router.post("/{org_id}/invitations", response_model=InvitationResponse)
@limiter.limit("20/hour")
async def create_invitation(
    request: Request, org_id: str, body: CreateInvitationRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Create + email a new invitation. Owner/admin only.

    Admins cannot mint owner-role invites (privilege escalation) — only
    owners can promote to owner. Member is the safe default.
    """
    inviter = _require_org_inviter(org_id, user, db)

    if body.org_role not in _ALLOWED_TARGET_ROLES:
        raise HTTPException(
            400, f"org_role must be one of {sorted(_ALLOWED_TARGET_ROLES)}",
        )
    if body.org_role == "owner" and inviter.role != "owner":
        raise HTTPException(
            403, "Only an existing owner can invite another owner",
        )

    email_normalized = body.email.lower().strip()

    # Idempotency : if a pending invite already exists for this (org, email)
    # we return it unchanged rather than spawning a parallel token. Saves the
    # admin from sending two emails and the recipient from token confusion.
    now = datetime.utcnow()
    existing = (
        db.query(Invitation)
        .filter(
            Invitation.organization_id == uuid.UUID(org_id),
            Invitation.email == email_normalized,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .first()
    )
    if existing:
        return _serialize_invitation(existing, db)

    invite = Invitation(
        organization_id=uuid.UUID(org_id),
        email=email_normalized,
        org_role=body.org_role,
        token=secrets.token_urlsafe(32),
        invited_by_user_id=user.id,
        message=(body.message or "").strip() or None,
        expires_at=now + timedelta(days=_INVITATION_TTL_DAYS),
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)

    org = db.query(Organization).filter(Organization.id == invite.organization_id).first()
    accept_url = f"{settings.frontend_url}/invite/{invite.token}"
    _send_invitation_email(
        to_email=invite.email,
        org_name=(org.name if org else "your team"),
        inviter_name=(user.name or user.email),
        accept_url=accept_url,
        message=invite.message,
    )

    return _serialize_invitation(invite, db)


@org_scoped_router.get("/{org_id}/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    org_id: str,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """List pending + recent invitations for an org. Owner/admin only.

    Includes accepted/revoked rows from the last 30 days for audit context —
    keeps the page useful without bloating the response.
    """
    _require_org_inviter(org_id, user, db)
    cutoff = datetime.utcnow() - timedelta(days=30)
    rows = (
        db.query(Invitation)
        .filter(
            Invitation.organization_id == uuid.UUID(org_id),
            Invitation.created_at >= cutoff,
        )
        .order_by(Invitation.created_at.desc())
        .all()
    )
    return [_serialize_invitation(r, db) for r in rows]


@org_scoped_router.delete("/{org_id}/invitations/{invitation_id}")
async def revoke_invitation(
    org_id: str, invitation_id: str,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Mark an invitation as revoked. Idempotent : revoking an already-
    revoked invite is a no-op 200."""
    _require_org_inviter(org_id, user, db)
    try:
        invite_uuid = uuid.UUID(invitation_id)
    except (ValueError, TypeError):
        raise HTTPException(400, "Malformed invitation_id")

    invite = (
        db.query(Invitation)
        .filter(
            Invitation.id == invite_uuid,
            Invitation.organization_id == uuid.UUID(org_id),
        )
        .first()
    )
    if not invite:
        raise HTTPException(404, "Invitation not found")
    if invite.accepted_at:
        raise HTTPException(409, "Already accepted — revoke has no effect")
    if not invite.revoked_at:
        invite.revoked_at = datetime.utcnow()
        db.commit()
    return {"ok": True, "revoked_at": invite.revoked_at.isoformat()}


# ───────── Token-scoped routes : received / preview / accept ─────────

class ReceivedInvitation(BaseModel):
    token: str
    organization_id: str
    organization_name: str
    org_role: str
    inviter_name: str | None
    message: str | None
    expires_at: str


@token_scoped_router.get("/received", response_model=list[ReceivedInvitation])
async def list_received_invitations(
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Pending invitations addressed to the signed-in user's email.

    Powers the dashboard "You've been invited to X" banner - before this,
    the ONLY surface for a received invite was the email link itself, so an
    invitee browsing the app had no clue an invite was waiting (the members
    page lists invitations SENT by the active org, not received ones).

    Returns the token because the banner links to /invite/{token} - safe :
    the caller is authenticated AND is the invite's addressee, i.e. the
    same person who already holds the token in their inbox.

    Excludes orgs the user already belongs to (accept would be a member-
    level no-op) plus anything expired / revoked / accepted.

    Registered before the /{invite_token}/* routes on purpose - no GET
    /{invite_token} exists today so there is no ambiguity, but keeping the
    literal path first makes that invariant obvious.
    """
    member_org_ids = {
        row.organization_id
        for row in db.query(OrganizationUser.organization_id)
        .filter(OrganizationUser.user_id == user.id)
        .all()
    }
    now = datetime.utcnow()
    invites = (
        db.query(Invitation)
        .filter(
            func.lower(Invitation.email) == (user.email or "").lower(),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
        .all()
    )
    out: list[ReceivedInvitation] = []
    for inv in invites:
        if inv.organization_id in member_org_ids:
            continue
        org = db.query(Organization).filter(Organization.id == inv.organization_id).first()
        inviter = (
            db.query(User).filter(User.id == inv.invited_by_user_id).first()
            if inv.invited_by_user_id else None
        )
        out.append(ReceivedInvitation(
            token=inv.token,
            organization_id=str(inv.organization_id),
            organization_name=(org.name if org else "Unknown organization"),
            org_role=inv.org_role,
            inviter_name=(inviter.name or inviter.email) if inviter else None,
            message=inv.message,
            expires_at=inv.expires_at.isoformat(),
        ))
    return out


class InvitationPreview(BaseModel):
    organization_name: str
    organization_id: str
    org_role: str
    email: str
    inviter_name: str | None
    expires_at: str
    is_valid: bool
    invalid_reason: str | None  # 'expired' | 'revoked' | 'accepted' | None
    # Phase E.C.4.1 — the accept page uses this to render either a "Sign
    # in" CTA (existing account) or an inline "Create account" form
    # (invite-bootstrap registration that bypasses registration_open).
    email_has_account: bool


@token_scoped_router.get("/{invite_token}/preview", response_model=InvitationPreview)
async def preview_invitation(invite_token: str, db: Session = Depends(get_db)):
    """Unauthenticated peek at an invitation — used by the /invite/[token]
    page to decide whether to show 'Sign in to accept' or an error state.

    Returns 404 only for truly unknown tokens. Expired / revoked / accepted
    tokens still return a 200 with `is_valid=false` so the UI can show a
    helpful message instead of a generic 'not found'.

    Path param is `invite_token` (not `token`) because the auth cookie is
    also named `token` — FastAPI's dependency analyzer can't disambiguate
    a path param against a Cookie param with the same name inside a
    sub-dependency. Renaming locally is the cheapest fix.
    """
    invite = db.query(Invitation).filter(Invitation.token == invite_token).first()
    if not invite:
        raise HTTPException(404, "Invitation not found")

    org = db.query(Organization).filter(Organization.id == invite.organization_id).first()
    inviter = (
        db.query(User).filter(User.id == invite.invited_by_user_id).first()
        if invite.invited_by_user_id else None
    )

    invalid_reason: str | None = None
    if invite.revoked_at:
        invalid_reason = "revoked"
    elif invite.accepted_at:
        invalid_reason = "accepted"
    elif invite.expires_at <= datetime.utcnow():
        invalid_reason = "expired"

    # Emails stored on User are case-insensitive in practice (lower()'d at
    # signup) ; do a case-insensitive compare so a mixed-case invite still
    # matches a lower-cased account row.
    email_has_account = db.query(User).filter(
        func.lower(User.email) == invite.email.lower()
    ).first() is not None

    return InvitationPreview(
        organization_name=(org.name if org else "Unknown organization"),
        organization_id=str(invite.organization_id),
        org_role=invite.org_role,
        email=invite.email,
        inviter_name=(inviter.name or inviter.email) if inviter else None,
        expires_at=invite.expires_at.isoformat(),
        is_valid=invalid_reason is None,
        invalid_reason=invalid_reason,
        email_has_account=email_has_account,
    )


class AcceptResponse(BaseModel):
    ok: bool
    organization_id: str
    org_role: str
    already_member: bool


class AcceptAndRegisterRequest(BaseModel):
    name: str
    password: str


class AcceptAndRegisterResponse(BaseModel):
    ok: bool
    organization_id: str
    org_role: str
    access_token: str


@token_scoped_router.post("/{invite_token}/accept", response_model=AcceptResponse)
@limiter.limit("30/hour")
async def accept_invitation(
    request: Request, invite_token: str, response: Response,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """Accept an invitation. Requires the user to be authenticated.

    Edge cases handled :
      - Already a member → 200 with already_member=true, marks invite
        accepted anyway (so the admin sees it in the list).
      - Email mismatch (user signed in with email A, invite was for B) → 403.
        The accept page should hint this clearly before sending the POST.
      - Expired / revoked → 409 with the reason.

    Side effect : sets the active_organization_id cookie to the newly-
    joined org so the next page load lands in the right context.
    """
    invite = db.query(Invitation).filter(Invitation.token == invite_token).first()
    if not invite:
        raise HTTPException(404, "Invitation not found")
    if invite.revoked_at:
        raise HTTPException(409, "This invitation has been revoked")
    if invite.expires_at <= datetime.utcnow() and not invite.accepted_at:
        raise HTTPException(409, "This invitation has expired")
    if invite.email.lower() != (user.email or "").lower():
        raise HTTPException(
            403,
            f"This invitation is for {invite.email}, but you're signed in as {user.email}. "
            f"Sign in with the invited account.",
        )

    # Idempotent : already a member ?
    existing_membership = (
        db.query(OrganizationUser)
        .filter(
            OrganizationUser.organization_id == invite.organization_id,
            OrganizationUser.user_id == user.id,
        )
        .first()
    )
    already_member = existing_membership is not None
    if not already_member:
        db.add(OrganizationUser(
            organization_id=invite.organization_id,
            user_id=user.id,
            role=invite.org_role,
            invited_by_user_id=invite.invited_by_user_id,
        ))
        if invite.org_role in ("owner", "admin"):
            _grant_all_org_clients(db, invite.organization_id, user.id)

    if not invite.accepted_at:
        invite.accepted_at = datetime.utcnow()
        invite.accepted_by_user_id = user.id

    db.commit()

    # UX nicety : land the user inside the org they just joined.
    response.set_cookie(
        "active_organization_id", str(invite.organization_id),
        httponly=True, secure=True, samesite="lax",
        max_age=60 * 60 * 24 * 180, path="/",
    )
    response.delete_cookie("active_client_id", path="/")

    return AcceptResponse(
        ok=True,
        organization_id=str(invite.organization_id),
        org_role=invite.org_role,
        already_member=already_member,
    )


# Phase E.C.4.1 — bootstrap registration via invitation. The invitation
# token is itself the authorization to create an account, so this path
# bypasses the global `registration_open` kill-switch. Without this, an
# invitee with no existing sen-ai.fr account hits the "registrations
# temporarily closed" wall and can't complete the flow.
@token_scoped_router.post("/{invite_token}/accept-and-register", response_model=AcceptAndRegisterResponse)
@limiter.limit("10/hour")
async def accept_and_register(
    request: Request, invite_token: str, body: AcceptAndRegisterRequest,
    response: Response, db: Session = Depends(get_db),
):
    """Create a brand-new user account from an invite + accept atomically.

    Skipped on purpose : email verification. The invite link was sent to
    the email we register, which is itself proof of email ownership — a
    second verification step would be redundant and add friction.

    Edge cases :
      - User with this email already exists → 409 ; the invitee should
        sign in instead via the normal /api/auth/login flow then POST
        /accept. UI hints at this via preview.email_has_account.
      - Invalid / expired / revoked invite → 404 / 409, same as /accept.
    """
    # Local imports to avoid circular dependency on auth helpers from the
    # invitations router boot path.
    from routers.auth import _validate_password, create_token
    from passlib.context import CryptContext

    invite = db.query(Invitation).filter(Invitation.token == invite_token).first()
    if not invite:
        raise HTTPException(404, "Invitation not found")
    if invite.revoked_at:
        raise HTTPException(409, "This invitation has been revoked")
    if invite.accepted_at:
        raise HTTPException(409, "This invitation has already been accepted")
    if invite.expires_at <= datetime.utcnow():
        raise HTTPException(409, "This invitation has expired")

    email_normalized = invite.email.lower().strip()
    if db.query(User).filter(func.lower(User.email) == email_normalized).first():
        raise HTTPException(
            409,
            "An account already exists for this email. Sign in instead, then click Accept.",
        )

    _validate_password(body.password)
    name_clean = (body.name or "").strip()[:120]
    if not name_clean:
        raise HTTPException(400, "Name is required")

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    user = User(
        email=email_normalized,
        name=name_clean,
        password_hash=pwd_context.hash(body.password),
        is_email_verified=True,   # invite link == email ownership proof
    )
    db.add(user)
    db.flush()

    db.add(OrganizationUser(
        organization_id=invite.organization_id,
        user_id=user.id,
        role=invite.org_role,
        invited_by_user_id=invite.invited_by_user_id,
    ))
    if invite.org_role in ("owner", "admin"):
        _grant_all_org_clients(db, invite.organization_id, user.id)

    invite.accepted_at = datetime.utcnow()
    invite.accepted_by_user_id = user.id
    db.commit()
    db.refresh(user)

    # Sign the new user in : same HttpOnly token cookie + active_org cookie
    # pattern as the authed accept handler. Avoids a forced extra round-trip
    # through /login.
    auth_token = create_token(str(user.id), user.email)
    response.set_cookie(
        "token", auth_token,
        httponly=True, secure=True, samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        "active_organization_id", str(invite.organization_id),
        httponly=True, secure=True, samesite="lax",
        max_age=60 * 60 * 24 * 180, path="/",
    )
    response.delete_cookie("active_client_id", path="/")

    return AcceptAndRegisterResponse(
        ok=True,
        organization_id=str(invite.organization_id),
        org_role=invite.org_role,
        access_token=auth_token,
    )


# ───────── helpers ─────────

def _serialize_invitation(inv: Invitation, db: Session) -> InvitationResponse:
    inviter = (
        db.query(User).filter(User.id == inv.invited_by_user_id).first()
        if inv.invited_by_user_id else None
    )
    is_pending = (
        inv.accepted_at is None
        and inv.revoked_at is None
        and inv.expires_at > datetime.utcnow()
    )
    return InvitationResponse(
        id=str(inv.id),
        email=inv.email,
        org_role=inv.org_role,
        invited_by_email=(inviter.email if inviter else None),
        created_at=inv.created_at.isoformat() if inv.created_at else "",
        expires_at=inv.expires_at.isoformat(),
        accepted_at=inv.accepted_at.isoformat() if inv.accepted_at else None,
        revoked_at=inv.revoked_at.isoformat() if inv.revoked_at else None,
        is_pending=is_pending,
    )
