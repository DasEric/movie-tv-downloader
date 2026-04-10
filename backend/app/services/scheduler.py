"""
APScheduler wrapper for periodic release-date checks.

Items marked `auto_download=True` and currently in WAITING_RELEASE state
get polled against TMDB; when the release date is reached they flip to
QUEUED and the queue manager picks them up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from app.config import settings as env_settings
from app.db import session_scope
from app.models import ItemStatus, QueueItem, utcnow
from app.queue_manager import queue_manager
from app.services import settings_store, tmdb

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _resolve_tz():
    """Return a tz object for APScheduler. Falls back to UTC on any error."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(env_settings.timezone or "UTC")
    except Exception as e:
        log.warning(
            "could not load timezone %r (%s) — falling back to UTC",
            env_settings.timezone,
            e,
        )
        return timezone.utc


async def start() -> None:
    global _scheduler
    if _scheduler:
        return
    interval = int(await settings_store.get("release_check_interval_min", 60))
    # Pass an explicit timezone so APScheduler doesn't depend on tzlocal
    # discovering the host timezone — this is the cause of "no time zone
    # found" startup crashes inside slim Docker images.
    _scheduler = AsyncIOScheduler(timezone=_resolve_tz())
    _scheduler.add_job(
        _check_releases,
        "interval",
        minutes=interval,
        id="release_check",
        # tz-aware so APScheduler doesn't reinterpret it as local time
        next_run_time=datetime.now(timezone.utc),
    )
    _scheduler.start()
    log.info(
        "scheduler started (release check every %dm, tz=%s)",
        interval,
        env_settings.timezone,
    )


async def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def _check_releases() -> None:
    now = utcnow()
    try:
        async with session_scope() as s:
            items = (
                await s.execute(
                    select(QueueItem).where(
                        QueueItem.status == ItemStatus.WAITING_RELEASE
                    )
                )
            ).scalars().all()
            for it in items:
                if not it.tmdb_id:
                    continue
                rel = await tmdb.movie_release(it.tmdb_id)
                if rel and rel <= now:
                    it.release_date = rel
                    it.status = ItemStatus.QUEUED
                    it.message = f"released {rel:%Y-%m-%d}, enqueued"
                    s.add(it)
                    log.info("item %s: release reached, enqueuing", it.title)
        queue_manager._wake.set()
    except Exception:
        log.exception("release check failed")
