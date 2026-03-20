import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from backend import db

router = APIRouter(prefix="/activity")


@router.get("")
def list_activity(
    instance_id: Optional[int] = None,
    level: Optional[str] = None,
    debug: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    entries = db.activity.query(
        instance_id=instance_id,
        level=level,
        include_debug=debug,
        limit=min(limit, 500),
        offset=offset,
    )
    return entries


@router.delete("")
def clear_activity():
    db.activity.clear()
    return {"status": "cleared"}


@router.get("/stream")
async def stream_activity(request: Request, debug: bool = False):
    broadcaster = request.app.state.broadcaster
    q = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    entry = json.loads(payload)
                    # Filter debug if not requested
                    if not debug and entry.get("level") == "debug":
                        continue
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Send keep-alive comment
                    yield ": keep-alive\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
