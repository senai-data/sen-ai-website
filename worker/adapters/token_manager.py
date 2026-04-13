"""Worker-side token manager — decrypt OAuth tokens from DB.

Mirrors api/services/token_manager.py but uses worker config.
For Phase 1 we only need decrypt (sync reads tokens, doesn't refresh —
refresh is handled by the API side on connection list/use).
"""

import logging

from cryptography.fernet import Fernet

from config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


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
