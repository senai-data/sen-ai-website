"""Fernet encryption for OAuth tokens + auto-refresh helper.

Tokens are encrypted at rest in the oauth_connections table. The symmetric
key lives in OAUTH_FERNET_KEY (env). To rotate: generate a new key, decrypt
all rows with the old key, re-encrypt with the new key, swap the env var.
"""

import logging
from datetime import datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy.orm import Session

from config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.oauth_fernet_key
        if not key:
            raise RuntimeError(
                "OAUTH_FERNET_KEY is not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_token(plain: str) -> str:
    """Encrypt a plaintext token string → Fernet ciphertext (base64 ASCII)."""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a Fernet ciphertext → plaintext token string."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


async def get_valid_access_token(connection, db: Session) -> str:
    """Return a usable access_token, refreshing transparently if expired.

    If token_expires_at is within 5 minutes, refresh via the provider and
    persist the new tokens. This avoids mid-request 401s from Google/Microsoft.

    Args:
        connection: OAuthConnection row (must be status='active').
        db: SQLAlchemy session (will be committed on refresh).

    Returns:
        Decrypted access_token ready for API calls.

    Raises:
        ValueError if the connection is not active or has no tokens.
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
        from services.oauth_providers import get_provider

        provider = get_provider(connection.provider)
        refresh_token = decrypt_token(connection.refresh_token_encrypted)
        try:
            new_tokens = await provider.refresh_access_token(refresh_token)
            connection.access_token_encrypted = encrypt_token(new_tokens["access_token"])
            connection.token_expires_at = datetime.utcnow() + timedelta(
                seconds=new_tokens.get("expires_in", 3600)
            )
            # Some providers return a new refresh token — update if present
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
