"""
Processes a single queue item end-to-end.

Called by queue_manager._wrap_process via a semaphore, so concurrency is
bounded and failures are isolated per item.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid

from app.config import settings
from app.models import ItemKind, ItemStatus
from app.queue_manager import queue_manager
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.base import EpisodeRef
from app.scrapers.megakino import MegakinoScraper
from app.scrapers.sto import StoScraper
from app.services import notifications, settings_store, subtitles
from app.services.captcha import CaptchaRequiredError
from app.services.downloader import download
from app.services.postprocess import finalize_movie, finalize_tv

log = logging.getLogger(__name__)


# Per-item cancel flags. The queue manager calls `signal_cancel(item_id)` on
# delete/pause/stop-all, which sets the threading.Event. The downloader's
# progress hook checks this flag on every tick and aborts yt-dlp at the
# next fragment boundary. Without this, task.cancel() only stops the
# coroutine — the underlying executor thread (running yt-dlp) keeps going
# and wastes bandwidth on a download that will be thrown away.
_cancel_events: dict[int, threading.Event] = {}


def signal_cancel(item_id: int) -> None:
    """Tell the downloader thread to abort its in-flight download."""
    evt = _cancel_events.get(item_id)
    if evt is not None:
        evt.set()


async def process_item(item_id: int) -> None:
    item = await queue_manager.get(item_id)
    if not item:
        return

    log.info("▶ processing #%d: %s", item.id, item.title)
    await queue_manager.update(
        item.id, status=ItemStatus.SCRAPING, progress=0.0, message="scraping"
    )

    tmp_dir = settings.tmp_path / f"{item.id}-{uuid.uuid4().hex[:8]}"
    cancel_event = threading.Event()
    _cancel_events[item_id] = cancel_event

    try:
        scraper = get_scraper(item.source)

        # ---- resolve stream ----
        if item.kind == ItemKind.MOVIE:
            if not isinstance(scraper, MegakinoScraper):
                raise RuntimeError(
                    f"{item.source} does not support movies in this build"
                )
            if not item.url:
                raise RuntimeError("movie item without URL")
            stream = await scraper.get_stream(item.url)
        elif item.kind == ItemKind.EPISODE:
            if not isinstance(scraper, (StoScraper, AniworldScraper)):
                raise RuntimeError(f"{item.source} does not support episodes")
            if not (item.slug and item.season and item.episode):
                raise RuntimeError("episode item missing slug/season/episode")
            ep = EpisodeRef(
                show=item.title,
                slug=item.slug,
                season=item.season,
                episode=item.episode,
                language=item.language,
            )
            stream = await scraper.get_stream(ep)
        else:
            raise RuntimeError(f"unsupported kind {item.kind}")

        await queue_manager.update(
            item.id,
            status=ItemStatus.DOWNLOADING,
            current_hoster=stream.hoster,
            message=f"downloading via {stream.hoster}",
            progress=0.0,
        )

        # ---- download ----
        quality = item.quality or await settings_store.get("quality_profile", "1080p")
        tmp_out = tmp_dir / "raw.%(ext)s"

        throttled_emit = _Throttle(1.0)

        def on_progress(ev: dict) -> None:
            # Runs on the event loop (bounced via call_soon_threadsafe
            # in the downloader). Kick off the DB update as a task.
            if ev.get("status") == "downloading":
                throttled_emit(
                    lambda: asyncio.create_task(
                        queue_manager.update(
                            item_id,
                            progress=float(ev.get("percent") or 0.0),
                            speed=ev.get("speed"),
                            eta=ev.get("eta"),
                        )
                    )
                )

        raw_path = await download(
            stream.url,
            tmp_out,
            quality,
            on_progress,
            http_headers=stream.headers,
            cancel_event=cancel_event,
        )

        # ---- post-process (ffmpeg -> mp4 -> plex path) ----
        await queue_manager.update(
            item.id,
            status=ItemStatus.PROCESSING,
            progress=99.0,
            message="converting to mp4",
        )

        if item.kind == ItemKind.MOVIE:
            year = None
            if item.release_date:
                year = item.release_date.year
            final_path = await finalize_movie(raw_path, item.title, year)
        else:
            final_path = await finalize_tv(
                raw_path, item.title, item.season or 1, item.episode or 1
            )

        # ---- subtitles ----
        try:
            await subtitles.fetch_for(final_path)
        except Exception as e:
            log.warning("subtitle fetch failed: %s", e)

        # ---- done ----
        await queue_manager.update(
            item.id,
            status=ItemStatus.COMPLETED,
            progress=100.0,
            message="done",
            output_path=str(final_path),
        )
        # Invalidate library cache so badges update immediately
        from app.api.library import invalidate_cache
        invalidate_cache()

        await notifications.notify(
            "Download complete",
            f"{item.title}\n→ `{final_path}`",
            success=True,
        )

        # Discord bot notification — only fires for items that originated
        # from a /film-anfrage or /serien-anfrage. Silent no-op otherwise.
        try:
            from app.services.discord_bot import notify_completed

            fresh = await queue_manager.get(item_id)
            if fresh and fresh.discord_requester_id:
                await notify_completed(fresh)
        except Exception:
            log.exception("discord notify_completed hook failed")

        log.info("✔ completed #%d: %s", item.id, final_path)

    except asyncio.CancelledError:
        # User cancelled via delete / pause / stop-all — don't treat as failure.
        log.info("⏸ cancelled #%d (user action)", item_id)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    except Exception as e:
        log.exception("✖ failed #%d: %s", item_id, e)
        # Clean the tmp scratch dir so failed attempts don't pile up
        # in the volume.
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        current = await queue_manager.get(item_id)
        attempts = (current.attempts if current else 0) + 1

        # Detect rate-limit / captcha errors and set a user-friendly
        # message with a [RATE_LIMITED] prefix so the UI can show
        # special instructions instead of a generic "failed" badge.
        err_str = str(e)
        is_rate_limited = (
            isinstance(e, CaptchaRequiredError)
            or "did not leave the site" in err_str
            or "all hosters exhausted" in err_str
        )

        if is_rate_limited:
            msg = (
                "[RATE_LIMITED] s.to hat die Anfragen blockiert. "
                "Bitte s.to im Browser öffnen, eventuelles Captcha lösen, "
                "kurz warten und dann erneut versuchen."
            )
            status = ItemStatus.PAUSED
        else:
            msg = err_str[:300]
            status = ItemStatus.FAILED

        await queue_manager.update(
            item_id,
            status=status,
            message=msg,
            attempts=attempts,
        )
        await notifications.notify(
            "Download failed" if not is_rate_limited else "Rate limited",
            f"{(current.title if current else item_id)}\n{msg}",
            success=False,
        )

        # Discord bot failure notification (same pattern as success path).
        try:
            from app.services.discord_bot import notify_failed

            if current and current.discord_requester_id and not is_rate_limited:
                await notify_failed(current, msg)
        except Exception:
            log.exception("discord notify_failed hook failed")

    finally:
        # Always remove the cancel flag so it doesn't leak across retries.
        _cancel_events.pop(item_id, None)


# ------------- helpers -------------

class _Throttle:
    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._last = 0.0

    def __call__(self, fn) -> None:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            try:
                fn()
            except Exception as e:
                # Progress updates are best-effort — never block downloads
                # because of a transient DB or loop hiccup.
                log.debug("throttled progress update failed: %s", e)


