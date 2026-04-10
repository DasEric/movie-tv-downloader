"""
Runtime-mutable settings stored in SQLite. Overrides env defaults when the
user changes them via the UI. Keys mirror the public API fields.
"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import select

from app.config import settings as env_settings
from app.db import session_scope
from app.models import AppSetting

DEFAULT_KEYS: dict[str, Any] = {
    "concurrency": lambda: env_settings.concurrency,
    "default_language": lambda: env_settings.default_language,
    "quality_profile": lambda: env_settings.quality_profile,
    "hoster_priority": lambda: env_settings.hoster_priority_list,
    "tmdb_api_key": lambda: env_settings.tmdb_api_key or "",
    "discord_webhook_url": lambda: env_settings.discord_webhook_url or "",
    "telegram_bot_token": lambda: env_settings.telegram_bot_token or "",
    "telegram_chat_id": lambda: env_settings.telegram_chat_id or "",
    "proxy_url": lambda: env_settings.proxy_url or "",
    "subtitle_languages": lambda: ["de", "en"],
    "auto_subtitles": lambda: True,
    "release_check_interval_min": lambda: 60,
}


async def get_all() -> dict[str, Any]:
    out: dict[str, Any] = {k: f() for k, f in DEFAULT_KEYS.items()}
    async with session_scope() as s:
        rows = (await s.execute(select(AppSetting))).scalars().all()
        for r in rows:
            try:
                out[r.key] = json.loads(r.value)
            except Exception:
                out[r.key] = r.value
    return out


async def get(key: str, default: Any = None) -> Any:
    all_ = await get_all()
    return all_.get(key, default)


async def set_many(values: dict[str, Any]) -> dict[str, Any]:
    async with session_scope() as s:
        for k, v in values.items():
            if k not in DEFAULT_KEYS:
                continue
            existing = (
                await s.execute(select(AppSetting).where(AppSetting.key == k))
            ).scalar_one_or_none()
            encoded = json.dumps(v)
            if existing:
                existing.value = encoded
                s.add(existing)
            else:
                s.add(AppSetting(key=k, value=encoded))
    return await get_all()
