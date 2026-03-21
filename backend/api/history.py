from typing import Optional
from fastapi import APIRouter
from backend import db

router = APIRouter(prefix="/history")


@router.get("")
def list_history(
    instance_id: Optional[int] = None,
    skill: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    return db.history.query(
        instance_id=instance_id,
        skill=skill,
        limit=min(limit, 200),
        offset=offset,
    )


@router.get("/items")
def list_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    limit: int = 500,
):
    return db.history.query_items_flat(
        instance_id=instance_id,
        item_type=item_type,
        limit=min(limit, 1000),
    )


@router.delete("")
def clear_history():
    db.history.clear()
    return {"status": "cleared"}
