"""
Processes a single queue item end-to-end.

Called by queue_manager._wrap_process via a semaphore, so concurrency is
bounded and failures are isolated per item.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
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
from app.services.downloader import download
from app.services.postprocess import finalize_movie, finalize_tv

log = logging.getLogger(__name__)


async def process_item(item_id: int) -> None:
    item = await queue_manager.get(item_id)
    if not item:
        return

    log.info("▶ processing #%d: %s", item.id, item.title)
    await queue_manager.update(
        item.id, status=ItemStatus.SCRAPING, progress=0.0, message="scraping"
    )

    tmp_dir = settings.tmp_path / f"{item.id}-{uuid.uuid4().hex[:8]}"

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
        await notifications.notify(
            "Download complete",
            f"{item.title}\n→ `{final_path}`",
            success=True,
        )
        log.info("✔ completed #%d: %s", item.id, final_path)

    except Exception as e:
        log.exception("✖ failed #%d: %s", item_id, e)
        # Clean the tmp scratch dir so failed attempts don't pile up
        # in the volume.
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        current = await queue_manager.get(item_id)
        attempts = (current.attempts if current else 0) + 1
        await queue_manager.update(
            item_id,
            status=ItemStatus.FAILED,
            message=str(e)[:300],
            attempts=attempts,
        )
        await notifications.notify(
            "Download failed",
            f"{(current.title if current else item_id)}\n{e}",
            success=False,
        )


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


