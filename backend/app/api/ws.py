"""
WebSocket endpoints:
  /api/ws/events  — queue change events (add / update / remove / reorder)
  /api/ws/logs    — live log tail
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import logger as app_logger
from app.queue_manager import queue_manager
from app.services import events

log = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/api/ws/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    q = events.subscribe()

    # Send initial snapshot
    try:
        items = await queue_manager.list_items()
        await ws.send_text(
            json.dumps(
                {
                    "event": "queue.snapshot",
                    "data": [i.model_dump(mode="json") for i in items],
                }
            )
        )
    except Exception:
        log.exception("ws snapshot failed")

    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=30)
                await ws.send_text(json.dumps(payload, default=str))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"event": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws events closed: %s", e)
    finally:
        events.unsubscribe(q)


@router.websocket("/api/ws/logs")
async def ws_logs(ws: WebSocket) -> None:
    await ws.accept()
    q = app_logger.subscribe()

    try:
        for entry in app_logger.recent_logs(200):
            await ws.send_text(json.dumps(entry))
    except Exception:
        pass

    try:
        while True:
            try:
                entry = await asyncio.wait_for(q.get(), timeout=30)
                await ws.send_text(json.dumps(entry))
            except asyncio.TimeoutError:
                await ws.send_text(json.dumps({"event": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("ws logs closed: %s", e)
    finally:
        app_logger.unsubscribe(q)
