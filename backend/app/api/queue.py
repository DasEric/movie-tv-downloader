"""Queue REST endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.api.schemas import (
    AddItemRequest,
    AddSeasonRequest,
    BulkAddEpisodesRequest,
    ReorderRequest,
)
from app.models import ItemKind, ItemStatus, QueueItem
from app.queue_manager import queue_manager
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.sto import StoScraper

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("")
async def list_queue():
    items = await queue_manager.list_items()
    return [i.model_dump(mode="json") for i in items]


@router.post("")
async def add_item(req: AddItemRequest):
    item = QueueItem(
        source=req.source,
        kind=req.kind,
        title=req.title,
        url=req.url,
        slug=req.slug,
        season=req.season,
        episode=req.episode,
        language=req.language,
        quality=req.quality,
        priority=req.priority,
        tmdb_id=req.tmdb_id,
        auto_download=req.auto_download,
    )
    saved = await queue_manager.add(item)
    return saved.model_dump(mode="json")


@router.post("/bulk-episodes")
async def add_bulk_episodes(req: BulkAddEpisodesRequest):
    created = []
    for ep in req.episodes:
        item = QueueItem(
            source=req.source,
            kind=ItemKind.EPISODE,
            title=req.title,
            slug=req.slug,
            season=req.season,
            episode=ep,
            language=req.language,
            quality=req.quality,
            tmdb_id=req.tmdb_id,
        )
        saved = await queue_manager.add(item)
        created.append(saved.model_dump(mode="json"))
    return created


@router.post("/season")
async def add_full_season(req: AddSeasonRequest):
    scraper = get_scraper(req.source)
    if not isinstance(scraper, (StoScraper, AniworldScraper)):
        raise HTTPException(400, "source does not support seasons")
    try:
        eps = await scraper.list_episodes(req.slug, req.season)
    except Exception as e:
        raise HTTPException(502, f"failed to list episodes: {e}")
    if not eps:
        raise HTTPException(
            404,
            f"no episodes found for {req.slug} season {req.season} — "
            "wrong slug or season number?",
        )
    # Check language availability concurrently (bounded to 5 parallel).
    sem = asyncio.Semaphore(5)

    async def check(ep: int) -> bool:
        async with sem:
            try:
                return await scraper.episode_has_language(
                    req.slug, req.season, ep, req.language
                )
            except Exception:
                return False

    checks = await asyncio.gather(*[check(ep) for ep in eps])
    available = [ep for ep, ok in zip(eps, checks) if ok]
    skipped = [ep for ep, ok in zip(eps, checks) if not ok]
    created = []
    for ep in available:
        item = QueueItem(
            source=req.source,
            kind=ItemKind.EPISODE,
            title=req.title,
            slug=req.slug,
            season=req.season,
            episode=ep,
            language=req.language,
            quality=req.quality,
        )
        saved = await queue_manager.add(item)
        created.append(saved.model_dump(mode="json"))
    return {
        "count": len(created),
        "items": created,
        "skipped": len(skipped),
        "total": len(eps),
    }


@router.delete("/{item_id}")
async def remove_item(item_id: int):
    ok = await queue_manager.delete(item_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.post("/{item_id}/pause")
async def pause_item(item_id: int):
    await queue_manager.pause(item_id)
    return {"ok": True}


@router.post("/{item_id}/resume")
async def resume_item(item_id: int):
    await queue_manager.resume(item_id)
    return {"ok": True}


@router.post("/{item_id}/retry")
async def retry_item(item_id: int):
    await queue_manager.update(
        item_id, status=ItemStatus.QUEUED, progress=0.0, message="retrying"
    )
    queue_manager._wake.set()
    return {"ok": True}


@router.post("/reorder")
async def reorder(req: ReorderRequest):
    await queue_manager.reorder(req.order)
    return {"ok": True}


@router.post("/retry-all")
async def retry_all():
    """Re-queue all failed and rate-limited (paused with [RATE_LIMITED]) items."""
    items = await queue_manager.list_items()
    count = 0
    for it in items:
        if it.id is None:
            continue
        is_failed = it.status == ItemStatus.FAILED
        is_rate_limited = (
            it.status == ItemStatus.PAUSED
            and it.message
            and it.message.startswith("[RATE_LIMITED]")
        )
        if is_failed or is_rate_limited:
            await queue_manager.update(
                it.id, status=ItemStatus.QUEUED, progress=0.0, message="retrying"
            )
            count += 1
    if count:
        queue_manager._wake.set()
    return {"retried": count}


@router.post("/pause-all")
async def pause_all():
    count = await queue_manager.pause_all()
    return {"paused": count}


@router.post("/resume-all")
async def resume_all():
    count = await queue_manager.resume_all()
    return {"resumed": count}


@router.post("/stop-all")
async def stop_all():
    count = await queue_manager.stop_all()
    return {"stopped": count}


@router.delete("")
async def clear_completed():
    items = await queue_manager.list_items()
    removed = 0
    for i in items:
        if i.status in (ItemStatus.COMPLETED, ItemStatus.FAILED) and i.id is not None:
            await queue_manager.delete(i.id)
            removed += 1
    return {"removed": removed}
