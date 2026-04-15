"""
Central configuration.

Values are read from environment variables (docker-compose injects them)
with sensible defaults. Settings that the user can change at runtime via the
UI (concurrency, hoster priority, quality, credentials) are persisted to
SQLite and override env values — see app/services/settings_store.py.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only point pydantic at a .env file when one actually exists in the cwd —
# inside the Docker container we don't ship a .env (env vars are passed
# directly by docker-compose), and on some pydantic-settings versions a
# missing env_file emits noisy warnings on every Settings() instantiation.
_ENV_FILE = ".env" if os.path.isfile(".env") else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # --- paths ---
    movies_path: Path = Path("/movies")
    tv_path: Path = Path("/tv")
    config_path: Path = Path("/config")
    tmp_path: Path = Path("/tmp/h0melab")
    static_path: Path = Path(__file__).parent / "static"

    # --- runtime ---
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    timezone: str = Field("Europe/Berlin", alias="TZ")
    concurrency: int = Field(3, alias="CONCURRENCY")
    default_language: str = Field("de", alias="DEFAULT_LANGUAGE")
    quality_profile: str = Field("1080p", alias="QUALITY_PROFILE")
    hoster_priority: str = Field(
        "VOE,Vidmoly,Vidoza,Doodstream", alias="HOSTER_PRIORITY"
    )
    homelab_credit: bool = Field(True, alias="HOMELAB_CREDIT")

    # --- api keys / secrets ---
    tmdb_api_key: str | None = Field(None, alias="TMDB_API_KEY")
    discord_webhook_url: str | None = Field(None, alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str | None = Field(None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(None, alias="TELEGRAM_CHAT_ID")

    # --- discord bot (env fallbacks — UI overrides once persisted) ---
    discord_token: str | None = Field(None, alias="DISCORD_TOKEN")
    discord_owner_id: str | None = Field(None, alias="DISCORD_OWNER_ID")
    discord_upload_channel_id: str | None = Field(
        None, alias="DISCORD_UPLOAD_CHANNEL_ID"
    )
    discord_request_role_id: str | None = Field(
        None, alias="DISCORD_REQUEST_ROLE_ID"
    )
    discord_guild_id: str | None = Field(None, alias="DISCORD_GUILD_ID")

    # --- networking ---
    proxy_url: str | None = Field(None, alias="PROXY_URL")
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # --- server ---
    host: str = Field("0.0.0.0", alias="H0MELAB_HOST")
    port: int = Field(3000, alias="H0MELAB_PORT")

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.config_path}/h0melab.db"

    @property
    def hoster_priority_list(self) -> list[str]:
        return [h.strip() for h in self.hoster_priority.split(",") if h.strip()]

    def ensure_dirs(self) -> None:
        # Stdlib logger — get_settings() runs before our setup_logging(),
        # so any warnings here go to the default stderr handler.
        import logging

        log = logging.getLogger(__name__)
        for p in (
            self.movies_path,
            self.tv_path,
            self.config_path,
            self.tmp_path,
        ):
            try:
                p.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                log.warning(
                    "could not create %s (%s) — fix the volume mount "
                    "permissions or use a named volume",
                    p,
                    e,
                )
            except OSError as e:
                log.warning("could not create %s: %s", p, e)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
