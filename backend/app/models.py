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
    FILMPALAST = "filmpalast.to"
    BURNING_SERIES = "burning-series.io"
    KINOX = "kinox.to"


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
    # --- discord bot context (filled when request originates from the bot) ---
    discord_requester_id: Optional[str] = None
    discord_guild_id: Optional[str] = None
    discord_channel_id: Optional[str] = None
    discord_owner_msg_id: Optional[str] = None
    # "movie" | "full_series" | "new_season" | "season" — controls which embed
    # template the completion notifier posts in the upload channel.
    discord_notify_kind: Optional[str] = None


class AppSetting(SQLModel, table=True):
    __tablename__ = "app_settings"

    key: str = Field(primary_key=True)
    value: str


class SeasonWatch(SQLModel, table=True):
    """
    A "watch this season for new episodes" entry.

    Unlike the Upcoming tab (which waits for a show/movie to appear at all),
    a SeasonWatch tracks an ongoing season where episodes trickle in over
    time — typically with a language delay (the German dub for E07 comes
    out 2 weeks after the English original).

    The scheduler walks every SeasonWatch row on every tick:
      1. list_episodes(slug, season) on the source site
      2. For each episode number NOT yet in `enqueued_episodes`:
         a. Check if the requested `language` is actually available for that
            specific episode (via episode_has_language()).
         b. If yes → spawn an EPISODE QueueItem + add to enqueued_episodes.
         c. If no → leave for the next tick (language may still arrive).
      3. If `expires_at` is in the past, delete the row.
    """

    __tablename__ = "season_watches"

    id: Optional[int] = Field(default=None, primary_key=True)
    source: ItemSource       # only STO / ANIWORLD (megakino has no seasons)
    slug: str                # show slug on the source site
    title: str
    season: int
    language: str = "de"
    quality: str = "1080p"
    poster: Optional[str] = None
    expires_at: Optional[datetime] = None   # None = forever
    # Comma-separated list of episode numbers that have already been
    # spawned into the queue. Stored as a string because SQLite JSON
    # columns are fiddly and this list is always small (< 100 entries).
    enqueued_episodes: str = ""
    last_checked: Optional[datetime] = None
    last_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)

    def enqueued_set(self) -> set[int]:
        return {int(x) for x in self.enqueued_episodes.split(",") if x.strip().isdigit()}

    def mark_enqueued(self, episode: int) -> None:
        cur = self.enqueued_set()
        cur.add(episode)
        self.enqueued_episodes = ",".join(str(e) for e in sorted(cur))
