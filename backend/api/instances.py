import requests
from fastapi import APIRouter, HTTPException, Request

from backend import db
from backend.agents.base import TRIGGER_BUSY, TRIGGER_UNKNOWN_SKILL
from backend.agents.orchestrator import TRIGGER_NOT_FOUND
from backend.models.instance import InstanceCreate, InstanceUpdate

router = APIRouter(prefix="/instances")

API_KEY_MASK = "********"
TRIGGERABLE_SKILLS = ("search_missing", "search_upgrades", "health_check", "verify_commands")
DELETE_WAIT_SECONDS = 15.0


def _get_orchestrator(request: Request):
    return request.app.state.orchestrator


def public_instance(inst: dict) -> dict:
    """The instance as a browser may see it — never the API key (C1)."""
    out = {key: value for key, value in inst.items() if key != "api_key"}
    out["api_key_set"] = bool(inst.get("api_key"))
    out["api_key"] = API_KEY_MASK if out["api_key_set"] else ""
    return out


def _normalized_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


@router.get("")
def list_instances(request: Request):
    orchestrator = _get_orchestrator(request)
    return [
        {**public_instance(inst), "agent_state": orchestrator.get_agent_state(inst["id"]) or {}}
        for inst in db.instances.get_all()
    ]


@router.get("/{instance_id}")
def get_instance(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    state = _get_orchestrator(request).get_agent_state(instance_id) or {}
    return {**public_instance(inst), "agent_state": state}


@router.post("", status_code=201)
def create_instance(data: InstanceCreate, request: Request):
    inst = db.instances.create(data.model_dump())
    if inst.get("enabled"):
        _get_orchestrator(request).start_agent(inst["id"])
    return public_instance(inst)


@router.put("/{instance_id}")
def update_instance(instance_id: int, data: InstanceUpdate, request: Request):
    existing = db.instances.get_by_id(instance_id)
    if not existing:
        raise HTTPException(404, "Instance not found")
    payload = data.model_dump()
    new_key = (payload.get("api_key") or "").strip()
    if new_key == API_KEY_MASK:
        new_key = ""
    payload["api_key"] = new_key or None
    if not new_key and _normalized_url(payload["url"]) != _normalized_url(existing["url"]):
        # Otherwise the stored key could be sent to any new address (C1/C4).
        raise HTTPException(400, "The URL changed — enter the API key again so it is not sent to a new address.")
    inst = db.instances.update(instance_id, payload)
    _get_orchestrator(request).reload_agent(instance_id)
    return public_instance(inst)


@router.delete("/{instance_id}", status_code=204)
def delete_instance(instance_id: int, request: Request):
    if not db.instances.get_by_id(instance_id):
        raise HTTPException(404, "Instance not found")
    # Abort a running search and wait for it before the row (and its foreign
    # keys) disappear (A-L5).
    _get_orchestrator(request).forget_instance(instance_id, wait_seconds=DELETE_WAIT_SECONDS)
    db.instances.delete(instance_id)


@router.post("/{instance_id}/toggle-skill")
def toggle_skill(instance_id: int, request: Request, skill: str, enabled: bool):
    if skill not in ("missing", "upgrades"):
        raise HTTPException(400, "skill must be 'missing' or 'upgrades'")
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    per_run_field = "missing_per_run" if skill == "missing" else "upgrades_per_run"
    if enabled and int(inst.get(per_run_field) or 0) < 1:
        raise HTTPException(409, f"Set '{per_run_field}' to at least 1 before enabling this skill.")
    db.instances.toggle_skill(instance_id, skill, enabled)
    # The job exists already; the agent only needs the new flag (A2).
    _get_orchestrator(request).refresh_config(instance_id)
    return {"status": "ok", "skill": skill, "enabled": enabled}


@router.post("/{instance_id}/trigger")
def trigger_instance(instance_id: int, request: Request, skill: str = "search_missing", force: bool = True):
    if skill not in TRIGGERABLE_SKILLS:
        raise HTTPException(400, f"Unknown skill '{skill}'")
    if not db.instances.get_by_id(instance_id):
        raise HTTPException(404, "Instance not found")
    result = _get_orchestrator(request).trigger(instance_id, skill, force=force)
    if result == TRIGGER_BUSY:
        raise HTTPException(409, f"{skill} is already running — try again when it has finished")
    if result == TRIGGER_NOT_FOUND:
        raise HTTPException(404, "Instance not found")
    if result == TRIGGER_UNKNOWN_SKILL:
        raise HTTPException(400, f"Skill '{skill}' is not available for this instance")
    return {"status": "triggered", "skill": skill}


@router.get("/{instance_id}/status")
def instance_status(instance_id: int, request: Request):
    state = _get_orchestrator(request).get_agent_state(instance_id)
    if state is not None:
        return {
            "connection_status": state.get("connection_status", "unknown"),
            "last_seen_at": state.get("last_seen_at"),
            "agent_state": state,
        }
    # Agent not running (disabled) — fall back to DB
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    return {
        "connection_status": inst.get("connection_status", "unknown"),
        "last_seen_at": inst.get("last_seen_at"),
        "agent_state": {},
    }


@router.get("/{instance_id}/test")
def test_connection(instance_id: int):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")

    url = inst["url"].rstrip("/") + "/api/v3/system/status"
    try:
        # Never follow a redirect: it would carry the API key to wherever the
        # Location header points (C4).
        resp = requests.get(url, headers={"X-Api-Key": inst["api_key"]}, timeout=10, allow_redirects=False)
        if 300 <= resp.status_code < 400:
            db.instances.update_status(instance_id, "offline")
            raise HTTPException(502, f"Instance answered with a redirect (HTTP {resp.status_code}) — check the URL")
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(504, "Connection timed out")
    except requests.exceptions.ConnectionError:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(503, "Cannot connect to instance")
    except requests.exceptions.HTTPError as exc:
        # Not `if exc.response`: Response.__bool__ is .ok and False for every
        # 4xx/5xx, which turned every error into "HTTP 0" (A-L1).
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            db.instances.update_status(instance_id, "error")
            raise HTTPException(401, "Invalid API key")
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(502, f"HTTP {code} from instance")
    except ValueError:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(502, "Instance did not answer with JSON — is the URL correct?")

    db.instances.update_status(instance_id, "online")
    return {"status": "online", "version": data.get("version"), "appName": data.get("appName")}


@router.post("/{instance_id}/toggle")
def toggle_instance(instance_id: int, enabled: bool, request: Request):
    inst = db.instances.toggle_enabled(instance_id, enabled)
    if not inst:
        raise HTTPException(404, "Instance not found")
    orchestrator = _get_orchestrator(request)
    if enabled:
        orchestrator.start_agent(instance_id)
    else:
        # Disabling aborts a search in progress (A-L5).
        orchestrator.stop_agent(instance_id, abort_running=True)
    return {"enabled": enabled}
