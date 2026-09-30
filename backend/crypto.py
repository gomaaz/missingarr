"""
Encryption of the *arr API keys stored in the database, and the session key.

Without SECRET_KEY (the default, as before 0.8.0) a random Fernet key and a
random session key are generated once and stored in app_settings. That
survives restarts, but whoever has the database file has the keys too.

With SECRET_KEY both keys are derived from it (HKDF-SHA256) and never stored.
On the first start with SECRET_KEY the stored API keys are re-encrypted and
the stored keys are deleted (C5). From then on the database is marked
key_source=env and the same SECRET_KEY is required to start.

Stored values carry the prefix 'enc:'. Values without it are legacy plain
text and are returned as they are.
"""
import base64
import hashlib
import hmac
import logging
import sqlite3

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.config import settings
from backend.database import get_db, get_or_create_secret_key

logger = logging.getLogger("missingarr.crypto")

_ENC_PREFIX = "enc:"
_KEY_SETTING = "encryption_key"
KEY_SOURCE_SETTING = "key_source"
KEY_CHECK_SETTING = "secret_key_check"
FERNET_INFO = b"missingarr fernet v1"
SESSION_INFO = b"missingarr session v1"
CHECK_INFO = b"missingarr check v1"

_fernet: Fernet | None = None
_session_secret: str | None = None


def _derive(secret: str, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"missingarr", info=info).derive(secret.encode())


def _setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _stored_fernet_key(create: bool) -> bytes | None:
    with get_db() as conn:
        value = _setting(conn, _KEY_SETTING)
        if value:
            return value.encode()
        if not create:
            return None
        key = Fernet.generate_key()
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (_KEY_SETTING, key.decode()))
        logger.info("Generated new encryption key and stored in DB")
        return key


def init_crypto() -> None:
    """Decide where the keys come from. Called at startup; raises when the
    database needs SECRET_KEY and it is missing or wrong."""
    global _fernet, _session_secret
    secret = (settings.secret_key or "").strip()
    with get_db() as conn:
        source = _setting(conn, KEY_SOURCE_SETTING) or "db"
        stored_check = _setting(conn, KEY_CHECK_SETTING)

    if source == "env" and not secret:
        raise RuntimeError(
            "SECRET_KEY is not set, but this database was encrypted with a key derived from "
            "SECRET_KEY. Set the same SECRET_KEY again — it cannot be recovered."
        )

    if not secret:
        _fernet = Fernet(_stored_fernet_key(create=True))
        _session_secret = get_or_create_secret_key()
        return

    fernet_key = base64.urlsafe_b64encode(_derive(secret, FERNET_INFO))
    check = hmac.new(_derive(secret, CHECK_INFO), b"missingarr", hashlib.sha256).hexdigest()
    if source == "env":
        if not hmac.compare_digest(check, stored_check or ""):
            raise RuntimeError(
                "SECRET_KEY does not match the key this database was encrypted with. "
                "Restore the previous SECRET_KEY."
            )
    else:
        _migrate_to_secret_key(Fernet(fernet_key), check)

    _fernet = Fernet(fernet_key)
    _session_secret = _derive(secret, SESSION_INFO).hex()


def _migrate_to_secret_key(new: Fernet, check: str) -> None:
    old_key = _stored_fernet_key(create=False)
    old = Fernet(old_key) if old_key else None
    with get_db() as conn:
        rows = conn.execute("SELECT id, api_key FROM instances").fetchall()
        for row in rows:
            value = row["api_key"] or ""
            if not value:
                continue
            if value.startswith(_ENC_PREFIX):
                if old is None:
                    raise RuntimeError("Encrypted API keys found but no stored encryption key — cannot re-encrypt")
                plain = old.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
            else:
                plain = value
            conn.execute(
                "UPDATE instances SET api_key=? WHERE id=?",
                (_ENC_PREFIX + new.encrypt(plain.encode()).decode(), row["id"]),
            )
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, 'env')", (KEY_SOURCE_SETTING,))
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (KEY_CHECK_SETTING, check))
        conn.execute("DELETE FROM app_settings WHERE key IN (?, 'secret_key')", (_KEY_SETTING,))
    try:
        # The deleted keys would otherwise linger in free pages of the file.
        conn = sqlite3.connect(settings.database_url)
        conn.execute("VACUUM")
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("VACUUM after re-encryption failed: %s", exc)
    logger.warning(
        "Re-encrypted %d API key(s) with a key derived from SECRET_KEY and removed the stored keys. "
        "Keep SECRET_KEY safe: without it the API keys cannot be decrypted.",
        len(rows),
    )


def _get_fernet() -> Fernet:
    if _fernet is None:
        init_crypto()
    return _fernet


def get_session_secret() -> str:
    """Key that signs session cookies and remember-me tokens."""
    if _session_secret is None:
        init_crypto()
    return _session_secret


def _reset_cache() -> None:
    """Tests switch databases; forget the cached keys."""
    global _fernet, _session_secret
    _fernet = None
    _session_secret = None


def encrypt(plain: str) -> str:
    """Encrypt a plain-text string. Returns 'enc:<ciphertext>'."""
    if not plain:
        return plain
    return f"{_ENC_PREFIX}{_get_fernet().encrypt(plain.encode()).decode()}"


def decrypt(value: str) -> str:
    """Decrypt a stored value. Plain-text (legacy) values are returned as-is."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    return _get_fernet().decrypt(value[len(_ENC_PREFIX):].encode()).decode()
