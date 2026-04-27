"""OAuth provider abstraction — Google (full), Microsoft & Notion (stubs).

Each provider knows how to build an authorize URL, exchange a code for
tokens, refresh, revoke, and fetch the identity of the user who consented.
"""

from abc import ABC, abstractmethod
from urllib.parse import urlencode

import httpx

from config import settings

# ── Scope catalog ──────────────────────────────────────────────────────
# Maps a product key to the OAuth scopes that product requires.

PRODUCT_SCOPES: dict[str, list[str]] = {
    # Google
    "google_ads": ["https://www.googleapis.com/auth/adwords"],
    "ga4": ["https://www.googleapis.com/auth/analytics.readonly"],
    "gbp": ["https://www.googleapis.com/auth/business.manage"],
    "sheets": ["https://www.googleapis.com/auth/spreadsheets"],
    "drive": ["https://www.googleapis.com/auth/drive.file"],
    "search_console": ["https://www.googleapis.com/auth/webmasters.readonly"],
    # Microsoft (stub)
    "sharepoint": ["Files.Read", "Sites.Read.All", "offline_access"],
    "onedrive": ["Files.Read", "offline_access"],
    # Notion (stub)
    "notion": [],
}

# Maps a product to its parent provider.
PRODUCT_PROVIDER: dict[str, str] = {
    "google_ads": "google",
    "ga4": "google",
    "gbp": "google",
    "sheets": "google",
    "drive": "google",
    "search_console": "google",
    "sharepoint": "microsoft",
    "onedrive": "microsoft",
    "notion": "notion",
}

# Valid products per provider (for request validation).
PROVIDER_PRODUCTS: dict[str, list[str]] = {
    "google": ["google_ads", "ga4", "gbp", "sheets", "drive", "search_console"],
    "microsoft": ["sharepoint", "onedrive"],
    "notion": ["notion"],
}


# ── Abstract provider ─────────────────────────────────────────────────

class OAuthProvider(ABC):
    name: str = ""

    @abstractmethod
    def authorize_url(self, state: str, scopes: list[str]) -> str:
        """Return the full redirect URL to the provider's consent screen."""

    @abstractmethod
    async def exchange_code(self, code: str) -> dict:
        """Exchange authorization code → {access_token, refresh_token, expires_in, ...}."""

    @abstractmethod
    async def refresh_access_token(self, refresh_token: str) -> dict:
        """Refresh → {access_token, expires_in, ...}. May include new refresh_token."""

    @abstractmethod
    async def revoke(self, token: str) -> bool:
        """Best-effort revocation at the provider. Return True on success."""

    @abstractmethod
    async def fetch_user_info(self, access_token: str) -> dict:
        """Return {account_id, account_email, account_name} of the consenting user."""


# ── Google ─────────────────────────────────────────────────────────────

class GoogleProvider(OAuthProvider):
    name = "google"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    REVOKE_URL = "https://oauth2.googleapis.com/revoke"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

    def authorize_url(self, state: str, scopes: list[str]) -> str:
        all_scopes = list(set(scopes + ["openid", "email", "profile"]))
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": settings.oauth_google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(all_scopes),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "include_granted_scopes": "true",
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.oauth_google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def refresh_access_token(self, refresh_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke(self, token: str) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.REVOKE_URL,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            return resp.status_code == 200

    async def fetch_user_info(self, access_token: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "account_id": data.get("id"),
                "account_email": data.get("email"),
                "account_name": data.get("name"),
            }


# ── Microsoft (stub) ──────────────────────────────────────────────────

class MicrosoftProvider(OAuthProvider):
    """To be implemented when SharePoint/OneDrive integration ships."""
    name = "microsoft"

    def authorize_url(self, state: str, scopes: list[str]) -> str:
        raise NotImplementedError("Microsoft OAuth not yet implemented")

    async def exchange_code(self, code: str) -> dict:
        raise NotImplementedError

    async def refresh_access_token(self, refresh_token: str) -> dict:
        raise NotImplementedError

    async def revoke(self, token: str) -> bool:
        raise NotImplementedError

    async def fetch_user_info(self, access_token: str) -> dict:
        raise NotImplementedError


# ── Notion (stub) ─────────────────────────────────────────────────────

class NotionProvider(OAuthProvider):
    """To be implemented when Notion integration ships."""
    name = "notion"

    def authorize_url(self, state: str, scopes: list[str]) -> str:
        raise NotImplementedError("Notion OAuth not yet implemented")

    async def exchange_code(self, code: str) -> dict:
        raise NotImplementedError

    async def refresh_access_token(self, refresh_token: str) -> dict:
        raise NotImplementedError

    async def revoke(self, token: str) -> bool:
        raise NotImplementedError

    async def fetch_user_info(self, access_token: str) -> dict:
        raise NotImplementedError


# ── Registry ──────────────────────────────────────────────────────────

PROVIDERS: dict[str, OAuthProvider] = {
    "google": GoogleProvider(),
    "microsoft": MicrosoftProvider(),
    "notion": NotionProvider(),
}


def get_provider(name: str) -> OAuthProvider:
    """Look up a provider by name. Raises ValueError for unknowns."""
    provider = PROVIDERS.get(name)
    if not provider:
        raise ValueError(f"Unknown OAuth provider: {name}")
    return provider
