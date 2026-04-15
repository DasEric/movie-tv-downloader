"""TMDB metadata lookup (v3 API, JSON)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.services import settings_store
from app.services.http import get_client

log = logging.getLogger(__name__)

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"


async def _key() -> str | None:
    k = await settings_store.get("tmdb_api_key")
    return k or None


_LANG_TAGS = {"de": "de-DE", "en": "en-US"}


async def _lang() -> str:
    code = await settings_store.get("default_language", "de")
    return _LANG_TAGS.get(str(code).lower(), "en-US")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict | None:
    key = await _key()
    if not key:
        return None
    try:
        c = await get_client()
        r = await c.get(
            f"{BASE}{path}",
            params={"api_key": key, "language": await _lang(), **(params or {})},
            timeout=15,
        )
    except Exception as e:
        log.warning("tmdb request failed for %s: %s", path, e)
        return None
    if r.status_code != 200:
        log.warning("tmdb %s -> %d", path, r.status_code)
        return None
    try:
        return r.json()
    except Exception as e:
        log.warning("tmdb %s json decode failed: %s", path, e)
        return None


async def search_movie(query: str) -> list[dict]:
    data = await _get("/search/movie", {"query": query})
    if not data:
        return []
    return [_map_movie(r) for r in data.get("results", [])[:15]]


async def search_tv(query: str) -> list[dict]:
    data = await _get("/search/tv", {"query": query})
    if not data:
        return []
    return [_map_tv(r) for r in data.get("results", [])[:15]]


async def upcoming_movies() -> list[dict]:
    """Movies that are not yet released (theatrical or otherwise)."""
    data = await _get("/movie/upcoming", {"page": "1"})
    if not data:
        return []
    today = datetime.utcnow().date()
    out = [_map_movie(r) for r in data.get("results", [])[:30]]
    # Filter to TRULY unreleased (TMDB's upcoming endpoint sometimes
    # includes titles that released last week in other regions).
    return [r for r in out if r.get("release_date") and r["release_date"].date() > today]


async def upcoming_tv() -> list[dict]:
    """TV shows with a first-air date in the future ("on the air" endpoint
    returns currently airing — we actually want the 'airing today' + manual
    date filter). TMDB has no dedicated 'upcoming tv' endpoint, so we use
    discover with a `first_air_date.gte=<today>` filter."""
    today = datetime.utcnow().date().isoformat()
    data = await _get(
        "/discover/tv",
        {
            "first_air_date.gte": today,
            "sort_by": "first_air_date.asc",
            "page": "1",
        },
    )
    if not data:
        return []
    return [_map_tv(r) for r in data.get("results", [])[:30]]


async def search_movie_with_year(title: str, year: int | None = None) -> list[dict]:
    """Search movies optionally constrained to a release year."""
    params: dict[str, Any] = {"query": title}
    if year:
        params["year"] = str(year)
    data = await _get("/search/movie", params)
    if not data:
        return []
    return [_map_movie(r) for r in data.get("results", [])[:15]]


async def search_tv_with_year(title: str, year: int | None = None) -> list[dict]:
    params: dict[str, Any] = {"query": title}
    if year:
        params["first_air_date_year"] = str(year)
    data = await _get("/search/tv", params)
    if not data:
        return []
    return [_map_tv(r) for r in data.get("results", [])[:15]]


async def details_movie(movie_id: int) -> dict | None:
    """Full movie details incl. title, overview, poster, release_date, rating."""
    data = await _get(f"/movie/{movie_id}")
    if not data:
        return None
    return {
        "tmdb_id": data["id"],
        "title": data.get("title") or data.get("original_title"),
        "overview": data.get("overview"),
        "release_date": _parse_date(data.get("release_date")),
        "year": (data.get("release_date") or "0000")[:4],
        "poster": (IMG + data["poster_path"]) if data.get("poster_path") else None,
        "rating": data.get("vote_average"),
    }


async def details_tv(show_id: int) -> dict | None:
    """Full TV details incl. name, overview, poster, first_air_date, rating,
    and the full season list with episode counts. Used by the Discord bot
    to determine the latest available season."""
    data = await _get(f"/tv/{show_id}")
    if not data:
        return None
    seasons = [
        {
            "season_number": s.get("season_number"),
            "episode_count": s.get("episode_count"),
            "air_date": _parse_date(s.get("air_date")),
        }
        for s in (data.get("seasons") or [])
        # Skip "specials" (season 0) — scrapers don't align with TMDB's
        # specials numbering, and the library scanner doesn't track them.
        if s.get("season_number") and s["season_number"] > 0
    ]
    return {
        "tmdb_id": data["id"],
        "title": data.get("name") or data.get("original_name"),
        "overview": data.get("overview"),
        "first_air_date": _parse_date(data.get("first_air_date")),
        "year": (data.get("first_air_date") or "0000")[:4],
        "poster": (IMG + data["poster_path"]) if data.get("poster_path") else None,
        "rating": data.get("vote_average"),
        "seasons": seasons,
        "latest_season": max((s["season_number"] for s in seasons), default=None),
    }


async def tv_episode(show_id: int, season: int, episode: int) -> dict | None:
    data = await _get(f"/tv/{show_id}/season/{season}/episode/{episode}")
    if not data:
        return None
    return {
        "name": data.get("name"),
        "air_date": _parse_date(data.get("air_date")),
        "overview": data.get("overview"),
    }


async def movie_release(movie_id: int) -> datetime | None:
    data = await _get(f"/movie/{movie_id}")
    if not data:
        return None
    return _parse_date(data.get("release_date"))


def _map_movie(r: dict) -> dict:
    return {
        "tmdb_id": r["id"],
        "title": r.get("title") or r.get("original_title"),
        "year": (r.get("release_date") or "0000")[:4],
        "release_date": _parse_date(r.get("release_date")),
        "poster": (IMG + r["poster_path"]) if r.get("poster_path") else None,
        "overview": r.get("overview"),
    }


def _map_tv(r: dict) -> dict:
    return {
        "tmdb_id": r["id"],
        "title": r.get("name") or r.get("original_name"),
        "year": (r.get("first_air_date") or "0000")[:4],
        "release_date": _parse_date(r.get("first_air_date")),
        "poster": (IMG + r["poster_path"]) if r.get("poster_path") else None,
        "overview": r.get("overview"),
    }


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None
