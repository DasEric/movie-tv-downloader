"""
Season-watchlist endpoints.

A watchlist entry says "keep watching season N of show X on source Y, and
whenever a new episode shows up in language Z, enqueue it for download".

This differs from the Upcoming tab in that:
  * Upcoming  = wait for the show/movie to appear AT ALL (unreleased content)
  * Watchlist = show IS on the site already, but episodes trickle in
                language-by-language over time (ongoing seasons)

The actual polling is done by `services/scheduler.py::_probe_season_watch`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import select

from app.db import session_scope
from app.models import ItemSource, SeasonWatch, utcnow

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddWatchRequest(BaseModel):
    source: ItemSource              # STO or ANIWORLD
    slug: str
    title: str
    season: int = Field(ge=1)
    language: str = "de"
    quality: str = "1080p"
    poster: Optional[str] = None
    # Expiry — either a concrete ISO date/datetime OR a relative "days"
    # / "months" hint from the UI. Both are optional (no expiry = forever).
    expires_at: Optional[str] = None
    expires_in_days: Optional[int] = None


@router.get("")
async def list_watches():
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(SeasonWatch).order_by(
                    SeasonWatch.created_at.desc(), SeasonWatch.id.desc()
                )
            )
        ).scalars().all()
    return [_serialise(r) for r in rows]


@router.post("")
async def add_watch(req: AddWatchRequest):
    if req.source not in (ItemSource.STO, ItemSource.ANIWORLD, ItemSource.BURNING_SERIES, ItemSource.KINOX):
        raise HTTPException(
            400,
            "Season watchlist only supports s.to, aniworld, burning-series.io and kinox.to.",
        )

    expires_at: Optional[datetime] = None
    if req.expires_at:
        try:
            parsed = datetime.fromisoformat(req.expires_at.replace("Z", "+00:00"))
            # Normalise to naive UTC so comparisons against utcnow() work.
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            expires_at = parsed
        except Exception:
            raise HTTPException(400, f"invalid expires_at: {req.expires_at!r}")
    elif req.expires_in_days and req.expires_in_days > 0:
        expires_at = utcnow() + timedelta(days=req.expires_in_days)

    row = SeasonWatch(
        source=req.source,
        slug=req.slug,
        title=req.title,
        season=req.season,
        language=req.language,
        quality=req.quality,
        poster=req.poster,
        expires_at=expires_at,
    )
    async with session_scope() as s:
        s.add(row)
        await s.flush()
        await s.refresh(row)
    return _serialise(row)


@router.delete("/{watch_id}")
async def delete_watch(watch_id: int):
    async with session_scope() as s:
        row = await s.get(SeasonWatch, watch_id)
        if not row:
            raise HTTPException(404, "not found")
        await s.delete(row)
    return {"ok": True}


@router.post("/{watch_id}/check-now")
async def check_now(watch_id: int):
    """Manually trigger a probe for a single watchlist entry — useful if
    the user doesn't want to wait for the next scheduled tick."""
    async with session_scope() as s:
        row = await s.get(SeasonWatch, watch_id)
        if not row:
            raise HTTPException(404, "not found")
        snapshot = row

    # Local import to avoid circular dependency (scheduler imports api.watchlist nowhere,
    # but scheduler pulls in scrapers which pull in services — keep it tidy).
    from app.services.scheduler import _probe_season_watch

    try:
        await _probe_season_watch(snapshot)
    except Exception as e:
        raise HTTPException(500, f"probe failed: {e}")

    async with session_scope() as s:
        fresh = await s.get(SeasonWatch, watch_id)
        if not fresh:
            return {"removed": True}
        return _serialise(fresh)


def _serialise(w: SeasonWatch) -> dict:
    return {
        "id": w.id,
        "source": w.source.value,
        "slug": w.slug,
        "title": w.title,
        "season": w.season,
        "language": w.language,
        "quality": w.quality,
        "poster": w.poster,
        "expires_at": w.expires_at.isoformat() if w.expires_at else None,
        "last_checked": w.last_checked.isoformat() if w.last_checked else None,
        "last_message": w.last_message,
        "enqueued_episodes": sorted(w.enqueued_set()),
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }
