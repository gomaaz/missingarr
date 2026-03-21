from typing import Optional
from fastapi import APIRouter
from backend import db

router = APIRouter(prefix="/searched")


@router.get("")
def list_searched(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    return db.searched.query(
        instance_id=instance_id,
        item_type=item_type,
        limit=min(limit, 500),
        offset=offset,
    )


@router.get("/count")
def count_searched(instance_id: Optional[int] = None):
    return db.searched.count(instance_id=instance_id)


@router.delete("")
def clear_all_searched():
    deleted = db.searched.clear()
    return {"deleted": deleted}


@router.delete("/{instance_id}")
def clear_searched_for_instance(instance_id: int):
    deleted = db.searched.clear(instance_id=instance_id)
    return {"deleted": deleted}
