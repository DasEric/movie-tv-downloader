"""Discord bot status endpoint — feeds the Settings page status badge."""
from __future__ import annotations

from fastapi import APIRouter

from app.services.discord_bot import runner as discord_runner

router = APIRouter(prefix="/api/discord", tags=["discord"])


@router.get("/status")
async def get_status() -> dict:
    return discord_runner.status()
