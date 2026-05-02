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


# ---------- subtitle remux ----------

_LANG_TO_ISO639_2: dict[str, str] = {
    "de": "deu", "en": "eng", "fr": "fra", "es": "spa", "it": "ita",
    "pt": "por", "nl": "nld", "pl": "pol", "ru": "rus", "ja": "jpn",
    "ko": "kor", "zh": "zho", "ar": "ara", "tr": "tur", "sv": "swe",
    "da": "dan", "fi": "fin", "no": "nor", "cs": "ces", "hu": "hun",
    "ro": "ron", "el": "ell", "he": "heb", "hi": "hin", "th": "tha",
    "uk": "ukr", "hr": "hrv", "bg": "bul", "sk": "slk", "sl": "slv",
}

_LANG_TITLES: dict[str, str] = {
    "de": "Deutsch", "en": "English", "fr": "Français", "es": "Español",
    "it": "Italiano", "pt": "Português", "nl": "Nederlands", "pl": "Polski",
    "ru": "Русский", "ja": "日本語", "ko": "한국어", "zh": "中文",
    "ar": "العربية", "tr": "Türkçe",
}


def _extract_lang_code(srt_path: Path) -> str:
    """Extract language code from srt filename like 'video.de.srt' -> 'de'."""
    parts = srt_path.stem.rsplit(".", 1)
    return parts[-1] if len(parts) > 1 else "und"


async def remux_subtitles(video_path: Path, srt_files: list[Path]) -> None:
    """
    Remux SRT subtitles into an existing MP4 as mov_text tracks.
    Writes to a temp file, then replaces the original on success.
    """
    if not srt_files or not video_path.exists():
        return

    tmp_out = video_path.with_suffix(".mp4.sub_remux")

    cmd: list[str] = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
    ]

    for srt in srt_files:
        cmd += ["-sub_charenc", "UTF-8", "-i", str(srt)]

    cmd += ["-map", "0:v", "-map", "0:a"]

    for idx in range(len(srt_files)):
        cmd += ["-map", f"{idx + 1}:0"]

    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"]

    for idx, srt in enumerate(srt_files):
        lang2 = _extract_lang_code(srt)
        lang3 = _LANG_TO_ISO639_2.get(lang2, lang2)
        title = _LANG_TITLES.get(lang2, lang2.upper())
        cmd += [
            f"-metadata:s:s:{idx}", f"language={lang3}",
            f"-metadata:s:s:{idx}", f"title={title}",
        ]

    cmd += ["-movflags", "+faststart", str(tmp_out)]

    rc = await _run(cmd)
    if rc != 0:
        tmp_out.unlink(missing_ok=True)
        raise RuntimeError(f"subtitle remux failed for {video_path.name}")

    tmp_out.replace(video_path)
    for srt in srt_files:
        srt.unlink(missing_ok=True)
    log.info("remuxed %d subtitle(s) into %s", len(srt_files), video_path.name)


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
