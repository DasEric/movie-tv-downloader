"""Settings endpoints — runtime-mutable app config."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.api.schemas import SettingsUpdateRequest
from app.queue_manager import queue_manager
from app.services import settings_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])

_SECRET_KEYS = (
    "tmdb_api_key",
    "discord_webhook_url",
    "telegram_bot_token",
    "discord_token",
)

_DISCORD_KEYS = {
    "discord_bot_enabled",
    "discord_mode",
    "discord_token",
    "discord_owner_id",
    "discord_upload_channel_id",
    "discord_request_role_id",
    "discord_guild_id",
}


def _mask(d: dict) -> dict:
    for k in _SECRET_KEYS:
        if d.get(k):
            d[k + "_set"] = True
            d[k] = "•" * 8
        else:
            d[k + "_set"] = False
    return d


@router.get("")
async def get_settings():
    all_ = await settings_store.get_all()
    return _mask(all_)


@router.put("")
async def update_settings(req: SettingsUpdateRequest):
    payload = {k: v for k, v in req.model_dump().items() if v is not None}
    # Don't overwrite secrets with masked placeholder
    for sk in _SECRET_KEYS:
        val = payload.get(sk)
        if isinstance(val, str) and val.startswith("•"):
            payload.pop(sk, None)

    new = await settings_store.set_many(payload)

    if "concurrency" in payload:
        await queue_manager.set_concurrency(int(payload["concurrency"]))

    # Refresh the per-source path cache so the library scanner sees new roots.
    if "source_paths" in payload:
        try:
            from app.services import paths

            await paths.refresh()
        except Exception as e:
            log.warning("path cache refresh after settings update failed: %s", e)

    # Hot-reload the Discord bot when any of its settings changed.
    if _DISCORD_KEYS & set(payload):
        try:
            from app.services.discord_bot import runner as discord_runner

            await discord_runner.reconcile()
        except Exception as e:
            log.warning("discord bot reconcile after settings update failed: %s", e)

    return _mask(new)
