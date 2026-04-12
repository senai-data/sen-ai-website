"""M5: Superadmin-only routes for platform operations.

Foundation for the admin UI (`/app/admin/`) and Phase 0 OAuth feature
gating. Every route in this router requires `is_superadmin = true` on the
authenticated user — see the `require_superadmin` dependency below.

Initial scope (intentionally minimal):
  * GET  /api/admin/clients   — list every client + member counts + scan counts
  * GET  /api/admin/users     — list every user + their client links

These two are enough for support cases ("which client does this user
belong to?", "how many scans on Pierre Fabre's workspace last month?")
and they unblock the upcoming `/app/admin/` Astro pages without locking
us into a particular admin schema.

When Phase 0 OAuth lands, this router will grow:
  * PATCH /api/admin/clients/{id}/apps    — toggle app feature flags
  * GET   /api/admin/clients/{id}/oauth   — list OAuth connections
  * POST  /api/admin/clients/{id}/oauth   — create OAuth connection
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Client, OAuthConnection, Scan, User, UserClient, get_db
from services.auth_service import get_current_user

router = APIRouter()


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    """Dependency that 403s any non-superadmin user.

    Cheap (no extra DB hit — `User` is already loaded by `get_current_user`).
    """
    if not user.is_superadmin:
        raise HTTPException(403, "Superadmin access required")
    return user


@router.get("/clients")
async def admin_list_clients(
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """List every client on the platform with summary stats.

    Returns: id, name, brand, created_at, member_count, scan_count.
    Sorted by created_at desc (newest first).
    """
    rows = (
        db.query(
            Client.id,
            Client.name,
            Client.brand,
            Client.created_at,
            func.count(func.distinct(UserClient.user_id)).label("member_count"),
            func.count(func.distinct(Scan.id)).label("scan_count"),
        )
        .outerjoin(UserClient, UserClient.client_id == Client.id)
        .outerjoin(Scan, Scan.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.created_at.desc())
        .all()
    )
    # Fetch apps for each client (not available in the aggregated query)
    client_ids = [r.id for r in rows]
    clients_map = {}
    if client_ids:
        clients_full = db.query(Client).filter(Client.id.in_(client_ids)).all()
        clients_map = {c.id: c for c in clients_full}

    return [
        {
            "id": str(r.id),
            "name": r.name,
            "brand": r.brand,
            "apps": clients_map[r.id].apps if r.id in clients_map else {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "member_count": r.member_count,
            "scan_count": r.scan_count,
        }
        for r in rows
    ]


@router.get("/users")
async def admin_list_users(
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """List every user on the platform with their client memberships.

    Returns: id, email, name, is_superadmin, auth methods, created_at,
    list of (client_id, client_name, role) for each membership.
    """
    users = db.query(User).order_by(User.created_at.desc()).all()
    out = []
    for u in users:
        links = (
            db.query(UserClient, Client)
            .join(Client, Client.id == UserClient.client_id)
            .filter(UserClient.user_id == u.id)
            .all()
        )
        out.append({
            "id": str(u.id),
            "email": u.email,
            "name": u.name,
            "is_superadmin": bool(u.is_superadmin),
            "auth": {
                "password": u.password_hash is not None,
                "google_oauth": u.google_id is not None,
            },
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "memberships": [
                {
                    "client_id": str(c.id),
                    "client_name": c.name,
                    "role": link.role,
                }
                for link, c in links
            ],
        })
    return out


VALID_APP_KEYS = {"ai_scan", "google_ads", "local_business"}


class AppToggleRequest(BaseModel):
    app_key: str          # e.g. "google_ads"
    enabled: bool = True
    config: dict | None = None  # optional app-specific config


@router.patch("/clients/{client_id}/apps")
async def admin_toggle_app(
    client_id: str,
    req: AppToggleRequest,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """Toggle an app module on/off for a client.

    Sets or removes an app key in the client's `apps` JSONB column.
    When enabled=false, the key is removed entirely (clean sidebar).
    """
    if req.app_key not in VALID_APP_KEYS:
        raise HTTPException(400, f"Unknown app: {req.app_key}. Valid: {sorted(VALID_APP_KEYS)}")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(404, "Client not found")

    apps = dict(client.apps or {})
    if req.enabled:
        app_entry = {"enabled": True}
        if req.config:
            app_entry.update(req.config)
        apps[req.app_key] = app_entry
    else:
        apps.pop(req.app_key, None)

    client.apps = apps
    # Force SQLAlchemy to detect JSONB mutation
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(client, "apps")
    db.commit()

    return {"client_id": str(client.id), "apps": client.apps}


@router.get("/clients/{client_id}/connections")
async def admin_client_connections(
    client_id: str,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    """List OAuth connections for a specific client (superadmin)."""
    rows = (
        db.query(OAuthConnection)
        .filter(OAuthConnection.client_id == client_id)
        .order_by(OAuthConnection.created_at.desc())
        .all()
    )
    return [
        {
            "id": str(c.id),
            "provider": c.provider,
            "product": c.product,
            "account_email": c.account_email,
            "status": c.status,
            "authorized_at": c.authorized_at.isoformat() if c.authorized_at else None,
        }
        for c in rows
    ]


@router.get("/me")
async def admin_whoami(user: User = Depends(require_superadmin)):
    """Confirm the caller is a superadmin. Useful for the admin UI to
    decide whether to render the /app/admin/ navigation entry."""
    return {
        "id": str(user.id),
        "email": user.email,
        "is_superadmin": True,
    }
