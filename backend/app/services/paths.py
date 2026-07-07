"""
Per-source output path routing.

By default everything lands in the two global roots (`settings.tv_path` and
`settings.movies_path`). Users who keep separate Plex libraries — e.g. a
dedicated "Anime" library fed by aniworld, separate from "Serien" fed by
s.to — can override the TV and/or movie root per source in the settings UI.

The override lives in the `source_paths` setting: a dict keyed by the
`ItemSource` value, each entry `{"tv": "<path>", "movie": "<path>"}`. An empty
or missing string means "use the global default", so the feature is fully
backward compatible.

Two access shapes:

  * `output_root(source, kind)` — async, used by the worker on the write path.
  * `all_tv_roots()` / `all_movie_roots()` — sync, used by the library scanner
    (which is called from sync contexts) to detect already-downloaded content
    across every configured root. Backed by a cache refreshed via `refresh()`
    on startup and after every settings save.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.models import ItemKind, ItemSource
from app.services import settings_store

log = logging.getLogger(__name__)

# Sync cache of the extra (non-default) roots, refreshed on settings change.
_extra_tv_roots: list[Path] = []
_extra_movie_roots: list[Path] = []


def _parse_overrides(raw) -> dict[str, dict]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            out[str(k)] = v
    return out


def _is_mounted_root(root: Path) -> bool:
    """Best-effort check that `root` is a real (Docker-mounted) location.

    A configured path only persists if it's actually bind-mounted into the
    container. If the user typed a path but forgot to mount it, writing there
    would silently vanish into the container's ephemeral layer. We detect the
    two safe cases:

      * the directory itself exists (a mounted volume dir always exists), or
      * its parent is a real non-root directory (a sub-folder of a mount, e.g.
        `/data/anime` where `/data` is mounted — we may create the sub-folder).

    A bare unmounted path like `/anime` (parent is the filesystem root `/`)
    fails the check, so we fall back to the default library instead of losing
    the file.
    """
    try:
        if root.is_dir():
            return True
        parent = root.parent
        return parent != parent.parent and parent.is_dir()
    except OSError:
        return False


async def output_root(source: ItemSource, kind: ItemKind) -> Path:
    """Return the destination root for a finished download (async, write path).

    Falls back to the global default root when no custom path is set OR when
    the custom path isn't actually mounted — so a misconfigured override never
    causes a lost download.
    """
    field = "movie" if kind == ItemKind.MOVIE else "tv"
    default = settings.movies_path if kind == ItemKind.MOVIE else settings.tv_path

    overrides = _parse_overrides(await settings_store.get("source_paths", {}))
    key = source.value if isinstance(source, ItemSource) else str(source)
    entry = overrides.get(key) or {}
    custom = str(entry.get(field, "") or "").strip()
    if not custom:
        return default

    root = Path(custom)
    if _is_mounted_root(root):
        return root

    log.warning(
        "custom %s path %r for source %s is not a mounted directory — "
        "falling back to default %s so the download isn't lost",
        field, custom, key, default,
    )
    return default


async def refresh() -> None:
    """Reload the sync root cache from persisted settings."""
    global _extra_tv_roots, _extra_movie_roots
    overrides = _parse_overrides(await settings_store.get("source_paths", {}))
    tv: dict[str, None] = {}   # dict preserves insertion order + dedupes
    mv: dict[str, None] = {}
    for entry in overrides.values():
        t = str(entry.get("tv", "") or "").strip()
        m = str(entry.get("movie", "") or "").strip()
        if t:
            tv[t] = None
        if m:
            mv[m] = None
    _extra_tv_roots = [Path(p) for p in tv]
    _extra_movie_roots = [Path(p) for p in mv]
    log.info(
        "path routing refreshed: %d extra tv root(s), %d extra movie root(s)",
        len(_extra_tv_roots),
        len(_extra_movie_roots),
    )


def all_tv_roots() -> list[Path]:
    """Global TV root + every custom TV root (sync, read/scan path)."""
    roots = [settings.tv_path, *_extra_tv_roots]
    # Dedupe while preserving order (a custom root may equal the default).
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        s = str(r)
        if s not in seen:
            seen.add(s)
            out.append(r)
    return out


def all_movie_roots() -> list[Path]:
    """Global movie root + every custom movie root (sync, read/scan path)."""
    roots = [settings.movies_path, *_extra_movie_roots]
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        s = str(r)
        if s not in seen:
            seen.add(s)
            out.append(r)
    return out
