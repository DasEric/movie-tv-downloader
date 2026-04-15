"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.models import ItemKind, ItemSource


class AddItemRequest(BaseModel):
    source: ItemSource
    kind: ItemKind
    title: str
    url: Optional[str] = None
    slug: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    language: str = "de"
    quality: str = "1080p"
    priority: int = 0
    tmdb_id: Optional[int] = None
    auto_download: bool = False


class BulkAddEpisodesRequest(BaseModel):
    source: ItemSource   # s.to or aniworld
    slug: str
    title: str
    season: int
    episodes: list[int]
    language: str = "de"
    quality: str = "1080p"
    tmdb_id: Optional[int] = None


class AddSeasonRequest(BaseModel):
    source: ItemSource
    slug: str
    title: str
    season: int
    language: str = "de"
    quality: str = "1080p"


class AddSeriesRequest(BaseModel):
    source: ItemSource
    slug: str
    title: str
    language: str = "de"
    quality: str = "1080p"


class ReorderRequest(BaseModel):
    order: list[int]


class SettingsUpdateRequest(BaseModel):
    concurrency: Optional[int] = None
    default_language: Optional[str] = None
    quality_profile: Optional[str] = None
    hoster_priority: Optional[list[str]] = None
    tmdb_api_key: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    proxy_url: Optional[str] = None
    subtitle_languages: Optional[list[str]] = None
    auto_subtitles: Optional[bool] = None
    release_check_interval_min: Optional[int] = None
    # --- discord bot ---
    discord_bot_enabled: Optional[bool] = None
    discord_mode: Optional[str] = None  # "standard" | "advanced"
    discord_token: Optional[str] = None
    discord_owner_id: Optional[str] = None
    discord_upload_channel_id: Optional[str] = None
    discord_request_role_id: Optional[str] = None
    discord_guild_id: Optional[str] = None
    # --- cloudflare fallback ---
    cloudflare_fallback_enabled: Optional[bool] = None


class SearchRequest(BaseModel):
    source: ItemSource
    query: str
