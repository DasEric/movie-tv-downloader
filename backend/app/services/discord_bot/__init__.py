"""
Embedded Discord bot.

Runs as a supervised asyncio task inside the FastAPI process — started
and stopped by the app lifespan, hot-reloaded on settings changes.

Public surface (used by main.py, api/settings.py, worker.py):

    await start_if_enabled()
    await stop()
    await reconcile()
    is_running() -> bool
    await notify_completed(item)
    await notify_failed(item)
"""
from __future__ import annotations

from app.services.discord_bot.runner import (  # noqa: F401
    is_running,
    notify_completed,
    notify_failed,
    reconcile,
    start_if_enabled,
    status,
    stop,
)
