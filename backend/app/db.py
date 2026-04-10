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
    log.info("database initialised at %s", settings.db_url)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
