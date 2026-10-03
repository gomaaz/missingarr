"""Imports page API: the downloads Radarr and Sonarr hold back, the app's
proposal with missingarr's verdict, import, command status, discard and the
open-imports count.

Plain def routes: requests blocks, so FastAPI runs them in its threadpool.
Login and the cross-site check come from the middlewares. No answer carries
an API key, a command's exception or message, or an error body of the app:
errors are the fixed texts of service.arr_error(), command results the fixed
MSG_* texts of the service.
"""

import logging
from dataclasses import asdict
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend import db
from backend.imports import service

logger = logging.getLogger("missingarr.imports")

router = APIRouter(prefix="/imports")


class ImportRequest(BaseModel):
    download_id: str = Field(min_length=1, max_length=200)
    proposal_key: str = Field(pattern=r"^[0-9a-f]{16}$")


class DiscardRequest(BaseModel):
    download_id: str = Field(min_length=1, max_length=200)
    blocklist: bool = True


def queue_link(url: str) -> dict:
    """Where the app's queue page is. The stored URL is often an address
    only missingarr reaches (a container name), so the page puts its own
    host name in front: scheme://<hostname>[:port]path. Anything but
    http(s) falls back to plain http without port."""
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in ("http", "https"):
        return {"scheme": "http", "port": None, "path": "/activity/queue"}
    try:
        port = parts.port
    except ValueError:
        port = None
    return {"scheme": parts.scheme.lower(), "port": port, "path": parts.path.rstrip("/") + "/activity/queue"}


def _instance(instance_id: int, command_id: int | None = None) -> dict:
    """The stored instance: unknown -> 404, switched off -> 409. With a
    command id (command status) a command this process does not follow there
    is 404 first, before the switch: an edit, a switch-off or a delete forgets
    the instance's imports, and the page stops asking at that 404 instead of
    getting 409 until its budget ends."""
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    if command_id is not None and not service.command_known(instance_id, command_id):
        raise HTTPException(404, service.UNKNOWN_COMMAND)
    if not inst.get("enabled"):
        raise HTTPException(409, service.INSTANCE_OFF)
    return inst


def _agent(request: Request, inst: dict):
    return request.app.state.orchestrator.detached_agent(inst)


def _call(action, *args):
    """A refusal becomes 409, a command this process did not send 404, no
    free proposal slot of the app 503, a failure of *arr its status code
    (504, 503 or 502), never 401: the page takes a 401 for a lost session.
    Every detail is a fixed text."""
    try:
        return action(*args)
    except service.ImportConflict as exc:
        raise HTTPException(409, str(exc))
    except service.UnknownCommand as exc:
        raise HTTPException(404, str(exc))
    except service.ProposalsBusy as exc:
        raise HTTPException(503, str(exc))
    except (requests.exceptions.RequestException, ValueError) as exc:
        status, detail = service.arr_error(exc)
        raise HTTPException(status, detail)


@router.get("")
def list_imports(request: Request):
    """One box per instance; an app that cannot be read only fills its own
    box with the error."""
    boxes = []
    for inst in db.instances.get_all():
        box = {
            "id": inst["id"],
            "name": inst["name"],
            "type": inst["type"],
            "enabled": bool(inst.get("enabled")),
            "queue_link": queue_link(inst.get("url") or ""),
            "error": "",
            "starting": False,
            "downloads": [],
        }
        if box["enabled"]:
            try:
                agent = _agent(request, inst)
                box["downloads"] = [download.as_dict() for download in service.list_open(agent)]
                # Right after a start the app's queue is still empty: the page
                # says so instead of "Nothing open." (app_starting never raises).
                box["starting"] = not box["downloads"] and service.app_starting(agent)
            except Exception as exc:
                box["downloads"], box["starting"] = [], False
                box["error"] = service.arr_error(exc)[1]
                logger.warning("Imports: instance %s not read: %s", inst["id"], box["error"])
        boxes.append(box)
    return {"instances": boxes, "checked_at": service.stamp_utc()}


@router.get("/count")
def imports_count(request: Request):
    """Menu and dashboard. Enabled instances only, each read at most once a
    minute (service.cached_count)."""
    rows, stamps = [], []
    for inst in db.instances.get_all(include_disabled=False):
        try:
            result = service.cached_count(_agent(request, inst))
        except Exception as exc:     # cached_count never raises; building the agent can
            result = service.InstanceCount(count=None, error=service.arr_error(exc)[1], starting=False,
                                           checked_at=service.stamp_utc())
        rows.append({"id": inst["id"], "name": inst["name"], "count": result.count,
                     "error": result.error, "starting": result.starting})
        stamps.append(result.checked_at)
    counts = [row["count"] for row in rows]
    return {
        "total": sum(count for count in counts if count is not None),
        "complete": all(count is not None for count in counts),
        "per_instance": rows,
        "checked_at": min(stamps) if stamps else service.stamp_utc(),
    }


@router.get("/{instance_id}/proposal")
def proposal(instance_id: int, request: Request,
             download_id: str = Query(..., min_length=1, max_length=200)):
    agent = _agent(request, _instance(instance_id))
    return _call(service.proposal_view, agent, download_id)


@router.post("/{instance_id}/import")
def start_import(instance_id: int, data: ImportRequest, request: Request):
    # The revision first: an edit after the instance is read sends nothing (409).
    revision = service.instance_revision(instance_id)
    agent = _agent(request, _instance(instance_id))
    return asdict(_call(service.import_download, agent, data.download_id, data.proposal_key, revision))


@router.get("/{instance_id}/commands/{command_id}")
def command(instance_id: int, command_id: int, request: Request):
    """Only imports this process sent to this instance; any other id is 404,
    also when the instance was switched off, edited or deleted meanwhile."""
    agent = _agent(request, _instance(instance_id, command_id))
    return asdict(_call(service.command_status, agent, command_id))


@router.post("/{instance_id}/discard")
def discard(instance_id: int, data: DiscardRequest, request: Request):
    revision = service.instance_revision(instance_id)
    agent = _agent(request, _instance(instance_id))
    return asdict(_call(service.discard_download, agent, data.download_id, data.blocklist, revision))
