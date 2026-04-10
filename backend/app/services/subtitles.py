"""
Subliminal integration — fetch DE/EN subtitles for the finalised mp4.

Uses a thread executor so the sync library doesn't block the event loop.
Fails quietly: missing subs should never abort a download.

Subliminal writes the .srt next to the video. We compute the expected
path via `subliminal.subtitle.get_subtitle_path(video, language)` and
return the files that actually exist afterwards.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.services import settings_store

log = logging.getLogger(__name__)


async def fetch_for(video_path: Path) -> list[Path]:
    enabled = await settings_store.get("auto_subtitles", True)
    if not enabled:
        return []
    langs = await settings_store.get("subtitle_languages", ["de", "en"])
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _download_sync, video_path, langs)
    except Exception as e:
        log.warning("subliminal failed for %s: %s", video_path.name, e)
        return []


def _download_sync(video_path: Path, langs: list[str]) -> list[Path]:
    try:
        from babelfish import Language  # type: ignore
        from subliminal import (  # type: ignore
            Video,
            download_best_subtitles,
            save_subtitles,
        )
    except Exception as e:
        log.warning("subliminal import failed: %s", e)
        return []

    if not video_path.exists():
        return []

    try:
        video = Video.fromname(str(video_path))
    except Exception as e:
        log.warning("subliminal could not parse %s: %s", video_path.name, e)
        return []

    try:
        # babelfish expects ISO 639-3 ("deu"/"eng") — our UI uses the
        # ISO 639-1 "de"/"en" alpha-2 codes, so convert via fromalpha2().
        lang_set: set = set()
        for lc in langs:
            code = str(lc).strip().lower()
            if len(code) == 2:
                lang_set.add(Language.fromalpha2(code))
            else:
                lang_set.add(Language(code))
    except Exception as e:
        log.warning("invalid subtitle language list %r: %s", langs, e)
        return []

    try:
        found = download_best_subtitles({video}, lang_set)
    except Exception as e:
        log.warning("subliminal download failed: %s", e)
        return []

    subs_for_video = found.get(video, [])
    if not subs_for_video:
        log.info("no subtitles found for %s", video_path.name)
        return []

    try:
        save_subtitles(video, subs_for_video)
    except Exception as e:
        log.warning("subliminal save failed: %s", e)
        return []

    # Subliminal saves files next to the video as <name>.<lang>.srt.
    # The language code it uses depends on the saver, so we try multiple
    # candidate filenames (alpha2 first, then alpha3).
    base = video_path.with_suffix("")
    saved_paths: list[Path] = []
    for sub in subs_for_video:
        alpha2 = getattr(sub.language, "alpha2", None)
        alpha3 = getattr(sub.language, "alpha3", None)
        lang_str = str(sub.language)
        candidates = []
        if alpha2:
            candidates.append(Path(f"{base}.{alpha2}.srt"))
        if alpha3:
            candidates.append(Path(f"{base}.{alpha3}.srt"))
        candidates.append(Path(f"{base}.{lang_str}.srt"))
        for cand in candidates:
            if cand.exists():
                saved_paths.append(cand)
                break
    if saved_paths:
        log.info(
            "saved %d subtitle(s) for %s: %s",
            len(saved_paths),
            video_path.name,
            ", ".join(p.name for p in saved_paths),
        )
    return saved_paths
