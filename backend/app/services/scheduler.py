"""
APScheduler wrapper for periodic release-date checks.

Items in status=WAITING_RELEASE are polled on every tick. For each one we
try to find the title on its configured source site:

  * If it's a MOVIE and source=megakino: search megakino, pick the first
    matching result, fill in `url`, flip status to QUEUED.
  * If it's a SEASON (i.e. waiting for a TV show season) and source is
    s.to or aniworld: slug-search the source, list episodes in the
    requested season, spawn individual EPISODE items in the queue, then
    delete the waiting item.

The check interval is configurable via `release_check_interval_min` in
settings and is re-read every tick so users can change it without a
restart.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlmodel import select

from app.config import settings as env_settings
from app.db import session_scope
from app.models import (
    ItemKind,
    ItemSource,
    ItemStatus,
    QueueItem,
    SeasonWatch,
    utcnow,
)
from app.queue_manager import queue_manager
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.megakino import MegakinoScraper
from app.scrapers.sto import StoScraper
from app.services import settings_store

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_current_interval: int = 60


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
    global _scheduler, _current_interval
    if _scheduler:
        return
    _current_interval = int(await settings_store.get("release_check_interval_min", 60))
    # Pass an explicit timezone so APScheduler doesn't depend on tzlocal
    # discovering the host timezone — this is the cause of "no time zone
    # found" startup crashes inside slim Docker images.
    _scheduler = AsyncIOScheduler(timezone=_resolve_tz())
    _scheduler.add_job(
        _check_releases,
        "interval",
        minutes=_current_interval,
        id="release_check",
        # tz-aware so APScheduler doesn't reinterpret it as local time
        next_run_time=datetime.now(timezone.utc),
    )
    _scheduler.start()
    log.info(
        "scheduler started (release check every %dm, tz=%s)",
        _current_interval,
        env_settings.timezone,
    )


async def stop() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


async def reschedule_if_needed() -> None:
    """Re-read the interval from settings and reschedule the job if it changed."""
    global _current_interval
    if not _scheduler:
        return
    new_interval = int(await settings_store.get("release_check_interval_min", 60))
    if new_interval != _current_interval:
        try:
            _scheduler.reschedule_job(
                "release_check", trigger="interval", minutes=new_interval
            )
            _current_interval = new_interval
            log.info("scheduler interval changed to %dm", new_interval)
        except Exception as e:
            log.warning("reschedule failed: %s", e)


async def _check_releases() -> None:
    """
    Main scheduler tick. Always re-reads the interval first so runtime
    changes from the Settings UI take effect without a restart.

    Two different watch types are processed:
      1. WAITING_RELEASE queue items  — the "Upcoming" tab (unreleased
         movies / not-yet-on-site shows).
      2. SeasonWatch rows             — the "Watchlist" tab (ongoing
         seasons where new episodes trickle in language-by-language).
    """
    await reschedule_if_needed()

    # ---- Upcoming tab (WAITING_RELEASE queue items) ----
    try:
        async with session_scope() as s:
            items = (
                await s.execute(
                    select(QueueItem).where(
                        QueueItem.status == ItemStatus.WAITING_RELEASE
                    )
                )
            ).scalars().all()
    except Exception:
        log.exception("release check query failed")
        items = []

    if items:
        log.info("release check: %d upcoming items to probe", len(items))
        for it in items:
            try:
                await _probe_item(it)
            except Exception:
                log.exception("release probe failed for #%s (%s)", it.id, it.title)

    # ---- Season watchlist ----
    try:
        async with session_scope() as s:
            watches = (
                await s.execute(select(SeasonWatch))
            ).scalars().all()
    except Exception:
        log.exception("season watch query failed")
        watches = []

    if watches:
        log.info("release check: %d season watches to probe", len(watches))
        for w in watches:
            try:
                await _probe_season_watch(w)
            except Exception:
                log.exception(
                    "season watch probe failed for #%s (%s S%s)",
                    w.id,
                    w.title,
                    w.season,
                )

    # Kick the queue loop so any newly-spawned items are picked up.
    queue_manager._wake.set()


async def _probe_item(it: QueueItem) -> None:
    """
    Check if `it.title` is now available on its configured source site.
    If yes:
      * movie → fill url + flip to QUEUED
      * season (TV) → spawn episode items + delete the waiting row
    If not, update the message with a "last checked" timestamp.
    """
    now_iso = utcnow().strftime("%Y-%m-%d %H:%M")

    # --- MOVIE path (megakino) ---
    if it.kind == ItemKind.MOVIE:
        if it.source != ItemSource.MEGAKINO:
            log.debug(
                "upcoming movie #%d has unsupported source %s", it.id, it.source
            )
            return

        scraper = get_scraper(it.source)
        if not isinstance(scraper, MegakinoScraper):
            return

        try:
            hits = await scraper.search(it.title)
        except Exception as e:
            log.warning("megakino search for %r failed: %s", it.title, e)
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — search error"
            )
            return

        if not hits:
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — not yet available"
            )
            return

        # Take the first result. (We trust TMDB-resolved titles to be specific.)
        best = hits[0]
        log.info("upcoming movie #%d available: %s", it.id, best.url)
        await queue_manager.update(
            it.id,
            status=ItemStatus.QUEUED,
            url=best.url,
            message=f"released — found on megakino",
        )
        return

    # --- SEASON path (s.to / aniworld TV shows) ---
    if it.kind == ItemKind.SEASON:
        if it.source not in (ItemSource.STO, ItemSource.ANIWORLD):
            return
        if not it.season:
            log.warning("upcoming season #%d has no season number", it.id)
            return

        scraper = get_scraper(it.source)
        if not isinstance(scraper, (StoScraper, AniworldScraper)):
            return

        # Slug search — returns at most one hit for s.to / aniworld
        try:
            hits = await scraper.search(it.title)
        except Exception as e:
            log.warning("%s search for %r failed: %s", it.source, it.title, e)
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — search error"
            )
            return

        if not hits:
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — show not yet on site"
            )
            return

        # Extract slug from the found URL
        hit = hits[0]
        if it.source == ItemSource.STO:
            from app.scrapers.sto import slug_from_url

            slug = slug_from_url(hit.url)
        else:
            from app.scrapers.aniworld import slug_from_url as aw_slug

            slug = aw_slug(hit.url)

        if not slug:
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — could not parse slug"
            )
            return

        # Try to list the requested season
        try:
            episodes = await scraper.list_episodes(slug, it.season)
        except Exception as e:
            log.info(
                "upcoming #%d: season %d not yet available (%s)",
                it.id,
                it.season,
                e,
            )
            await queue_manager.update(
                it.id, message=f"checked {now_iso} — season {it.season} not found yet"
            )
            return

        if not episodes:
            await queue_manager.update(
                it.id,
                message=f"checked {now_iso} — season {it.season} has no episodes yet",
            )
            return

        # Filter by language availability — the user explicitly asked for
        # "in der Sprache die man will". Don't spawn items for episodes
        # that aren't actually available in the requested language yet.
        available: list[int] = []
        for ep in episodes:
            try:
                has_lang = await scraper.episode_has_language(
                    slug, it.season, ep, it.language
                )
            except Exception as e:
                log.warning(
                    "upcoming #%d: lang check for E%02d failed: %s",
                    it.id,
                    ep,
                    e,
                )
                continue
            if has_lang:
                available.append(ep)

        if not available:
            await queue_manager.update(
                it.id,
                message=(
                    f"checked {now_iso} — {len(episodes)} episodes on site "
                    f"but none in {it.language} yet"
                ),
            )
            return

        # Spawn episode items and remove the waiting row
        log.info(
            "upcoming #%d: season %d available in %s — spawning %d episodes",
            it.id,
            it.season,
            it.language,
            len(available),
        )
        for ep in available:
            new_item = QueueItem(
                source=it.source,
                kind=ItemKind.EPISODE,
                title=it.title,
                slug=slug,
                season=it.season,
                episode=ep,
                language=it.language,
                quality=it.quality,
                tmdb_id=it.tmdb_id,
            )
            await queue_manager.add(new_item)

        # If not all episodes were in the requested language yet, keep the
        # watch alive as a SeasonWatch so further episodes get picked up.
        if len(available) < len(episodes):
            watch = SeasonWatch(
                source=it.source,
                slug=slug,
                title=it.title,
                season=it.season,
                language=it.language,
                quality=it.quality,
                poster=it.output_path,
                enqueued_episodes=",".join(str(e) for e in sorted(available)),
            )
            async with session_scope() as s:
                s.add(watch)
            log.info(
                "upcoming #%d: %d/%d episodes in %s — remaining ones go into SeasonWatch",
                it.id,
                len(available),
                len(episodes),
                it.language,
            )

        await queue_manager.delete(it.id)
        return

    log.debug("upcoming #%d has unsupported kind %s", it.id, it.kind)


# ============================================================================
# Season Watchlist
# ============================================================================

async def _probe_season_watch(w: SeasonWatch) -> None:
    """
    Probe a single SeasonWatch row.

    Flow:
      1. If expires_at is in the past, delete the watch.
      2. Otherwise, list_episodes() for the show/season on the source site.
      3. For each episode that's not yet in `enqueued_episodes`, check
         whether the user's requested language is actually available for
         that specific episode (via episode_has_language()).
      4. If yes → spawn an EPISODE QueueItem + mark the episode as enqueued.
      5. If no  → leave for the next tick.
    """
    now = utcnow()

    # 1) Expiry check
    if w.expires_at and w.expires_at <= now:
        async with session_scope() as s:
            row = await s.get(SeasonWatch, w.id)
            if row:
                await s.delete(row)
        log.info(
            "season watch #%s (%s S%s) expired and removed",
            w.id,
            w.title,
            w.season,
        )
        return

    # 2) List episodes on the source
    if w.source not in (ItemSource.STO, ItemSource.ANIWORLD):
        log.warning("season watch #%s has unsupported source %s", w.id, w.source)
        return

    scraper = get_scraper(w.source)
    if not isinstance(scraper, (StoScraper, AniworldScraper)):
        return

    try:
        episodes = await scraper.list_episodes(w.slug, w.season)
    except Exception as e:
        await _update_watch(w.id, last_message=f"list_episodes failed: {e}"[:250])
        return

    if not episodes:
        await _update_watch(
            w.id,
            last_message=f"no episodes yet on {w.source.value}",
            last_checked=now,
        )
        return

    already_done = w.enqueued_set()
    new_candidates = [ep for ep in episodes if ep not in already_done]

    if not new_candidates:
        await _update_watch(
            w.id,
            last_checked=now,
            last_message=f"up to date ({len(episodes)} episodes tracked)",
        )
        return

    spawned = 0
    language_missing = 0
    newly_enqueued: set[int] = set()

    for ep in new_candidates:
        try:
            has_lang = await scraper.episode_has_language(
                w.slug, w.season, ep, w.language
            )
        except Exception as e:
            log.warning(
                "season watch #%s: language check E%02d failed: %s",
                w.id,
                ep,
                e,
            )
            continue

        if not has_lang:
            language_missing += 1
            continue

        # Spawn a real queue item for this episode
        new_item = QueueItem(
            source=w.source,
            kind=ItemKind.EPISODE,
            title=w.title,
            slug=w.slug,
            season=w.season,
            episode=ep,
            language=w.language,
            quality=w.quality,
            message=f"auto-queued from watchlist #{w.id}",
        )
        await queue_manager.add(new_item)
        newly_enqueued.add(ep)
        spawned += 1
        log.info(
            "season watch #%s: E%02d (%s) now available in %s → enqueued",
            w.id,
            ep,
            w.title,
            w.language,
        )

    # Persist the updated enqueued set
    if newly_enqueued:
        async with session_scope() as s:
            row = await s.get(SeasonWatch, w.id)
            if row:
                cur = row.enqueued_set() | newly_enqueued
                row.enqueued_episodes = ",".join(str(e) for e in sorted(cur))
                row.last_checked = now
                row.last_message = (
                    f"enqueued {spawned} new episode(s)"
                    + (
                        f", {language_missing} still waiting on {w.language}"
                        if language_missing
                        else ""
                    )
                )
                s.add(row)
    else:
        msg = f"no new episodes in {w.language}"
        if language_missing:
            msg = f"{language_missing} episode(s) not yet in {w.language}"
        await _update_watch(w.id, last_checked=now, last_message=msg)


async def _update_watch(
    watch_id: int,
    *,
    last_checked: datetime | None = None,
    last_message: str | None = None,
) -> None:
    async with session_scope() as s:
        row = await s.get(SeasonWatch, watch_id)
        if not row:
            return
        if last_checked is not None:
            row.last_checked = last_checked
        if last_message is not None:
            row.last_message = last_message[:250]
        s.add(row)
