"""
Bot lifecycle supervisor.

The bot lives inside the FastAPI event loop. This module manages its
start/stop/restart based on settings, and exposes the "notify_completed"
hook that worker.py calls after a successful download.

reconcile() is idempotent and debounced: multiple near-simultaneous calls
(user spamming the save button) collapse into a single end-state transition.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlmodel import select

from app.db import session_scope
from app.models import ItemStatus, QueueItem
from app.services import settings_store

log = logging.getLogger(__name__)


# ---- singleton state ----

_bot = None  # PlexDownloaderBot instance
_task: Optional[asyncio.Task] = None
_lock = asyncio.Lock()
_last_error: Optional[str] = None


# ---- lifecycle ----

async def _desired_enabled() -> tuple[bool, str]:
    enabled = bool(await settings_store.get("discord_bot_enabled", False))
    token = str(await settings_store.get("discord_token") or "")
    if enabled and not token:
        return False, ""
    return enabled, token


def is_running() -> bool:
    return _bot is not None and _task is not None and not _task.done()


def status() -> dict:
    return {
        "running": is_running(),
        "error": _last_error,
    }


async def start_if_enabled() -> None:
    enabled, _ = await _desired_enabled()
    if not enabled:
        log.info("discord bot disabled — not starting")
        return
    await reconcile()


async def reconcile() -> None:
    """Bring the bot into alignment with the current settings."""
    async with _lock:
        enabled, token = await _desired_enabled()
        running = is_running()

        if not enabled:
            if running:
                await _stop_inner()
            return

        if running:
            # Token changes require a full restart.
            current_token = getattr(_bot, "_current_token", None)
            if current_token != token:
                await _stop_inner()
                await _start_inner(token)
            return

        await _start_inner(token)


async def _start_inner(token: str) -> None:
    global _bot, _task, _last_error
    _last_error = None
    try:
        from app.services.discord_bot.bot import PlexDownloaderBot

        bot = PlexDownloaderBot()
        bot._current_token = token  # type: ignore[attr-defined]
        _bot = bot

        async def _run() -> None:
            global _last_error
            try:
                await bot.start(token)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _last_error = str(e)
                log.exception("discord bot task crashed")

        _task = asyncio.create_task(_run(), name="discord_bot")
        log.info("discord bot starting...")
    except Exception as e:
        _last_error = str(e)
        log.exception("discord bot failed to start")
        _bot = None
        _task = None


async def _stop_inner() -> None:
    global _bot, _task
    bot = _bot
    task = _task
    _bot = None
    _task = None
    if bot is not None:
        try:
            await bot.close()
        except Exception as e:
            log.warning("error closing discord bot: %s", e)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception:
            log.exception("error awaiting discord bot task")
    log.info("discord bot stopped")


async def stop() -> None:
    async with _lock:
        if is_running():
            await _stop_inner()


# ---- worker-side notification hooks ----

_NON_TERMINAL = (
    ItemStatus.QUEUED,
    ItemStatus.SCRAPING,
    ItemStatus.DOWNLOADING,
    ItemStatus.PROCESSING,
    ItemStatus.PAUSED,
    ItemStatus.WAITING_RELEASE,
)


async def _batch_has_pending_siblings(item: QueueItem) -> bool:
    """Return True if other queue items in the same batch (same tmdb_id +
    notify_kind; for `new_season` also same season) are still running.

    We use this to delay the "now available" channel embed until the LAST
    episode in the batch completes — otherwise we'd spam one embed per
    episode.
    """
    if not item.tmdb_id:
        return False
    async with session_scope() as s:
        stmt = select(QueueItem).where(
            QueueItem.tmdb_id == item.tmdb_id,
            QueueItem.discord_notify_kind == item.discord_notify_kind,
            QueueItem.id != item.id,
            QueueItem.status.in_(_NON_TERMINAL),  # type: ignore[attr-defined]
        )
        if item.discord_notify_kind == "new_season" and item.season is not None:
            stmt = stmt.where(QueueItem.season == item.season)
        rows = (await s.execute(stmt)).scalars().all()
        return bool(rows)


async def notify_completed(item: QueueItem) -> None:
    """Called by worker.py after a QueueItem transitions to COMPLETED.
    Sends the user DM and the upload-channel embed when the batch is done.
    If the bot isn't running, this is a silent no-op."""
    if not is_running() or _bot is None:
        return
    if not item.discord_requester_id:
        return
    try:
        if await _batch_has_pending_siblings(item):
            # Not the last in batch — stay quiet.
            return

        bot = _bot
        # Build a RequestDecision-like object from the item for embed rendering.
        from app.services.discord_bot.flow import RequestDecision, RequestPath

        # Best-effort: re-pull TMDB details for poster/overview/rating.
        overview = None
        poster = None
        rating = None
        year = None
        try:
            from app.services import tmdb as tmdb_svc

            if item.kind.value == "movie" and item.tmdb_id:
                d = await tmdb_svc.details_movie(item.tmdb_id)
            elif item.tmdb_id:
                d = await tmdb_svc.details_tv(item.tmdb_id)
            else:
                d = None
            if d:
                overview = d.get("overview")
                poster = d.get("poster")
                rating = d.get("rating")
                y = d.get("year")
                year = int(y) if y and y != "0000" else None
        except Exception:
            pass

        decision = RequestDecision(
            path=RequestPath.DOWNLOAD_FULL,
            title=item.title,
            tmdb_id=item.tmdb_id or 0,
            media_type="movie" if item.kind.value == "movie" else "series",
            year=year,
            overview=overview,
            poster_url=poster,
            rating=rating,
        )

        # Import here to avoid circular at module load.
        from app.services.discord_bot.views import (
            _channel_embed,
            _get_upload_channel,
        )
        from app.services.discord_bot import strings as S

        channel = await _get_upload_channel(bot)
        if channel is not None:
            kind = item.discord_notify_kind or "movie"
            season = item.season if kind == "new_season" else None
            try:
                await channel.send(embed=_channel_embed(decision, kind, season))
            except Exception as e:
                log.warning("notify_completed: channel post failed: %s", e)

        # DM requester.
        try:
            user = await bot.fetch_user(int(item.discord_requester_id))
            await user.send(S.DONE_USER_DM.format(title=item.title))
        except Exception as e:
            log.warning("notify_completed: user DM failed: %s", e)
    except Exception:
        log.exception("notify_completed outer failure")


async def notify_failed(item: QueueItem, reason: str) -> None:
    """DM the owner (and requester) that a Discord-originated download failed."""
    if not is_running() or _bot is None:
        return
    if not item.discord_requester_id:
        return
    try:
        bot = _bot
        from app.services.discord_bot import strings as S

        owner_id = await settings_store.get("discord_owner_id") or ""
        if owner_id:
            try:
                owner = await bot.fetch_user(int(owner_id))
                await owner.send(
                    S.OWNER_FAILED_DM.format(title=item.title, reason=reason[:500])
                )
            except Exception as e:
                log.warning("notify_failed: owner DM failed: %s", e)
        try:
            user = await bot.fetch_user(int(item.discord_requester_id))
            await user.send(S.FAILED_USER_DM.format(title=item.title))
        except Exception as e:
            log.warning("notify_failed: user DM failed: %s", e)
    except Exception:
        log.exception("notify_failed outer failure")
