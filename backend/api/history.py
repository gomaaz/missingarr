from typing import Literal, Optional

from fastapi import APIRouter, Query, Response

from backend import db

router = APIRouter(prefix="/history")

ItemType = Literal["movie", "episode", "season", "series"]
SkillName = Literal["search_missing", "search_upgrades"]


@router.get("")
def list_history(
    instance_id: Optional[int] = None,
    skill: Optional[SkillName] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return db.history.query(instance_id=instance_id, skill=skill, limit=limit, offset=offset)


@router.get("/items")
def list_items_flat(
    response: Response,
    instance_id: Optional[int] = None,
    item_type: Optional[ItemType] = None,
    skill: Optional[SkillName] = None,
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    filters = {
        "instance_id": instance_id,
        "item_type": item_type,
        "skill": skill,
        "search": (q or "").strip() or None,
    }
    # The page shows "N items" and page numbers; they must describe the whole
    # filtered set, not the slice that was loaded (B-L5).
    response.headers["X-Total-Count"] = str(db.history.count_items_flat(**filters))
    return db.history.query_items_flat(**filters, limit=limit, offset=offset)


@router.delete("")
def clear_history():
    return {"status": "cleared", **db.history.clear()}
