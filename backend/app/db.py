"""Async SQLite engine + session helper."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.config import settings

log = logging.getLogger(__name__)

_engine = create_async_engine(settings.db_url, echo=False, future=True)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    # Import models so SQLModel metadata knows about them.
    from app import models  # noqa: F401

    # Make absolutely sure the parent dir for the SQLite file exists and
    # is writable BEFORE we try to open it. The default mount point /config
    # is created by the Dockerfile, but a user-mounted volume might not be.
    config_dir = settings.config_path
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(
            f"Cannot create config dir {config_dir}: {e}. "
            "Check the volume mount permissions in docker-compose."
        ) from e

    if not os.access(str(config_dir), os.W_OK):
        raise RuntimeError(
            f"Config dir {config_dir} is not writable. "
            "Make sure the host bind-mount is owned by a user the "
            "container can write as (or use a named volume)."
        )

    async with _engine.begin() as conn:
        # Better concurrency for our mixed read/write workload.
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        await conn.run_sync(SQLModel.metadata.create_all)

        # Lightweight idempotent migrations for columns SQLModel's
        # create_all() can't add to existing tables. Each entry is
        # (table, column, SQL type). "duplicate column name" is the
        # SQLite response for an already-applied migration — we swallow
        # it. Any other error propagates.
        for table, column, sql_type in _MIGRATIONS:
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
                )
                log.info("db migration: added %s.%s", table, column)
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                raise
    log.info("database initialised at %s", settings.db_url)


# Additive migrations — list new columns added after initial schema freeze.
# SQLModel.create_all() is a no-op on existing tables, so these run once
# per column per database lifetime (idempotent on subsequent boots).
_MIGRATIONS: list[tuple[str, str, str]] = [
    ("queue_items", "discord_requester_id", "VARCHAR"),
    ("queue_items", "discord_guild_id", "VARCHAR"),
    ("queue_items", "discord_channel_id", "VARCHAR"),
    ("queue_items", "discord_owner_msg_id", "VARCHAR"),
    ("queue_items", "discord_notify_kind", "VARCHAR"),
]


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
