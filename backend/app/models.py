"""SQLModel tables for queue + settings persistence."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """
    Timezone-naive UTC `datetime` (SQLite has no tz storage).
    Replaces the deprecated `datetime.utcnow()` on Python 3.12+.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ItemStatus(str, Enum):
    QUEUED = "queued"
    SCRAPING = "scraping"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    WAITING_RELEASE = "waiting_release"


class ItemKind(str, Enum):
    MOVIE = "movie"
    EPISODE = "episode"
    SEASON = "season"  # expands into episodes on enqueue


class ItemSource(str, Enum):
    STO = "s.to"
    ANIWORLD = "aniworld"
    MEGAKINO = "megakino"


class QueueItem(SQLModel, table=True):
    __tablename__ = "queue_items"

    id: Optional[int] = Field(default=None, primary_key=True)
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
    order_index: int = 0
    status: ItemStatus = Field(default=ItemStatus.QUEUED)
    progress: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    current_hoster: Optional[str] = None
    message: Optional[str] = None
    tmdb_id: Optional[int] = None
    release_date: Optional[datetime] = None
    auto_download: bool = False
    output_path: Optional[str] = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str
