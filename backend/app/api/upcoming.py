"""
Upcoming (watch-for-release) endpoints.

An "upcoming" item is a QueueItem in status=WAITING_RELEASE. The scheduler
periodically tries to find it on the configured source site (s.to,
aniworld.to, megakino). When it shows up, the scheduler either:

  * turns the item into a QUEUED movie item (with `url` filled in), or
  * expands it into individual episode items for a specific season.

Both happen in `services/scheduler.py::_check_releases`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.db import session_scope
from app.models import ItemKind, ItemSource, ItemStatus, QueueItem
from app.queue_manager import queue_manager
from app.services import settings_store, tmdb

router = APIRouter(prefix="/api/upcoming", tags=["upcoming"])


class AddUpcomingRequest(BaseModel):
    source: ItemSource
    kind: ItemKind  # MOVIE or SEASON (we treat SEASON as "watch for a TV show")
    title: str
    year: Optional[int] = None
    tmdb_id: Optional[int] = None
    release_date: Optional[str] = None  # YYYY-MM-DD
    language: str = "de"
    quality: str = "1080p"
    season: Optional[int] = None  # for TV shows, which season to download when available
    poster: Optional[str] = None


@router.get("")
async def list_upcoming():
    """Return all WAITING_RELEASE items."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(QueueItem)
                .where(QueueItem.status == ItemStatus.WAITING_RELEASE)
                .order_by(QueueItem.release_date.asc(), QueueItem.id.asc())
            )
        ).scalars().all()
    return [r.model_dump(mode="json") for r in rows]


@router.post("")
async def add_upcoming(req: AddUpcomingRequest):
    """Add a new watch-for-release entry. Requires no slug/url yet — the
    scheduler will find it when the source sites publish it."""
    if not await settings_store.get("tmdb_api_key"):
        raise HTTPException(
            412,
            "TMDB API key required — set TMDB_API_KEY in .env or in "
            "Settings → API keys before adding upcoming items.",
        )

    release_dt = None
    if req.release_date:
        from datetime import datetime

        try:
            release_dt = datetime.strptime(req.release_date, "%Y-%m-%d")
        except Exception:
            release_dt = None

    item = QueueItem(
        source=req.source,
        kind=req.kind,
        title=req.title,
        language=req.language,
        quality=req.quality,
        season=req.season,
        tmdb_id=req.tmdb_id,
        release_date=release_dt,
        auto_download=True,
        status=ItemStatus.WAITING_RELEASE,
        message=(
            f"waiting for release"
            + (f" ({req.release_date})" if req.release_date else "")
        ),
        output_path=req.poster,  # stash poster here; UI reads it back
    )
    saved = await queue_manager.add(item)
    return saved.model_dump(mode="json")


@router.delete("/{item_id}")
async def remove_upcoming(item_id: int):
    ok = await queue_manager.delete(item_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


# -- TMDB upcoming search --------------------------------------------------

@router.get("/tmdb/movies")
async def tmdb_upcoming_movies():
    if not await settings_store.get("tmdb_api_key"):
        raise HTTPException(412, "TMDB API key required")
    return await tmdb.upcoming_movies()


@router.get("/tmdb/tv")
async def tmdb_upcoming_tv():
    if not await settings_store.get("tmdb_api_key"):
        raise HTTPException(412, "TMDB API key required")
    return await tmdb.upcoming_tv()


@router.get("/tmdb/search")
async def tmdb_search(q: str, kind: str = "movie"):
    """Manual TMDB search for cases where a user wants a specific movie/show
    that isn't in the "upcoming" feed (e.g. a delayed release, a foreign film)."""
    if not await settings_store.get("tmdb_api_key"):
        raise HTTPException(412, "TMDB API key required")
    if not q.strip():
        return []
    if kind == "tv":
        return await tmdb.search_tv(q.strip())
    return await tmdb.search_movie(q.strip())
