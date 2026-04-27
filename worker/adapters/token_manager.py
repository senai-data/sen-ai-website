"""Worker-side token manager — decrypt/encrypt OAuth tokens + auto-refresh.

Mirrors api/services/token_manager.py but uses worker config.
Handles transparent token refresh for sync jobs so the worker doesn't
fail on expired tokens.
"""

import logging
from datetime import datetime, timedelta

import httpx
from cryptography.fernet import Fernet

from config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.oauth_fernet_key
        if not key:
            raise RuntimeError(
                "OAUTH_FERNET_KEY is not set in worker .env. "
                "The worker needs this to decrypt OAuth tokens for sync jobs."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def decrypt_token(encrypted: str) -> str:
    """Decrypt a Fernet ciphertext → plaintext token string."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


def encrypt_token(plain: str) -> str:
    """Encrypt a plaintext token string → Fernet ciphertext."""
    return _get_fernet().encrypt(plain.encode()).decode()


def get_valid_access_token(connection, db) -> str:
    """Return a usable access_token, refreshing transparently if expired.

    Synchronous version for the worker (no async). Checks token_expires_at
    and refreshes via Google's token endpoint if needed.
    """
    if connection.status != "active":
        raise ValueError(f"Connection {connection.id} is {connection.status}")
    if not connection.access_token_encrypted:
        raise ValueError(f"Connection {connection.id} has no access token")

    needs_refresh = (
        connection.token_expires_at is not None
        and connection.token_expires_at <= datetime.utcnow() + timedelta(minutes=5)
    )

    if needs_refresh and connection.refresh_token_encrypted:
        refresh_token = decrypt_token(connection.refresh_token_encrypted)
        try:
            resp = httpx.post(
                GOOGLE_TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "grant_type": "refresh_token",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            new_tokens = resp.json()
            connection.access_token_encrypted = encrypt_token(new_tokens["access_token"])
            connection.token_expires_at = datetime.utcnow() + timedelta(
                seconds=new_tokens.get("expires_in", 3600)
            )
            if new_tokens.get("refresh_token"):
                connection.refresh_token_encrypted = encrypt_token(new_tokens["refresh_token"])
            connection.last_used_at = datetime.utcnow()
            db.commit()
            logger.info(f"Refreshed token for connection {connection.id}")
        except Exception:
            logger.exception(f"Failed to refresh token for connection {connection.id}")
            connection.status = "expired"
            db.commit()
            raise

    connection.last_used_at = datetime.utcnow()
    return decrypt_token(connection.access_token_encrypted)
