import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import settings
from backend.database import init_db
from backend.log_broadcaster import broadcaster
from backend.agents.orchestrator import Orchestrator
from backend.api import health, instances, activity, history
from backend.tooltips import TOOLTIPS

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("missingarr")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting {settings.app_name} v{settings.version}")
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


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
)

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
        **extra,
    }


# ─── API routers ───────────────────────────────────────────────────────────────
app.include_router(health.router, prefix="/api")
app.include_router(instances.router, prefix="/api")
app.include_router(activity.router, prefix="/api")
app.include_router(history.router, prefix="/api")


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
    entries = db.history.query_with_items(limit=50)
    all_instances = db.instances.get_all()
    return templates.TemplateResponse(
        "history.html",
        template_ctx(request, entries=entries, instances=all_instances),
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


@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    return templates.TemplateResponse(
        "help.html",
        template_ctx(request),
    )
