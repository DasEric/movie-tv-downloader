"""
In-process async queue manager.

This is intentionally simple: asyncio semaphore for concurrency, a persistent
SQLite backing store, and a pub/sub for UI updates. No Celery, no Redis —
fits the "single container" constraint and avoids the reference-repo stall
bug entirely because there is no multi-process handoff.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlmodel import select

from app.db import session_scope
from app.models import ItemKind, ItemStatus, QueueItem, utcnow
from app.services import events, settings_store

log = logging.getLogger(__name__)


class QueueManager:
    def __init__(self) -> None:
        self._sem: asyncio.Semaphore | None = None
        self._wake = asyncio.Event()
        self._running: dict[int, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None
        self._stopped = False
        self._current_concurrency = 3

    # ---------- lifecycle ----------

    async def start(self) -> None:
        self._current_concurrency = int(
            await settings_store.get("concurrency", 3)
        )
        self._sem = asyncio.Semaphore(self._current_concurrency)
        self._loop_task = asyncio.create_task(self._run_loop())
        log.info(
            "queue manager started (concurrency=%d)", self._current_concurrency
        )
        await self._requeue_interrupted()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._loop_task:
            self._loop_task.cancel()
        for t in list(self._running.values()):
            t.cancel()

    async def _requeue_interrupted(self) -> None:
        """Any item stuck in SCRAPING/DOWNLOADING/PROCESSING from a prior run
        is rolled back to QUEUED so the main loop picks it up."""
        # Use enum members so the SQLAlchemy Enum column serialises them
        # consistently (same style as the comparison in `_run_loop`).
        interrupted = [
            ItemStatus.SCRAPING,
            ItemStatus.DOWNLOADING,
            ItemStatus.PROCESSING,
        ]
        async with session_scope() as s:
            items = (
                await s.execute(
                    select(QueueItem).where(QueueItem.status.in_(interrupted))
                )
            ).scalars().all()
            for it in items:
                it.status = ItemStatus.QUEUED
                it.progress = 0.0
                it.message = "resumed after restart"
                s.add(it)
        self._wake.set()

    # ---------- CRUD ----------

    async def add(self, item: QueueItem) -> QueueItem:
        async with session_scope() as s:
            # Duplicate check: skip if an identical item is already
            # queued / in progress (not completed or failed).
            dup = await self._find_duplicate(s, item)
            if dup is not None:
                log.debug("skipping duplicate of queue item %d", dup.id)
                return dup

            max_order = (
                await s.execute(
                    select(QueueItem.order_index).order_by(QueueItem.order_index.desc())
                )
            ).scalars().first() or 0
            item.order_index = max_order + 1
            s.add(item)
            await s.flush()
            await s.refresh(item)
        events.publish("queue.added", item.model_dump(mode="json"))
        self._wake.set()
        return item

    @staticmethod
    async def _find_duplicate(s, item: QueueItem) -> QueueItem | None:
        active_statuses = [
            ItemStatus.QUEUED,
            ItemStatus.SCRAPING,
            ItemStatus.DOWNLOADING,
            ItemStatus.PROCESSING,
            ItemStatus.PAUSED,
            ItemStatus.WAITING_RELEASE,
        ]
        q = (
            select(QueueItem)
            .where(
                QueueItem.source == item.source,
                QueueItem.kind == item.kind,
                QueueItem.status.in_(active_statuses),
            )
        )
        if item.kind == ItemKind.EPISODE:
            q = q.where(
                QueueItem.slug == item.slug,
                QueueItem.season == item.season,
                QueueItem.episode == item.episode,
            )
        elif item.kind == ItemKind.MOVIE:
            q = q.where(QueueItem.title == item.title)
        else:
            return None
        result = (await s.execute(q.limit(1))).scalars().first()
        return result

    async def queued_episodes(self, slug: str, season: int) -> set[int]:
        """Return episode numbers already in the queue (active states)."""
        active = [
            ItemStatus.QUEUED, ItemStatus.SCRAPING, ItemStatus.DOWNLOADING,
            ItemStatus.PROCESSING, ItemStatus.PAUSED, ItemStatus.WAITING_RELEASE,
        ]
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(QueueItem.episode)
                    .where(
                        QueueItem.kind == ItemKind.EPISODE,
                        QueueItem.slug == slug,
                        QueueItem.season == season,
                        QueueItem.status.in_(active),
                        QueueItem.episode.isnot(None),
                    )
                )
            ).scalars().all()
        return {int(e) for e in rows if e is not None}

    async def list_items(self) -> list[QueueItem]:
        async with session_scope() as s:
            return (
                await s.execute(
                    select(QueueItem).order_by(
                        QueueItem.order_index.asc(), QueueItem.id.asc()
                    )
                )
            ).scalars().all()

    async def get(self, item_id: int) -> Optional[QueueItem]:
        async with session_scope() as s:
            return await s.get(QueueItem, item_id)

    async def update(self, item_id: int, **fields) -> Optional[QueueItem]:
        async with session_scope() as s:
            item = await s.get(QueueItem, item_id)
            if not item:
                return None
            for k, v in fields.items():
                setattr(item, k, v)
            item.updated_at = utcnow()
            s.add(item)
            await s.flush()
            await s.refresh(item)
        events.publish("queue.updated", item.model_dump(mode="json"))
        return item

    def _abort_running(self, item_id: int) -> None:
        """
        Signal the downloader thread to stop AND cancel the asyncio task.

        Order matters: we flip the threading.Event FIRST so the executor
        thread sees it at its next progress-hook checkpoint and actually
        stops yt-dlp. task.cancel() alone is not enough because Python
        can't cancel threads from outside — the yt-dlp thread would keep
        running and silently waste bandwidth.
        """
        # Local import to avoid circular dependency (worker imports
        # queue_manager for state updates).
        from app.worker import signal_cancel

        signal_cancel(item_id)
        task = self._running.pop(item_id, None)
        if task:
            task.cancel()

    async def delete(self, item_id: int) -> bool:
        self._abort_running(item_id)
        async with session_scope() as s:
            item = await s.get(QueueItem, item_id)
            if not item:
                return False
            await s.delete(item)
        events.publish("queue.removed", {"id": item_id})
        return True

    async def pause(self, item_id: int) -> None:
        self._abort_running(item_id)
        await self.update(item_id, status=ItemStatus.PAUSED, progress=0.0)

    async def resume(self, item_id: int) -> None:
        await self.update(item_id, status=ItemStatus.QUEUED, message=None)
        self._wake.set()

    # ---- bulk operations ----

    async def pause_all(self) -> int:
        """
        Pause every item that is currently queued or running. In-flight
        downloads are properly aborted (the worker gets CancelledError).
        """
        async with session_scope() as s:
            items = (
                await s.execute(
                    select(QueueItem).where(
                        QueueItem.status.in_(
                            [
                                ItemStatus.QUEUED,
                                ItemStatus.SCRAPING,
                                ItemStatus.DOWNLOADING,
                                ItemStatus.PROCESSING,
                            ]
                        )
                    )
                )
            ).scalars().all()
        count = 0
        for it in items:
            if it.id is None:
                continue
            self._abort_running(it.id)
            await self.update(
                it.id, status=ItemStatus.PAUSED, progress=0.0, message="paused"
            )
            count += 1
        log.info("pause_all: paused %d items", count)
        return count

    async def resume_all(self) -> int:
        """Flip every paused item back to queued."""
        async with session_scope() as s:
            items = (
                await s.execute(
                    select(QueueItem).where(QueueItem.status == ItemStatus.PAUSED)
                )
            ).scalars().all()
        count = 0
        for it in items:
            if it.id is None:
                continue
            await self.update(it.id, status=ItemStatus.QUEUED, message=None)
            count += 1
        self._wake.set()
        log.info("resume_all: resumed %d items", count)
        return count

    async def stop_all(self) -> int:
        """
        Hard-stop every running download without deleting the items.
        They are left in PAUSED state so the user can resume later.
        """
        return await self.pause_all()

    async def reorder(self, order: list[int]) -> None:
        async with session_scope() as s:
            for idx, iid in enumerate(order):
                item = await s.get(QueueItem, iid)
                if item:
                    item.order_index = idx
                    s.add(item)
        events.publish("queue.reordered", {"order": order})

    async def set_concurrency(self, n: int) -> None:
        n = max(1, min(n, 20))
        self._current_concurrency = n
        self._sem = asyncio.Semaphore(n)
        log.info("concurrency changed to %d", n)
        self._wake.set()

    # ---------- main loop ----------

    async def _run_loop(self) -> None:
        from app.worker import process_item  # local import to break cycle

        while not self._stopped:
            self._wake.clear()
            try:
                async with session_scope() as s:
                    next_items = (
                        await s.execute(
                            select(QueueItem)
                            .where(QueueItem.status == ItemStatus.QUEUED)
                            .order_by(
                                QueueItem.priority.desc(),
                                QueueItem.order_index.asc(),
                                QueueItem.id.asc(),
                            )
                            .limit(self._current_concurrency * 2)
                        )
                    ).scalars().all()

                for item in next_items:
                    if item.id in self._running:
                        continue
                    if len(self._running) >= self._current_concurrency:
                        break
                    task = asyncio.create_task(self._wrap_process(item.id, process_item))
                    self._running[item.id] = task
            except Exception:
                log.exception("queue loop error")

            try:
                await asyncio.wait_for(self._wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    async def _wrap_process(self, item_id: int, func) -> None:
        assert self._sem is not None
        async with self._sem:
            try:
                await func(item_id)
            except asyncio.CancelledError:
                log.info("item %d cancelled", item_id)
            except Exception as e:
                log.exception("item %d failed: %s", item_id, e)
                await self.update(
                    item_id,
                    status=ItemStatus.FAILED,
                    message=str(e)[:300],
                )
            finally:
                self._running.pop(item_id, None)
                self._wake.set()


queue_manager = QueueManager()
