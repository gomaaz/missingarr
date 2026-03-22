import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from backend.config import settings
from backend.database import init_db, get_or_create_secret_key
from backend.log_broadcaster import broadcaster
from backend.agents.orchestrator import Orchestrator
from backend.api import health, instances, activity, history, searched
from backend.tooltips import TOOLTIPS
from backend.auth import (
    AuthMiddleware, verify_password, auth_enabled, init_auth,
    create_remember_token, _REMEMBER_COOKIE, _REMEMBER_MAX_AGE,
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


# Init DB early so we can read the persisted secret key before middleware is wired.
init_db()
_session_secret = get_or_create_secret_key()

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)

# Middleware order: last added = outermost = runs first.
# SessionMiddleware must wrap AuthMiddleware so session is available when auth checks it.
# Uses a DB-persisted secret key so sessions survive Docker restarts.
app.add_middleware(AuthMiddleware)
app.add_middleware(SessionMiddleware, secret_key=_session_secret, session_cookie="ma_session", https_only=False)

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
        "dashboard.html",
        template_ctx(request, cards=cards),
    )


@app.get("/instances", response_class=HTMLResponse)
async def instances_list(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        "instances/list.html",
        template_ctx(request, instances=all_instances),
    )


@app.get("/instances/new", response_class=HTMLResponse)
async def instance_new(request: Request):
    return templates.TemplateResponse(
        "instances/form.html",
        template_ctx(request, instance=None, action="/api/instances", method="POST"),
    )


@app.get("/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit(instance_id: int, request: Request):
    from backend import db
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        return RedirectResponse("/instances")
    return templates.TemplateResponse(
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
        "history.html",
        template_ctx(request, instances=all_instances),
    )


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    from backend import db
    recent = db.activity.query(limit=100, include_debug=False)
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        "logs.html",
        template_ctx(request, recent=recent, instances=all_instances),
    )


@app.get("/searched", response_class=HTMLResponse)
async def searched_page(request: Request):
    from backend import db
    all_instances = db.instances.get_all()
    counts = db.searched.count()
    return templates.TemplateResponse(
        "searched.html",
        template_ctx(request, instances=all_instances, counts=counts),
    )


@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse(
        "help.html",
        template_ctx(request),
    )


# ─── Auth routes ───────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str = ""):
    if not auth_enabled():
        return RedirectResponse("/", status_code=302)
    if request.session.get("user"):
        return RedirectResponse(next or "/", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "app_name": settings.app_name, "next": next, "error": error},
    )


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
    remember: bool = Form(default=False),
):
    if not auth_enabled():
        return RedirectResponse("/", status_code=302)

    if username == settings.auth_username and verify_password(password):
        request.session["user"] = username
        response = RedirectResponse(next or "/", status_code=302)
        if remember:
            token = create_remember_token(username)
            response.set_cookie(
                _REMEMBER_COOKIE, token,
                max_age=_REMEMBER_MAX_AGE,
                httponly=True,
                samesite="lax",
            )
        return response

    # Invalid credentials — re-render login with error
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "app_name": settings.app_name, "next": next, "error": "Invalid username or password."},
        status_code=401,
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(_REMEMBER_COOKIE)
    return response
