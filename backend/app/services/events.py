"""
In-process pub/sub so the WebSocket layer can push queue updates to the UI
without polling the DB.

Slow / dead clients are handled gracefully: when their queue fills, we drop
the OLDEST event to make room for the newest. This guarantees the client
always converges to the latest state, instead of getting stuck on stale
events from minutes ago.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

_subscribers: set[asyncio.Queue] = set()


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def publish(event: str, data: Any) -> None:
    payload = {"event": event, "data": data}
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # Drop the oldest event so a slow client doesn't miss the latest
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                pass
