import asyncio
import json
from typing import Optional


class LogBroadcaster:
    """
    Bridges sync agent threads → async FastAPI SSE clients.
    Agent threads call broadcast() which is thread-safe.
    SSE endpoint subscribes/unsubscribes async queues.
    """

    def __init__(self):
        self._queues: list[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    def broadcast(self, entry: dict):
        """Called from sync agent threads — thread-safe."""
        if not self._loop or not self._queues:
            return

        payload = json.dumps(entry, ensure_ascii=False)

        def _push():
            for q in list(self._queues):
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    pass  # drop if client is slow

        self._loop.call_soon_threadsafe(_push)


# Global singleton
broadcaster = LogBroadcaster()
