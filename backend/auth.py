"""
Auth helpers for Missingarr.

Authentication is always active. Set AUTH_USERNAME and AUTH_PASSWORD env vars
to configure credentials. If AUTH_PASSWORD is not set, a random password is
generated at startup and printed to the logs.
"""
import hmac as _hmac
import hashlib
import secrets
import logging
from passlib.context import CryptContext
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings

logger = logging.getLogger("missingarr.auth")

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
_PUBLIC_PREFIXES = ("/static/", "/api/health")

# Resolved at startup — either from env var or auto-generated
_active_password: str = ""

_REMEMBER_COOKIE = "ma_remember"
_REMEMBER_MAX_AGE = 30 * 24 * 60 * 60  # 30 days


def _remember_secret() -> str:
    from backend.database import get_or_create_secret_key
    return get_or_create_secret_key()


def create_remember_token(username: str) -> str:
    key = _remember_secret().encode()
    sig = _hmac.new(key, username.encode(), hashlib.sha256).hexdigest()
    return f"{username}:{sig}"


def verify_remember_token(token: str) -> str | None:
    try:
        username, sig = token.rsplit(":", 1)
        key = _remember_secret().encode()
        expected = _hmac.new(key, username.encode(), hashlib.sha256).hexdigest()
        if _hmac.compare_digest(sig, expected):
            return username
    except Exception:
        pass
    return None


def init_auth() -> None:
    """Call once at startup to resolve the active password."""
    global _active_password
    if settings.auth_password:
        _active_password = settings.auth_password
        logger.info(f"Auth enabled — username: {settings.auth_username}")
    else:
        _active_password = secrets.token_urlsafe(12)
        logger.warning("=" * 60)
        logger.warning("  AUTH_PASSWORD not set — generated a temporary password:")
        logger.warning(f"  Username : {settings.auth_username}")
        logger.warning(f"  Password : {_active_password}")
        logger.warning("  Set AUTH_PASSWORD in your environment to make it permanent.")
        logger.warning("=" * 60)


def auth_enabled() -> bool:
    return True


def verify_password(plain: str) -> bool:
    """Constant-time comparison. Supports plain-text and bcrypt hashes."""
    if not _active_password:
        return False
    if _active_password.startswith("$2"):
        try:
            return _pwd_ctx.verify(plain, _active_password)
        except Exception:
            return False
    return secrets.compare_digest(plain.encode(), _active_password.encode())


def is_authenticated(request: Request) -> bool:
    return request.session.get("user") == settings.auth_username


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path == "/login" or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        if not is_authenticated(request):
            # Check remember-me cookie and auto-restore session
            token = request.cookies.get(_REMEMBER_COOKIE)
            if token:
                username = verify_remember_token(token)
                if username == settings.auth_username:
                    request.session["user"] = username
                    return await call_next(request)
            return RedirectResponse(f"/login?next={path}", status_code=302)

        return await call_next(request)
