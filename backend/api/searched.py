from typing import Literal, Optional

from fastapi import APIRouter, Query, Response

from backend import db

router = APIRouter(prefix="/searched")

ItemType = Literal["movie", "episode", "season", "series"]


@router.get("")
def list_searched(
    response: Response,
    instance_id: Optional[int] = None,
    item_type: Optional[ItemType] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    response.headers["X-Total-Count"] = str(
        db.searched.count_filtered(instance_id=instance_id, item_type=item_type)
    )
    return db.searched.query(instance_id=instance_id, item_type=item_type, limit=limit, offset=offset)


@router.get("/count")
def count_searched(instance_id: Optional[int] = None):
    return db.searched.count(instance_id=instance_id)


@router.delete("")
def clear_all_searched():
    return {"deleted": db.searched.clear()}


@router.delete("/{instance_id}")
def clear_searched_for_instance(instance_id: int):
    return {"deleted": db.searched.clear(instance_id=instance_id)}
