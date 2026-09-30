import asyncio
import logging
import math
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import settings
from backend.database import init_db
from backend.crypto import get_session_secret, init_crypto
from backend.log_broadcaster import broadcaster
from backend.agents.orchestrator import Orchestrator
from backend.api import health, instances, activity, history, searched
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.version}")
    init_auth()
    init_db()
    # Raises when the database needs SECRET_KEY and it is missing or wrong;
    # the start stops with that message in the log.
    init_crypto()

    # Wire broadcaster to current event loop
    loop = asyncio.get_event_loop()
    broadcaster.set_loop(loop)
    app.state.broadcaster = broadcaster

    # Start orchestrator
    orchestrator = Orchestrator(broadcaster=broadcaster)
    app.state.orchestrator = orchestrator
    orchestrator.start_all()
    logger.info("Orchestrator started")

    yield

    # Shutdown
    logger.info("Shutting down orchestrator...")
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


# ─── UI routes ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    orchestrator = request.app.state.orchestrator
    cards = []
    for inst in all_instances:
        state = orchestrator.get_agent_state(inst["id"]) or {}
        recent = db.history.get_last_for_instance(inst["id"])
        cards.append({"instance": inst, "state": state, "recent": recent})
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_ctx(request, cards=cards),
    )


@app.get("/instances", response_class=HTMLResponse)
async def instances_list(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        request,
        "instances/list.html",
        template_ctx(request, instances=all_instances),
    )


@app.get("/instances/new", response_class=HTMLResponse)
async def instance_new(request: Request):
    return templates.TemplateResponse(
        request,
        "instances/form.html",
        template_ctx(request, instance=None, action="/api/instances", method="POST"),
    )


@app.get("/instances/{instance_id}/card", response_class=HTMLResponse)
async def instance_card(instance_id: int, request: Request):
    from backend import db
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        from fastapi import HTTPException
        raise HTTPException(404)
    orchestrator = request.app.state.orchestrator
    state = orchestrator.get_agent_state(instance_id) or {}
    recent = db.history.get_last_for_instance(instance_id)
    return templates.TemplateResponse(
        request,
        "instances/card.html",
        template_ctx(request, inst=inst, state=state, recent=recent, conn=inst["connection_status"]),
    )


@app.get("/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit(instance_id: int, request: Request):
    from backend import db
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        return RedirectResponse("/instances")
    return templates.TemplateResponse(
        request,
        "instances/form.html",
        template_ctx(
            request,
            instance=inst,
            action=f"/api/instances/{instance_id}",
            method="PUT",
        ),
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        request,
        "history.html",
        template_ctx(request, instances=all_instances),
    )


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    from backend import db
    recent = db.activity.query(limit=100, include_debug=False)
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        request,
        "logs.html",
        template_ctx(request, recent=recent, instances=all_instances),
    )


@app.get("/searched", response_class=HTMLResponse)
async def searched_page(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    counts = db.searched.count()
    return templates.TemplateResponse(
        request,
        "searched.html",
        template_ctx(request, instances=all_instances, counts=counts),
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
