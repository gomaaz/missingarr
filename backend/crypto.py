"""
Fernet (AES-128-CBC + HMAC-SHA256) encryption for API keys stored in the DB.

The encryption key is generated once and persisted in the app_settings table
so it survives container restarts without requiring manual SECRET_KEY setup.
Existing plain-text keys (no 'enc:' prefix) are returned as-is and re-encrypted
the next time they are saved.
"""
import logging
from cryptography.fernet import Fernet
from backend.database import get_db

logger = logging.getLogger("missingarr.crypto")

_ENC_PREFIX = "enc:"
_KEY_SETTING = "encryption_key"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (_KEY_SETTING,)
        ).fetchone()

        if row:
            raw_key = row["value"].encode()
        else:
            raw_key = Fernet.generate_key()
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)",
                (_KEY_SETTING, raw_key.decode()),
            )
            logger.info("Generated new encryption key and stored in DB")

    _fernet = Fernet(raw_key)
    return _fernet


def encrypt(plain: str) -> str:
    """Encrypt a plain-text string. Returns 'enc:<ciphertext>'."""
    if not plain:
        return plain
    token = _get_fernet().encrypt(plain.encode()).decode()
    return f"{_ENC_PREFIX}{token}"


def decrypt(value: str) -> str:
    """Decrypt a stored value. Plain-text (legacy) values are returned as-is."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # Legacy plain-text — pass through
    token = value[len(_ENC_PREFIX):]
    return _get_fernet().decrypt(token.encode()).decode()
