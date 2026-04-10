"""
Structured logging + in-memory ring buffer so the UI log viewer can
stream the last N lines without hitting the filesystem.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Deque

from app.config import settings

_RING_SIZE = 2000
_ring: Deque[dict] = deque(maxlen=_RING_SIZE)
_subscribers: set[asyncio.Queue] = set()


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "msg": self.format(record),
            }
            _ring.append(entry)
            for q in list(_subscribers):
                try:
                    q.put_nowait(entry)
                except asyncio.QueueFull:
                    # Drop the oldest pending log so a slow client always
                    # converges to the latest tail.
                    try:
                        q.get_nowait()
                        q.put_nowait(entry)
                    except Exception:
                        pass
        except Exception:
            pass


def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.log_level.upper())

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    ring = RingBufferHandler()
    ring.setFormatter(fmt)
    root.addHandler(ring)

    # Less-noisy third-party loggers
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def recent_logs(limit: int = 500) -> list[dict]:
    return list(_ring)[-limit:]


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)
