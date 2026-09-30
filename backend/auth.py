"""
Auth helpers for Missingarr.

Authentication is always active. Set AUTH_USERNAME and AUTH_PASSWORD env vars
to configure credentials. If AUTH_PASSWORD is not set, a random password is
generated at startup and printed to the logs.
"""
import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

import bcrypt
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from backend import db
from backend.config import settings
from backend.crypto import get_session_secret

logger = logging.getLogger("missingarr.auth")

_PUBLIC_PREFIXES = ("/static/", "/api/health")

# Resolved at startup — either from env var or auto-generated
_active_password: str = ""

REMEMBER_COOKIE = "ma_remember"
REMEMBER_MAX_AGE = 30 * 24 * 60 * 60  # 30 days
TOKEN_VERSION_SETTING = "token_version"
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
BCRYPT_MAX_BYTES = 72


def init_auth() -> None:
    """Call once at startup to resolve the active password."""
    global _active_password
    _reset_token_version_cache()  # read it again from the database init_db() opens next
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


# ─── Remember-me tokens and sessions ──────────────────────────────────────────

_token_version_cache: int | None = None


def current_token_version() -> int:
    global _token_version_cache
    if _token_version_cache is None:
        _token_version_cache = int(db.app_settings.get_value(TOKEN_VERSION_SETTING) or 0)
    return _token_version_cache


def revoke_all_tokens() -> int:
    """Sign out everywhere: every remember-me token and every session carries
    the version they were issued with, and it no longer matches (C6)."""
    global _token_version_cache
    version = int(db.app_settings.get_value(TOKEN_VERSION_SETTING) or 0) + 1
    db.app_settings.set_value(TOKEN_VERSION_SETTING, str(version))
    _token_version_cache = version
    return version


def _reset_token_version_cache() -> None:
    global _token_version_cache
    _token_version_cache = None


def _password_fingerprint() -> str:
    return hashlib.sha256(b"missingarr-password|" + _active_password.encode()).hexdigest()


def _remember_signature(username: str, issued: int, version: int) -> str:
    message = f"v2|{username}|{issued}|{version}|{_password_fingerprint()}".encode()
    return hmac.new(get_session_secret().encode(), message, hashlib.sha256).hexdigest()


def create_remember_token(username: str, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    version = current_token_version()
    return f"v2.{issued}.{version}.{_remember_signature(username, issued, version)}"


def verify_remember_token(token: str, now: int | None = None) -> str | None:
    """Valid for 30 days from issue, until the next sign-out or password
    change. Tokens of 0.7.0 ("user:hmac") are not accepted any more."""
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v2":
        return None
    try:
        issued, version = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    current = int(time.time() if now is None else now)
    if issued > current + 300 or current - issued > REMEMBER_MAX_AGE:
        return None
    if version != current_token_version():
        return None
    expected = _remember_signature(settings.auth_username, issued, version)
    return settings.auth_username if hmac.compare_digest(parts[3], expected) else None


def is_authenticated(request: Request) -> bool:
    session = request.session
    return (
        session.get("user") == settings.auth_username
        and session.get("tv") == current_token_version()
        and session.get("pf") == _password_fingerprint()[:16]
    )


def start_session(request: Request) -> None:
    request.session["user"] = settings.auth_username
    request.session["tv"] = current_token_version()
    request.session["pf"] = _password_fingerprint()[:16]


# ─── Password ─────────────────────────────────────────────────────────────────

def verify_password(plain: str) -> bool:
    """Plain text compared in constant time, or a bcrypt hash checked with
    bcrypt directly. The hashing library used before 0.8.0 could not read
    bcrypt >= 4.1 and rejected every login with a hash (C-L3)."""
    if not _active_password:
        return False
    if _active_password.startswith(_BCRYPT_PREFIXES):
        # bcrypt only ever used the first 72 bytes. bcrypt >= 5 raises on
        # longer input instead of cutting it; cut it here like older bcrypt
        # releases and htpasswd did, so their hashes keep working and a long
        # password is a plain mismatch, not an error line (C-L3).
        candidate = plain.encode()[:BCRYPT_MAX_BYTES]
        try:
            return bcrypt.checkpw(candidate, _active_password.encode())
        except ValueError as exc:
            logger.error("bcrypt check failed (%s) — is AUTH_PASSWORD a complete bcrypt hash?", exc)
            return False
    return secrets.compare_digest(plain.encode(), _active_password.encode())


# ─── Login throttling ─────────────────────────────────────────────────────────

class LoginThrottle:
    """Per client address, in memory (C7). The first five failures are free;
    from the fifth on the address waits 30 s, doubling up to 15 minutes. A
    success clears the address; an address quiet for an hour is forgotten."""

    FREE_ATTEMPTS = 5
    BASE_DELAY_SECONDS = 30
    MAX_DELAY_SECONDS = 15 * 60
    FORGET_AFTER_SECONDS = 60 * 60

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, list] = {}  # address -> [failures, locked_until, last_failure]

    def _prune(self, now: float) -> None:
        stale = [a for a, (_, until, last) in self._state.items()
                 if now >= until and now - last > self.FORGET_AFTER_SECONDS]
        for address in stale:
            del self._state[address]

    def retry_after(self, address: str) -> int:
        with self._lock:
            state = self._state.get(address)
            if not state:
                return 0
            return max(0, math.ceil(state[1] - self._clock()))

    def record_failure(self, address: str) -> int:
        with self._lock:
            now = self._clock()
            self._prune(now)
            failures, locked_until, _ = self._state.get(address, [0, 0.0, now])
            failures += 1
            if failures >= self.FREE_ATTEMPTS:
                steps = min(failures - self.FREE_ATTEMPTS, 10)
                locked_until = now + min(self.MAX_DELAY_SECONDS, self.BASE_DELAY_SECONDS * 2 ** steps)
            self._state[address] = [failures, locked_until, now]
            return failures

    def record_success(self, address: str) -> None:
        with self._lock:
            self._state.pop(address, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


login_throttle = LoginThrottle()


# ─── Middleware ───────────────────────────────────────────────────────────────

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
        token = request.cookies.get(REMEMBER_COOKIE)
        if token and verify_remember_token(token) == settings.auth_username:
            start_session(request)
            return await call_next(request)
        return unauthenticated_response(request)
