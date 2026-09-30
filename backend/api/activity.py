import asyncio
import json
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend import db

router = APIRouter(prefix="/activity")

KEEPALIVE_SECONDS = 30


@router.get("")
def list_activity(
    instance_id: Optional[int] = None,
    level: Optional[Literal["info", "warn", "error", "debug"]] = None,
    debug: bool = False,
    # Out-of-range values are an error, not silently clamped (B9).
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return db.activity.query(
        instance_id=instance_id, level=level, include_debug=debug, limit=limit, offset=offset,
    )


@router.delete("")
def clear_activity():
    db.activity.clear()
    return {"status": "cleared"}


@router.get("/stream")
async def stream_activity(request: Request, debug: bool = False):
    broadcaster = request.app.state.broadcaster
    queue = broadcaster.subscribe()
    shutdown = broadcaster.shutdown_event

    async def event_generator():
        try:
            while shutdown is None or not shutdown.is_set():
                if await request.is_disconnected():
                    break
                getter = asyncio.ensure_future(queue.get())
                waiters = {getter}
                stopper = None
                if shutdown is not None:
                    stopper = asyncio.ensure_future(shutdown.wait())
                    waiters.add(stopper)
                try:
                    done, _ = await asyncio.wait(
                        waiters, timeout=KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    # Also when the client leaves: Starlette cancels this
                    # generator, and asyncio.wait does not cancel what it waits on.
                    for task in waiters:
                        if not task.done():
                            task.cancel()
                if stopper is not None and stopper in done:
                    break  # server is shutting down (C-L7)
                if getter in done:
                    payload = getter.result()
                    if not debug and json.loads(payload).get("level") == "debug":
                        continue
                    yield f"data: {payload}\n\n"
                else:
                    yield ": keep-alive\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
