from fastapi import APIRouter, HTTPException, Request
from backend import db
from backend.models.instance import InstanceCreate, InstanceUpdate

router = APIRouter(prefix="/instances")


def _get_orchestrator(request: Request):
    return request.app.state.orchestrator


@router.get("")
def list_instances(request: Request):
    instances = db.instances.get_all()
    orchestrator = _get_orchestrator(request)
    result = []
    for inst in instances:
        state = orchestrator.get_agent_state(inst["id"]) or {}
        result.append({**inst, "agent_state": state})
    return result


@router.get("/{instance_id}")
def get_instance(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    state = _get_orchestrator(request).get_agent_state(instance_id) or {}
    return {**inst, "agent_state": state}


@router.post("", status_code=201)
def create_instance(data: InstanceCreate, request: Request):
    inst = db.instances.create(data.model_dump())
    if inst.get("enabled"):
        _get_orchestrator(request).start_agent(inst["id"])
    return inst


@router.put("/{instance_id}")
def update_instance(instance_id: int, data: InstanceUpdate, request: Request):
    inst = db.instances.update(instance_id, data.model_dump())
    if not inst:
        raise HTTPException(404, "Instance not found")
    _get_orchestrator(request).reload_agent(instance_id)
    return inst


@router.delete("/{instance_id}", status_code=204)
def delete_instance(instance_id: int, request: Request):
    _get_orchestrator(request).stop_agent(instance_id)
    deleted = db.instances.delete(instance_id)
    if not deleted:
        raise HTTPException(404, "Instance not found")


@router.post("/{instance_id}/toggle-skill")
def toggle_skill(instance_id: int, request: Request, skill: str, enabled: bool):
    if skill not in ("missing", "upgrades"):
        raise HTTPException(400, "skill must be 'missing' or 'upgrades'")
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    db.instances.toggle_skill(instance_id, skill, enabled)
    return {"status": "ok", "skill": skill, "enabled": enabled}


@router.post("/{instance_id}/trigger")
def trigger_instance(instance_id: int, request: Request, skill: str = "search_missing", force: bool = True):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    _get_orchestrator(request).trigger(instance_id, skill, force=force)
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
def test_connection(instance_id: int, request: Request):
    import requests as req_lib
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")

    url = inst["url"].rstrip("/") + "/api/v3/system/status"
    try:
        resp = req_lib.get(
            url,
            headers={"X-Api-Key": inst["api_key"]},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        db.instances.update_status(instance_id, "online")
        return {
            "status": "online",
            "version": data.get("version"),
            "appName": data.get("appName"),
        }
    except req_lib.exceptions.ConnectionError:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(503, "Cannot connect to instance")
    except req_lib.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response else 0
        if code in (401, 403):
            db.instances.update_status(instance_id, "error")
            raise HTTPException(401, "Invalid API key")
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(502, f"HTTP {code} from instance")


@router.post("/{instance_id}/toggle")
def toggle_instance(instance_id: int, enabled: bool, request: Request):
    inst = db.instances.toggle_enabled(instance_id, enabled)
    if not inst:
        raise HTTPException(404, "Instance not found")
    orchestrator = _get_orchestrator(request)
    if enabled:
        orchestrator.start_agent(instance_id)
    else:
        orchestrator.stop_agent(instance_id)
    return {"enabled": enabled}
