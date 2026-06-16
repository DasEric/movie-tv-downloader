"""
Decision + enqueue logic for Discord bot requests.

`decide()` takes a TMDB id + media type and classifies the request into
one of four paths (already complete / new season / download full / not
found). `enqueue_from_request()` then turns that decision into concrete
QueueItem rows, attaching the requester's Discord context so the worker
can notify them on completion.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Optional
from urllib.parse import urlparse

from app.api.library import check_movie, get_existing_episodes
from app.models import ItemKind, ItemSource, QueueItem
from app.queue_manager import queue_manager
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.megakino import MegakinoScraper
from app.scrapers.sto import StoScraper
from app.scrapers.burningseries import BurningSeriesScraper
from app.scrapers.kinox import KinoxScraper
from app.services import settings_store, tmdb

log = logging.getLogger(__name__)


class RequestPath(str, Enum):
    ALREADY_COMPLETE = "already_complete"
    NEW_SEASON = "new_season"
    DOWNLOAD_FULL = "download_full"
    NOT_FOUND = "not_found"


@dataclass
class RequestDecision:
    path: RequestPath
    title: str
    tmdb_id: int
    media_type: Literal["movie", "series"]
    year: Optional[int] = None
    overview: Optional[str] = None
    poster_url: Optional[str] = None
    rating: Optional[float] = None
    # Scraper hit (when path is NEW_SEASON or DOWNLOAD_FULL)
    source: Optional[ItemSource] = None
    slug: Optional[str] = None
    url: Optional[str] = None  # movie URL on the scraper site
    seasons_to_download: list[int] = field(default_factory=list)
    # Context for the UI
    existing_seasons: list[int] = field(default_factory=list)


# --- slug extraction helpers ---

_STO_SLUG = re.compile(r"/serie/stream/([^/?#]+)")
_ANIWORLD_SLUG = re.compile(r"/anime/stream/([^/?#]+)")


def _slug_from_sto(url: str) -> Optional[str]:
    m = _STO_SLUG.search(urlparse(url).path)
    return m.group(1) if m else None


def _slug_from_aniworld(url: str) -> Optional[str]:
    m = _ANIWORLD_SLUG.search(urlparse(url).path)
    return m.group(1) if m else None


# --- TMDB -> scraper resolution ---

async def _resolve_movie_scraper(title: str, year: int | None) -> Optional[tuple[ItemSource, str, str]]:
    """Returns (source, url, matched_title) for the first megakino hit."""
    scraper = MegakinoScraper()
    try:
        hits = await scraper.search(title)
    except Exception as e:
        log.info("megakino search for %r failed: %s", title, e)
        return None
    if not hits:
        return None
    # Prefer year match when we have one; otherwise first.
    best = hits[0]
    if year:
        for h in hits:
            if h.year == year:
                best = h
                break
    return (ItemSource.MEGAKINO, best.url, best.title)


async def _resolve_series_scraper(
    title: str,
) -> Optional[tuple[ItemSource, str, list[int]]]:
    """Try s.to first, then aniworld. Returns (source, slug, seasons)."""
    # s.to
    try:
        sto = StoScraper()
        hits = await sto.search(title)
        if hits:
            slug = _slug_from_sto(hits[0].url)
            if slug:
                seasons = await sto.list_seasons(slug)
                if seasons:
                    return (ItemSource.STO, slug, seasons)
    except Exception as e:
        log.info("s.to resolve for %r failed: %s", title, e)

    # aniworld fallback
    try:
        ani = AniworldScraper()
        hits = await ani.search(title)
        if hits:
            slug = _slug_from_aniworld(hits[0].url) or hits[0].url.rstrip("/").rsplit("/", 1)[-1]
            if slug:
                seasons = await ani.list_seasons(slug)
                if seasons:
                    return (ItemSource.ANIWORLD, slug, seasons)
    except Exception as e:
        log.info("aniworld resolve for %r failed: %s", title, e)

    return None


# --- public: decide ---

async def decide(tmdb_id: int, media_type: str) -> RequestDecision:
    """Classify a request into one of the four RequestPath outcomes.

    media_type must be "movie" or "series"/"tv".
    """
    mt: Literal["movie", "series"] = "movie" if media_type == "movie" else "series"

    if mt == "movie":
        details = await tmdb.details_movie(tmdb_id)
        if not details:
            # TMDB lookup failed — treat as NOT_FOUND so the owner gets
            # a manual-upload path instead of a crash.
            return RequestDecision(
                path=RequestPath.NOT_FOUND,
                title=f"TMDB #{tmdb_id}",
                tmdb_id=tmdb_id,
                media_type="movie",
            )
        title = details["title"]
        year_str = details.get("year") or "0000"
        try:
            year = int(year_str) if year_str and year_str != "0000" else None
        except ValueError:
            year = None

        # Already on disk?
        existing = await check_movie(title=title, year=year)
        if existing.get("found"):
            return RequestDecision(
                path=RequestPath.ALREADY_COMPLETE,
                title=title,
                tmdb_id=tmdb_id,
                media_type="movie",
                year=year,
                overview=details.get("overview"),
                poster_url=details.get("poster"),
                rating=details.get("rating"),
            )

        # Scraper hit?
        hit = await _resolve_movie_scraper(title, year)
        if hit is None:
            return RequestDecision(
                path=RequestPath.NOT_FOUND,
                title=title,
                tmdb_id=tmdb_id,
                media_type="movie",
                year=year,
                overview=details.get("overview"),
                poster_url=details.get("poster"),
                rating=details.get("rating"),
            )
        source, url, _matched = hit
        return RequestDecision(
            path=RequestPath.DOWNLOAD_FULL,
            title=title,
            tmdb_id=tmdb_id,
            media_type="movie",
            year=year,
            overview=details.get("overview"),
            poster_url=details.get("poster"),
            rating=details.get("rating"),
            source=source,
            url=url,
        )

    # --- series ---
    details = await tmdb.details_tv(tmdb_id)
    if not details:
        return RequestDecision(
            path=RequestPath.NOT_FOUND,
            title=f"TMDB #{tmdb_id}",
            tmdb_id=tmdb_id,
            media_type="series",
        )
    title = details["title"]
    year_str = details.get("year") or "0000"
    try:
        year = int(year_str) if year_str and year_str != "0000" else None
    except ValueError:
        year = None

    existing_map = get_existing_episodes(title)
    existing_seasons = sorted(existing_map.keys())

    resolved = await _resolve_series_scraper(title)
    if resolved is None:
        # No scraper hit. If we have nothing on disk either → NOT_FOUND.
        # If we have partial content on disk → ALREADY_COMPLETE (we
        # can't tell what's "latest" without a scraper).
        if existing_seasons:
            return RequestDecision(
                path=RequestPath.ALREADY_COMPLETE,
                title=title,
                tmdb_id=tmdb_id,
                media_type="series",
                year=year,
                overview=details.get("overview"),
                poster_url=details.get("poster"),
                rating=details.get("rating"),
                existing_seasons=existing_seasons,
            )
        return RequestDecision(
            path=RequestPath.NOT_FOUND,
            title=title,
            tmdb_id=tmdb_id,
            media_type="series",
            year=year,
            overview=details.get("overview"),
            poster_url=details.get("poster"),
            rating=details.get("rating"),
        )
    source, slug, scraper_seasons = resolved

    missing = sorted(set(scraper_seasons) - set(existing_seasons))
    if not missing:
        return RequestDecision(
            path=RequestPath.ALREADY_COMPLETE,
            title=title,
            tmdb_id=tmdb_id,
            media_type="series",
            year=year,
            overview=details.get("overview"),
            poster_url=details.get("poster"),
            rating=details.get("rating"),
            source=source,
            slug=slug,
            existing_seasons=existing_seasons,
        )

    path = RequestPath.NEW_SEASON if existing_seasons else RequestPath.DOWNLOAD_FULL
    return RequestDecision(
        path=path,
        title=title,
        tmdb_id=tmdb_id,
        media_type="series",
        year=year,
        overview=details.get("overview"),
        poster_url=details.get("poster"),
        rating=details.get("rating"),
        source=source,
        slug=slug,
        seasons_to_download=missing,
        existing_seasons=existing_seasons,
    )


# --- public: enqueue ---

async def enqueue_from_request(
    decision: RequestDecision,
    *,
    requester_id: str,
    guild_id: str,
    channel_id: str,
    owner_msg_id: Optional[str] = None,
    language: Optional[str] = None,
    quality: Optional[str] = None,
) -> list[QueueItem]:
    """Turn a decision into one or more QueueItems, tagged with Discord
    context for completion callbacks."""
    if decision.path not in (RequestPath.DOWNLOAD_FULL, RequestPath.NEW_SEASON):
        return []

    lang = language or await settings_store.get("default_language", "de")
    qual = quality or await settings_store.get("quality_profile", "1080p")

    discord_fields = dict(
        discord_requester_id=str(requester_id),
        discord_guild_id=str(guild_id) if guild_id else None,
        discord_channel_id=str(channel_id) if channel_id else None,
        discord_owner_msg_id=str(owner_msg_id) if owner_msg_id else None,
    )

    # ---- movie ----
    if decision.media_type == "movie":
        if not decision.source or not decision.url:
            return []
        item = QueueItem(
            source=decision.source,
            kind=ItemKind.MOVIE,
            title=decision.title,
            url=decision.url,
            language=str(lang),
            quality=str(qual),
            tmdb_id=decision.tmdb_id,
            discord_notify_kind="movie",
            **discord_fields,
        )
        saved = await queue_manager.add(item)
        return [saved]

    # ---- series ----
    if not decision.source or not decision.slug or not decision.seasons_to_download:
        return []
    scraper = get_scraper(decision.source)
    if not isinstance(scraper, (StoScraper, AniworldScraper, BurningSeriesScraper, KinoxScraper)):
        return []

    # "new_season" when the library already had something; "full_series"
    # when we're downloading everything.
    notify_kind = (
        "new_season" if decision.path is RequestPath.NEW_SEASON else "full_series"
    )

    sem = asyncio.Semaphore(5)
    created: list[QueueItem] = []
    existing_map = get_existing_episodes(decision.title)

    for sn in decision.seasons_to_download:
        try:
            eps = await scraper.list_episodes(decision.slug, sn)
        except Exception as e:
            log.info(
                "list_episodes(%r, %s) failed: %s", decision.slug, sn, e
            )
            continue

        on_disk = set(existing_map.get(sn, []))
        eps = [e for e in eps if e not in on_disk]
        if not eps:
            continue

        async def _check(ep: int, season: int = sn) -> bool:
            async with sem:
                try:
                    return await scraper.episode_has_language(
                        decision.slug, season, ep, str(lang)
                    )
                except Exception:
                    return False

        checks = await asyncio.gather(*[_check(e) for e in eps])
        available = [e for e, ok in zip(eps, checks) if ok]

        for ep in available:
            item = QueueItem(
                source=decision.source,
                kind=ItemKind.EPISODE,
                title=decision.title,
                slug=decision.slug,
                season=sn,
                episode=ep,
                language=str(lang),
                quality=str(qual),
                tmdb_id=decision.tmdb_id,
                discord_notify_kind=notify_kind,
                **discord_fields,
            )
            saved = await queue_manager.add(item)
            created.append(saved)

    return created
