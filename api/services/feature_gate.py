"""Feature gating — reusable FastAPI dependency.

Usage in a router:

    from services.feature_gate import require_app

    @router.get("/campaigns")
    async def list_campaigns(
        client=Depends(require_app("google_ads")),
        ...
    ):
        # client is the Client row, guaranteed to have google_ads enabled
        ...

Returns the Client object so downstream code can read client.id, client.apps, etc.
"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from models import Client, User, UserClient, get_db
from services.auth_service import get_current_user


def require_app(app_key: str):
    """Factory that returns a FastAPI dependency checking the app is enabled."""

    async def _check(
        client_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Client:
        # Verify user has access to client
        link = (
            db.query(UserClient)
            .filter(UserClient.user_id == user.id, UserClient.client_id == client_id)
            .first()
        )
        if not link:
            raise HTTPException(403, "Access denied")

        client = db.query(Client).filter(Client.id == client_id).first()
        if not client:
            raise HTTPException(404, "Client not found")

        # Check feature flag
        apps = client.apps or {}
        if not apps.get(app_key, {}).get("enabled"):
            raise HTTPException(
                403,
                f"The '{app_key}' module is not enabled for this workspace. "
                f"Contact your administrator.",
            )

        return client

    return _check
