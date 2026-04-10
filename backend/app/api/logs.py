"""Log viewer endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Query

from app import logger as app_logger

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(limit: int = Query(500, ge=1, le=2000)):
    return app_logger.recent_logs(limit)
