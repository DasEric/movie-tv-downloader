"""Library scan — check what's already downloaded on disk."""
from __future__ import annotations

import logging
import re
import time
from difflib import get_close_matches
from pathlib import Path

from fastapi import APIRouter, Query

from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".ts", ".m4v", ".webm", ".flv"}

# Patterns to extract episode numbers from filenames, tried in order.
_EP_PATTERNS = [
    re.compile(r"[Ss](\d+)[Ee](\d+)"),                # S01E05
    re.compile(r"[Ee][Pp]?\.?(\d+)", re.IGNORECASE),   # E05, Ep05, e5
    re.compile(r"(?:^|[\.\-_ x])(\d{2,3})(?:[\.\-_ v]|$)"),  # .05. or _05_ or -05-
]

# Patterns to extract season number from folder names.
_SEASON_DIR_PATTERNS = [
    re.compile(r"[Ss](?:eason|taffel)\s*(\d+)", re.IGNORECASE),  # Season 01, Staffel 1
    re.compile(r"^[Ss](\d+)$"),                                    # S01
    re.compile(r"^(\d{1,2})$"),                                    # just "1" or "01"
]

# ---------- cache ----------

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60.0  # seconds


def invalidate_cache() -> None:
    """Called by the worker after a download completes."""
    _cache.clear()


def _cached(key: str):
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def _store(key: str, value: dict) -> dict:
    _cache[key] = (time.monotonic(), value)
    return value


# ---------- helpers ----------

def _find_show_dir(title: str) -> Path | None:
    """Find the best-matching show folder under /tv/."""
    tv = settings.tv_path
    if not tv.is_dir():
        return None

    # Exact match first (case-insensitive)
    lower = title.lower().strip()
    dirs = {}
    try:
        for d in tv.iterdir():
            if d.is_dir():
                dirs[d.name.lower()] = d
    except OSError:
        return None

    if lower in dirs:
        return dirs[lower]

    # Strip trailing "Staffel X" for matching
    stripped = re.sub(r"\s*[Ss]taffel\s+\d+\s*$", "", title, flags=re.IGNORECASE).strip().lower()
    if stripped and stripped in dirs:
        return dirs[stripped]

    # Fuzzy match
    candidates = list(dirs.keys())
    matches = get_close_matches(lower, candidates, n=1, cutoff=0.7)
    if not matches and stripped:
        matches = get_close_matches(stripped, candidates, n=1, cutoff=0.7)
    if matches:
        return dirs[matches[0]]

    return None


def _find_season_dirs(show_dir: Path) -> dict[int, Path]:
    """Map season numbers to their directories."""
    result: dict[int, Path] = {}
    try:
        for d in show_dir.iterdir():
            if not d.is_dir():
                continue
            for pat in _SEASON_DIR_PATTERNS:
                m = pat.search(d.name)
                if m:
                    result[int(m.group(1))] = d
                    break
    except OSError:
        pass
    return result


def _scan_episodes(folder: Path) -> list[int]:
    """Extract episode numbers from video files in a folder."""
    episodes: set[int] = set()
    try:
        for f in folder.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in VIDEO_EXTS:
                continue
            name = f.stem
            for pat in _EP_PATTERNS:
                m = pat.search(name)
                if m:
                    # Use last group (some patterns have season+episode groups)
                    ep_num = int(m.group(m.lastindex or 1))
                    if 1 <= ep_num <= 999:
                        episodes.add(ep_num)
                    break
    except OSError:
        pass
    return sorted(episodes)


def get_existing_episodes(title: str) -> dict[int, list[int]]:
    """Return {season_number: [episode_numbers]} for episodes already on disk.

    This is a non-async helper for internal use (queue endpoints) so they
    can skip episodes that are already downloaded.
    """
    show_dir = _find_show_dir(title)
    if not show_dir:
        return {}
    season_dirs = _find_season_dirs(show_dir)
    if not season_dirs:
        eps = _scan_episodes(show_dir)
        return {1: eps} if eps else {}
    result: dict[int, list[int]] = {}
    for num, path in season_dirs.items():
        eps = _scan_episodes(path)
        if eps:
            result[num] = eps
    return result


# ---------- endpoints ----------

@router.get("/show")
async def check_show(title: str = Query(..., min_length=1)):
    """
    Check what episodes of a show exist on disk.

    Returns:
        {
            "found": true,
            "seasons": {"1": [1,2,3], "8": [1,3,5]},
            "total_episodes": 8
        }
    """
    cache_key = f"show:{title.lower()}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    show_dir = _find_show_dir(title)
    if not show_dir:
        return _store(cache_key, {"found": False, "seasons": {}, "total_episodes": 0})

    season_dirs = _find_season_dirs(show_dir)
    if not season_dirs:
        # Maybe all episodes are directly in the show folder (no season subdirs)
        eps = _scan_episodes(show_dir)
        if eps:
            return _store(cache_key, {
                "found": True,
                "seasons": {"1": eps},
                "total_episodes": len(eps),
            })
        return _store(cache_key, {"found": False, "seasons": {}, "total_episodes": 0})

    seasons: dict[str, list[int]] = {}
    total = 0
    for num, path in sorted(season_dirs.items()):
        eps = _scan_episodes(path)
        if eps:
            seasons[str(num)] = eps
            total += len(eps)

    return _store(cache_key, {
        "found": bool(seasons),
        "seasons": seasons,
        "total_episodes": total,
    })


@router.get("/movie")
async def check_movie(
    title: str = Query(..., min_length=1),
    year: int | None = Query(None),
):
    """Check if a movie file exists on disk."""
    cache_key = f"movie:{title.lower()}:{year}"
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    movies = settings.movies_path
    if not movies.is_dir():
        return _store(cache_key, {"found": False})

    lower = title.lower().strip()

    try:
        for f in movies.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in VIDEO_EXTS:
                continue
            name_lower = f.stem.lower()
            # Check title match (with or without year)
            if lower in name_lower:
                return _store(cache_key, {"found": True})
            # Fuzzy: close enough?
            matches = get_close_matches(lower, [name_lower], n=1, cutoff=0.75)
            if matches:
                return _store(cache_key, {"found": True})
    except OSError:
        pass

    return _store(cache_key, {"found": False})
