"""Library scan — check what's already downloaded on disk."""
from __future__ import annotations

import logging
import re
import time
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

from fastapi import APIRouter, Query
from slugify import slugify as _slugify

from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/library", tags=["library"])

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".ts", ".m4v", ".webm", ".flv"}

# ---------- name normalization ----------

_STRIP_YEAR = re.compile(r"\s*\(\d{4}\)\s*$")
_STRIP_STAFFEL = re.compile(r"\s*[Ss]taffel\s+\d+\s*$")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_MULTI_SPACE = re.compile(r"\s+")
_LEADING_ARTICLE = re.compile(r"^(?:the|a|an|der|die|das|ein|eine)\s+", re.IGNORECASE)


def _normalize(name: str) -> str:
    """Aggressive normalization for matching: transliterate unicode,
    lowercase, strip year/staffel suffix, remove all non-alphanumeric
    chars, collapse whitespace.

    Examples:
        "Grey's Anatomy"           -> "greys anatomy"
        "Türkisch für Anfänger"    -> "turkisch fur anfanger"
        "The Flash (2014)"         -> "the flash"
        "Dr. House"                -> "dr house"
        "The Rookie Staffel 1"     -> "the rookie"
    """
    s = name.strip()
    s = _STRIP_YEAR.sub("", s)
    s = _STRIP_STAFFEL.sub("", s)
    s = _slugify(s, separator=" ", lowercase=True)
    s = _NON_ALNUM.sub("", s)
    s = _MULTI_SPACE.sub(" ", s).strip()
    return s


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
    """Find the best-matching show folder under /tv/.

    Uses a multi-tier strategy:
      1. Exact case-insensitive match
      2. Normalized match (strips punctuation, years, 'Staffel X')
      3. Fuzzy match on normalized names (cutoff 0.65)
    """
    tv = settings.tv_path
    if not tv.is_dir():
        log.warning("tv_path %s is not a directory", tv)
        return None

    # Build indexes: lowercase -> Path  AND  normalized -> Path
    dirs: dict[str, Path] = {}
    norm_dirs: dict[str, Path] = {}
    try:
        for d in tv.iterdir():
            if d.is_dir():
                dirs[d.name.lower()] = d
                n = _normalize(d.name)
                if n:
                    norm_dirs[n] = d
    except OSError:
        log.warning("failed to list tv_path %s", tv, exc_info=True)
        return None

    # Tier 1: exact case-insensitive
    lower = title.lower().strip()
    if lower in dirs:
        log.debug("show %r matched tier-1 (exact) → %s", title, dirs[lower])
        return dirs[lower]

    # Tier 2: normalized exact match
    norm_title = _normalize(title)
    if norm_title and norm_title in norm_dirs:
        log.debug("show %r matched tier-2 (normalized) → %s", title, norm_dirs[norm_title])
        return norm_dirs[norm_title]

    # Tier 3: fuzzy on normalized names
    if norm_title:
        norm_candidates = list(norm_dirs.keys())
        matches = get_close_matches(norm_title, norm_candidates, n=1, cutoff=0.65)
        if matches:
            candidate = matches[0]
            ratio = SequenceMatcher(None, norm_title, candidate).ratio()
            # Reject the match when one name is a strict prefix of the
            # other with substantial extra content — those are different
            # series that only share a name prefix (e.g. "Malcolm
            # Mittendrin" vs "Malcolm Mittendrin: Unfair wie immer").
            a, b = sorted([candidate, norm_title], key=len)
            if b.startswith(a) and len(b) - len(a) >= 4:
                log.debug(
                    "show %r fuzzy candidate %r rejected (prefix guard, ratio=%.2f)",
                    title, candidate, ratio,
                )
                return None
            # Reject when similarity is only due to a shared leading
            # article ("The", "A", etc.).  Strip articles and re-check
            # the distinctive parts — if those diverge, the shows are
            # unrelated (e.g. "The Boys" vs "The Fosters").
            core_title = _LEADING_ARTICLE.sub("", norm_title)
            core_cand = _LEADING_ARTICLE.sub("", candidate)
            if core_title != norm_title or core_cand != candidate:
                core_ratio = SequenceMatcher(None, core_title, core_cand).ratio()
                if core_ratio < 0.6:
                    log.debug(
                        "show %r fuzzy candidate %r rejected (article guard, "
                        "core_ratio=%.2f)",
                        title, candidate, core_ratio,
                    )
                    return None
            log.debug(
                "show %r matched tier-3 (fuzzy, ratio=%.2f) → %s",
                title, ratio, norm_dirs[candidate],
            )
            return norm_dirs[candidate]

    log.info(
        "show %r not found on disk (norm=%r, %d folders scanned)",
        title, norm_title, len(dirs),
    )
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
    return [ep for _, ep in _scan_episodes_with_season(folder)]


def _scan_episodes_with_season(folder: Path) -> list[tuple[int | None, int]]:
    """Extract (season, episode) pairs from video files in a folder.

    Returns a list of (season_number_or_None, episode_number) tuples.
    Season is extracted from filenames like S01E05; None when only an
    episode number is found.
    """
    results: list[tuple[int | None, int]] = []
    seen: set[tuple[int | None, int]] = set()
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
                    if pat is _EP_PATTERNS[0]:
                        # S01E05 pattern — both season and episode
                        season = int(m.group(1))
                        ep_num = int(m.group(2))
                    else:
                        season = None
                        ep_num = int(m.group(m.lastindex or 1))
                    if 1 <= ep_num <= 999:
                        key = (season, ep_num)
                        if key not in seen:
                            seen.add(key)
                            results.append(key)
                    break
    except OSError:
        pass
    results.sort(key=lambda t: (t[0] or 0, t[1]))
    return results


def get_existing_episodes(title: str) -> dict[int, list[int]]:
    """Return {season_number: [episode_numbers]} for episodes already on disk.

    This is a non-async helper for internal use (queue endpoints) so they
    can skip episodes that are already downloaded.
    """
    show_dir = _find_show_dir(title)
    if not show_dir:
        return {}

    result: dict[int, set[int]] = {}

    # Scan season subdirectories
    season_dirs = _find_season_dirs(show_dir)
    for num, path in season_dirs.items():
        for _, ep in _scan_episodes_with_season(path):
            result.setdefault(num, set()).add(ep)

    # Also scan the show root for loose files (e.g. S02E03 not in a subfolder)
    for season, ep in _scan_episodes_with_season(show_dir):
        s = season if season is not None else 1
        result.setdefault(s, set()).add(ep)

    return {k: sorted(v) for k, v in sorted(result.items()) if v}


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
    norm_title = _normalize(title)

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
            # Normalized match
            norm_file = _normalize(f.stem)
            if norm_title and norm_file and norm_title == norm_file:
                return _store(cache_key, {"found": True})
            # Fuzzy on normalized names
            if norm_title and norm_file:
                matches = get_close_matches(norm_title, [norm_file], n=1, cutoff=0.65)
                # Guard: don't treat a prefix-match with substantial
                # extra content as the same title (e.g. "Malcolm
                # Mittendrin" vs "Malcolm Mittendrin Unfair wie immer").
                if matches:
                    a, b = sorted([norm_title, norm_file], key=len)
                    if b.startswith(a) and len(b) - len(a) >= 4:
                        matches = []
                # Guard: reject when similarity comes only from a shared
                # leading article (e.g. "The Boys" vs "The Fosters").
                if matches:
                    ct = _LEADING_ARTICLE.sub("", norm_title)
                    cf = _LEADING_ARTICLE.sub("", norm_file)
                    if (ct != norm_title or cf != norm_file) and \
                       SequenceMatcher(None, ct, cf).ratio() < 0.6:
                        matches = []
            else:
                matches = get_close_matches(lower, [name_lower], n=1, cutoff=0.75)
            if matches:
                return _store(cache_key, {"found": True})
    except OSError:
        pass

    return _store(cache_key, {"found": False})
