"""
Post-processing pipeline:
  raw download (any container)  ->  H.264/AAC MP4  ->  Plex path with canonical name
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

from slugify import slugify

from app.config import settings

log = logging.getLogger(__name__)

MOVIES_ROOT = settings.movies_path
TV_ROOT = settings.tv_path
TMP_ROOT = settings.tmp_path


# ---------- naming ----------

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_STAFFEL_RE = re.compile(r'\s*Staffel\s+\d+\s*$', re.IGNORECASE)
_FALLBACK = "unknown"


def _safe_folder(name: str) -> str:
    """Readable folder name: keep spaces, strip OS-invalid chars.
    Falls back to 'unknown' so we never produce an empty path segment."""
    cleaned = _INVALID.sub("", name or "").strip().rstrip(".")
    cleaned = re.sub(r"  +", " ", cleaned)
    return cleaned or _FALLBACK


def _clean_show_title(name: str) -> str:
    """Strip trailing 'Staffel X' from show titles.
    s.to sometimes appends the season to the show name
    (e.g. 'The Rookie Staffel 1') — we don't want that in folder names."""
    return _STAFFEL_RE.sub("", name or "").strip() or name


def _safe_file_token(name: str) -> str:
    """Filename token: underscores, ASCII-ish.
    Falls back to 'unknown' so we never produce an empty filename."""
    token = slugify(name or "", separator="_", lowercase=False)
    return token or _FALLBACK


def tv_target(show: str, season: int, episode: int) -> Path:
    """
    /tv/Show Name/SXX/Show_Name_SXXEYY.mp4
    """
    clean = _clean_show_title(show)
    folder = TV_ROOT / _safe_folder(clean) / f"S{season:02d}"
    fname = f"{_safe_file_token(clean)}_S{season:02d}E{episode:02d}.mp4"
    return folder / fname


def movie_target(title: str, year: int | None) -> Path:
    suffix = f" ({year})" if year else ""
    return MOVIES_ROOT / f"{_safe_folder(title)}{suffix}.mp4"


# ---------- ffmpeg ----------

async def convert_to_mp4(src: Path, dst: Path) -> None:
    """
    Remux when possible (fast path), else transcode to H.264/AAC.
    Always output an MP4 — no .ts/.mkv leftovers.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = dst.with_suffix(".mp4.part")

    # Try stream-copy remux first (fast, lossless if codecs are already compatible).
    remux_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        "-f", "mp4", str(tmp_out),
    ]
    rc = await _run(remux_cmd)
    if rc != 0:
        log.info("remux failed for %s, transcoding", src.name)
        transcode_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(tmp_out),
        ]
        rc = await _run(transcode_cmd)
        if rc != 0:
            tmp_out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg failed for {src}")

    tmp_out.replace(dst)
    src.unlink(missing_ok=True)


async def _run(cmd: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(*cmd)
    return await proc.wait()


# ---------- top-level entrypoint for the worker ----------

async def finalize_tv(src_tmp: Path, show: str, season: int, episode: int) -> Path:
    dst = tv_target(show, season, episode)
    await convert_to_mp4(src_tmp, dst)
    _cleanup_tmp(src_tmp.parent)
    return dst


async def finalize_movie(src_tmp: Path, title: str, year: int | None) -> Path:
    dst = movie_target(title, year)
    await convert_to_mp4(src_tmp, dst)
    _cleanup_tmp(src_tmp.parent)
    return dst


def _cleanup_tmp(folder: Path) -> None:
    if folder.is_dir() and folder.is_relative_to(TMP_ROOT):
        shutil.rmtree(folder, ignore_errors=True)
