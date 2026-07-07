"""FastAPI application entry point — serves REST, WebSocket, and the built frontend."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import (
    discord as discord_api,
    library,
    logs,
    queue,
    search,
    settings as settings_api,
    upcoming,
    watchlist,
    ws,
)
from app.config import settings
from app.db import init_db
from app.logger import setup_logging
from app.queue_manager import queue_manager
from app.services import scheduler
from app.services.http import close_client

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    log.info("H0melab Downloader starting (v%s)", __version__)
    await init_db()
    # Warm the per-source path cache before the library scanner runs.
    try:
        from app.services import paths
        await paths.refresh()
    except Exception as e:
        log.warning("initial path cache refresh failed: %s", e)
    await queue_manager.start()
    await scheduler.start()
    # Discord bot — no-op when disabled or token missing.
    from app.services.discord_bot import runner as discord_runner
    try:
        await discord_runner.start_if_enabled()
    except Exception as e:
        log.warning("discord bot start_if_enabled failed: %s", e)
    try:
        yield
    finally:
        try:
            await discord_runner.stop()
        except Exception as e:
            log.warning("discord bot stop failed: %s", e)
        await scheduler.stop()
        await queue_manager.stop()
        await close_client()
        log.info("H0melab Downloader stopped.")


app = FastAPI(
    title="H0melab Downloader",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(discord_api.router)
app.include_router(library.router)
app.include_router(queue.router)
app.include_router(search.router)
app.include_router(settings_api.router)
app.include_router(upcoming.router)
app.include_router(watchlist.router)
app.include_router(logs.router)
app.include_router(ws.router)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "concurrency": queue_manager._current_concurrency,
        "running": len(queue_manager._running),
    }


@app.get("/api/info")
async def info() -> dict:
    from app.services import settings_store
    from app.models import ItemSource

    runtime = await settings_store.get_all()
    return {
        "version": __version__,
        "homelab_credit": settings.homelab_credit,
        "default_language": settings.default_language,
        "paths": {
            "movies": str(settings.movies_path),
            "tv": str(settings.tv_path),
            "config": str(settings.config_path),
        },
        "sources": [s.value for s in ItemSource],
        # Service status flags for the UI badges (no secrets leaked)
        "configured": {
            "tmdb": bool(runtime.get("tmdb_api_key")),
            "discord": bool(runtime.get("discord_webhook_url")),
            "telegram": bool(
                runtime.get("telegram_bot_token") and runtime.get("telegram_chat_id")
            ),
            "proxy": bool(runtime.get("proxy_url")),
        },
    }


# ---- static frontend ----
# Files end up in app/static/ during the Docker build (see Dockerfile stage 1).
_STATIC = settings.static_path
_INDEX = _STATIC / "index.html"

if _STATIC.exists():
    _assets_dir = _STATIC / "assets"
    if _assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_assets_dir)),
            name="assets",
        )

    _STATIC_RESOLVED = _STATIC.resolve()

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # Never serve API paths via the fallback.
        if full_path.startswith("api/") or full_path.startswith("ws/"):
            return JSONResponse({"error": "not found"}, status_code=404)

        # Path-traversal guard: resolve and confirm the target stays
        # inside the static dir.
        if full_path:
            try:
                target = (_STATIC / full_path).resolve()
            except (OSError, RuntimeError):
                return JSONResponse({"error": "bad path"}, status_code=400)
            try:
                target.relative_to(_STATIC_RESOLVED)
            except ValueError:
                return JSONResponse({"error": "forbidden"}, status_code=403)
            if target.is_file():
                return FileResponse(target)

        # Default: serve index.html so React Router / BrowserRouter works.
        if _INDEX.exists():
            return FileResponse(_INDEX)
        return JSONResponse({"error": "frontend not built"}, status_code=500)
else:
    log.warning("static frontend not found at %s — UI will 404", _STATIC)
