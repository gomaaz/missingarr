import asyncio
import logging
import math
import signal
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend import db
from backend.config import settings
from backend.database import init_db
from backend.crypto import get_session_secret, init_crypto
from backend.log_broadcaster import broadcaster
from backend.agents.orchestrator import Orchestrator
from backend.api import health, instances, activity, history, searched, checked_search
from backend.api.instances import public_instance
from backend.checked_search.settings import (
    FIELD_LABELS, GENERAL_FIELDS, RADARR_FIELDS, SETTING_BOUNDS, SONARR_FIELDS, CheckedSearchSettings,
)
from backend.models.instance import FIELD_BOUNDS
from backend.tooltips import TOOLTIPS
from backend.auth import (
    AuthMiddleware, CSRFMiddleware, LazySessionMiddleware, REMEMBER_COOKIE, REMEMBER_MAX_AGE,
    auth_enabled, create_remember_token, init_auth, is_authenticated, login_throttle,
    revoke_all_tokens, start_session, verify_password,
)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("missingarr")


def install_shutdown_signal_hook(broadcaster) -> None:
    """Wake open log streams as soon as SIGTERM/SIGINT arrives (C-L7).

    uvicorn waits for running responses before it runs the lifespan shutdown,
    and a log stream never ends by itself, so a single open browser tab kept
    the process alive until Docker's SIGKILL. The previous handler (uvicorn's)
    still runs afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        return  # signal handlers can only be set from the main thread (e.g. not under TestClient)
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(signum)

        def handler(received, frame, previous=previous):
            broadcaster.request_shutdown()
            if callable(previous):
                previous(received, frame)
            elif previous == signal.SIG_DFL:
                signal.signal(received, signal.SIG_DFL)
                signal.raise_signal(received)

        signal.signal(signum, handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.version}")
    init_auth()
    init_db()
    # Runs a killed process left on 'running' (B-L1).
    interrupted = db.history.close_interrupted_runs()
    if interrupted:
        logger.warning("Closed %d search run(s) interrupted by the last shutdown", interrupted)
    # Raises when the database needs SECRET_KEY and it is missing or wrong;
    # the start stops with that message in the log.
    init_crypto()

    broadcaster.set_loop(asyncio.get_running_loop())
    install_shutdown_signal_hook(broadcaster)
    app.state.broadcaster = broadcaster

    orchestrator = Orchestrator(broadcaster=broadcaster)
    app.state.orchestrator = orchestrator
    orchestrator.start_all()
    logger.info("Orchestrator started")

    yield

    logger.info("Shutting down orchestrator...")
    broadcaster.request_shutdown()
    orchestrator.stop_all()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)

# Middleware order: last added = outermost = runs first.
# Session must wrap Auth so the session is available when Auth checks it.
# The session key is read on the first request, after the lifespan ran init_db().
app.add_middleware(AuthMiddleware)
app.add_middleware(
    LazySessionMiddleware,
    secret_provider=get_session_secret,
    session_cookie="ma_session",
    same_site="lax",
)
# Outermost: a cross-site write is refused before anything else runs (C3).
app.add_middleware(CSRFMiddleware)

# Static files & templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def checked_search_form() -> dict:
    """Field groups of the form section "Checked search". A rule both apps
    have (skip_existing_file) is rendered once, outside the app groups."""
    both = [f for f in RADARR_FIELDS if f in SONARR_FIELDS]
    defaults = CheckedSearchSettings().model_dump()
    return {
        "general": list(GENERAL_FIELDS),
        "radarr": [f for f in RADARR_FIELDS if f not in both],
        "sonarr": [f for f in SONARR_FIELDS if f not in both],
        "both": both,
        "labels": FIELD_LABELS,
        "bounds": SETTING_BOUNDS,
        "defaults": defaults,
        "kinds": {
            name: "bool" if isinstance(value, bool) else "list" if isinstance(value, list) else "int"
            for name, value in defaults.items()
        },
    }


def template_ctx(request: Request, **extra) -> dict:
    """Base context passed to all templates."""
    return {
        "request": request,
        "app_name": settings.app_name,
        "version": settings.version,
        "tooltips": TOOLTIPS,
        "auth_enabled": auth_enabled(),
        **extra,
    }


# ─── API routers ───────────────────────────────────────────────────────────────
app.include_router(health.router, prefix="/api")
app.include_router(instances.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(searched.router, prefix="/api")
app.include_router(checked_search.router, prefix="/api")


# ─── UI routes ─────────────────────────────────────────────────────────────────

# Every instance handed to a template goes through public_instance(): pages
# never contain the API key (C1).

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    orchestrator = request.app.state.orchestrator
    cards = []
    for inst in db.instances.get_all():
        cards.append({
            "instance": public_instance(inst),
            "state": orchestrator.get_agent_state(inst["id"]) or {},
            "recent": db.history.get_last_for_instance(inst["id"]),
        })
    return templates.TemplateResponse(request, "dashboard.html", template_ctx(request, cards=cards))


@app.get("/instances", response_class=HTMLResponse)
async def instances_list(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "instances/list.html", template_ctx(request, instances=instances))


@app.get("/instances/new", response_class=HTMLResponse)
async def instance_new(request: Request):
    return templates.TemplateResponse(
        request, "instances/form.html",
        template_ctx(request, instance=None, action="/api/instances", method="POST", bounds=FIELD_BOUNDS,
                     cs=checked_search_form()),
    )


@app.get("/instances/{instance_id}/card", response_class=HTMLResponse)
async def instance_card(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404)
    state = request.app.state.orchestrator.get_agent_state(instance_id) or {}
    recent = db.history.get_last_for_instance(instance_id)
    return templates.TemplateResponse(
        request, "instances/card.html",
        template_ctx(request, inst=public_instance(inst), state=state, recent=recent,
                     conn=inst["connection_status"]),
    )


@app.get("/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        return RedirectResponse("/instances")
    return templates.TemplateResponse(
        request, "instances/form.html",
        template_ctx(request, instance=public_instance(inst), action=f"/api/instances/{instance_id}",
                     method="PUT", bounds=FIELD_BOUNDS, cs=checked_search_form()),
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "history.html", template_ctx(request, instances=instances))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    recent = db.activity.query(limit=100, include_debug=False)
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "logs.html", template_ctx(request, recent=recent, instances=instances))


@app.get("/searched", response_class=HTMLResponse)
async def searched_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(
        request, "searched.html", template_ctx(request, instances=instances, counts=db.searched.count()),
    )


@app.get("/checked-search", response_class=HTMLResponse)
async def checked_search_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(
        request, "checked_search.html",
        template_ctx(request, instances=instances, summary=db.checked_search_log.summary(current_round=True)),
    )


@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse(
        request,
        "help.html",
        template_ctx(request),
    )


# ─── Auth routes ───────────────────────────────────────────────────────────────

def safe_next(value: str | None) -> str:
    """Only local paths. '//host' and '/\\host' are treated by browsers as
    another host (C10)."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or any(ord(ch) < 32 for ch in value):
        return "/"
    return value


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_page(request: Request, next_path: str, error: str, status: int, headers: dict | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "app_name": settings.app_name, "next": next_path, "error": error},
        status_code=status,
        headers=headers,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str = ""):
    target = safe_next(next)
    if not auth_enabled() or is_authenticated(request):
        return RedirectResponse(target, status_code=302)
    return _login_page(request, target, error, 200)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
    remember: bool = Form(default=False),
):
    target = safe_next(next)
    if not auth_enabled():
        return RedirectResponse(target, status_code=302)

    address = client_ip(request)
    wait = login_throttle.retry_after(address)
    if wait:
        logger.warning("Sign-in from %s refused — locked for another %ds", address, wait)
        return _login_page(
            request, target,
            f"Too many failed sign-in attempts. Try again in {math.ceil(wait / 60)} minute(s).",
            429, {"Retry-After": str(wait)},
        )

    if username == settings.auth_username and verify_password(password):
        login_throttle.record_success(address)
        start_session(request)
        response = RedirectResponse(target, status_code=302)
        if remember:
            response.set_cookie(
                REMEMBER_COOKIE, create_remember_token(username),
                max_age=REMEMBER_MAX_AGE, httponly=True, samesite="lax",
                secure=settings.cookie_secure,
            )
        return response

    failures = login_throttle.record_failure(address)
    logger.warning("Failed sign-in for user %r from %s (%d in a row)", username[:64], address, failures)
    return _login_page(request, target, "Invalid username or password.", 401)


@app.post("/logout")
async def logout(request: Request):
    # Signs out every browser and script, not only this one (C6).
    revoke_all_tokens()
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(REMEMBER_COOKIE, httponly=True, samesite="lax", secure=settings.cookie_secure)
    return response
