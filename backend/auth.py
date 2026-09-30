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
from urllib.parse import quote, urlsplit

from passlib.context import CryptContext
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

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


class LazySessionMiddleware(SessionMiddleware):
    """SessionMiddleware that reads its signing key and cookie flags on the
    first HTTP request instead of at import time.

    The key lives in the database (or is derived from SECRET_KEY), and
    importing backend.main must not open a database: tests and tools import it
    without the real data directory. Lifespan messages pass straight through,
    so the key is only read after the lifespan has run init_db().
    """

    def __init__(self, app, secret_provider, **options):
        self.app = app
        self._secret_provider = secret_provider
        self._options = options
        self._ready = False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not self._ready:
            SessionMiddleware.__init__(
                self, self.app,
                secret_key=self._secret_provider(),
                https_only=settings.cookie_secure,
                **self._options,
            )
            self._ready = True
        await SessionMiddleware.__call__(self, scope, receive, send)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_same_origin_request(headers) -> bool:
    """True unless a browser tells us the request comes from another site (C3).

    Modern browsers send Sec-Fetch-Site on every request; 'same-site' is not
    enough, because every other service on the same host counts as same-site.
    Older browsers send Origin on unsafe requests. Clients that send neither
    (curl, scripts) are not browsers and cannot be forged by a web page.
    """
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == (headers.get("host") or "").lower()


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in SAFE_METHODS and not is_same_origin_request(request.headers):
            logger.warning(
                "Blocked cross-site %s %s (Origin=%r, Sec-Fetch-Site=%r)",
                request.method, request.url.path,
                request.headers.get("origin"), request.headers.get("sec-fetch-site"),
            )
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)


def start_session(request: Request) -> None:
    request.session["user"] = settings.auth_username


def _login_target(wanted: str) -> str:
    return "/login?next=" + quote(wanted, safe="/")


def _current_page(request: Request) -> str:
    """The page an htmx call was made from (HX-Current-URL), as a local path.
    Anything odd falls back to '/'; /login runs safe_next() on it again."""
    current = urlsplit(request.headers.get("hx-current-url") or "")
    path = current.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path + (f"?{current.query}" if current.query else "")


def unauthenticated_response(request: Request):
    """Pages get the login redirect; API calls and htmx get a 401 they can act
    on. A 302 made fetch() follow to the login page and report success (C-L6).

    htmx is checked first: the dashboard card polls /api/instances/{id}/status
    every 5 s through htmx. Its 401 carries HX-Redirect back to the page the
    card is on, otherwise htmx would never leave the dead page."""
    path = request.url.path
    is_api = path.startswith("/api/")
    if request.headers.get("hx-request") == "true":
        if is_api:
            return JSONResponse(
                {"detail": "Not authenticated"}, status_code=401,
                headers={"HX-Redirect": _login_target(_current_page(request))},
            )
        wanted = path + (f"?{request.url.query}" if request.url.query else "")
        return Response(status_code=401, headers={"HX-Redirect": _login_target(wanted)})
    if is_api:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    wanted = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(_login_target(wanted), status_code=302)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/login" or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)
        if is_authenticated(request):
            return await call_next(request)
        token = request.cookies.get(_REMEMBER_COOKIE)
        if token and verify_remember_token(token) == settings.auth_username:
            start_session(request)
            return await call_next(request)
        return unauthenticated_response(request)
