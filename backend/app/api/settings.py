"""Settings endpoints — runtime-mutable app config."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.schemas import SettingsUpdateRequest
from app.queue_manager import queue_manager
from app.services import settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    all_ = await settings_store.get_all()
    # redact secrets in the GET response
    for k in ("tmdb_api_key", "discord_webhook_url", "telegram_bot_token"):
        if all_.get(k):
            all_[k + "_set"] = True
            all_[k] = "•" * 8
        else:
            all_[k + "_set"] = False
    return all_


@router.put("")
async def update_settings(req: SettingsUpdateRequest):
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    # Don't overwrite secrets with masked placeholder
    for sk in ("tmdb_api_key", "discord_webhook_url", "telegram_bot_token"):
        if payload.get(sk, "").startswith("•"):
            payload.pop(sk, None)
    new = await settings_store.set_many(payload)
    if "concurrency" in payload:
        await queue_manager.set_concurrency(int(payload["concurrency"]))
    # mask on response
    for k in ("tmdb_api_key", "discord_webhook_url", "telegram_bot_token"):
        if new.get(k):
            new[k + "_set"] = True
            new[k] = "•" * 8
        else:
            new[k + "_set"] = False
    return new
