"""
Auth helpers for Missingarr.

Set AUTH_USERNAME + AUTH_PASSWORD env vars to enable login protection.
Leave AUTH_PASSWORD empty to run without authentication (trusted network only).
"""
import secrets
import logging
from passlib.context import CryptContext
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import settings

logger = logging.getLogger("missingarr.auth")

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Paths that never require authentication
_PUBLIC = {"/login", "/static"}
_PUBLIC_PREFIXES = ("/static/", "/api/health")


def auth_enabled() -> bool:
    return bool(settings.auth_password)


def verify_password(plain: str) -> bool:
    """Constant-time comparison against the configured password."""
    if not settings.auth_password:
        return False
    # Support both plain-text and bcrypt-hashed passwords
    stored = settings.auth_password
    if stored.startswith("$2"):
        try:
            return _pwd_ctx.verify(plain, stored)
        except Exception:
            return False
    # Plain-text fallback — use constant-time compare
    return secrets.compare_digest(plain.encode(), stored.encode())


def hash_password(plain: str) -> str:
    """Generate a bcrypt hash for storage in SECRET_KEY env var."""
    return _pwd_ctx.hash(plain)


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    return request.session.get("user") == settings.auth_username


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Always allow public paths
        if path == "/login" or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        if not is_authenticated(request):
            return RedirectResponse(f"/login?next={path}", status_code=302)

        return await call_next(request)
