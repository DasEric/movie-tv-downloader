"""
yt-dlp wrapper that runs inside the same Python process and reports
progress via a callback. We avoid shelling out so we don't need to parse
stdout and so we don't risk pipe-buffer deadlocks.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Callable

import yt_dlp  # type: ignore

from app.config import settings

log = logging.getLogger(__name__)

ProgressCB = Callable[[dict], None]

QUALITY_MAP = {
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "1440p": "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best",
    "4k": "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best",
    "best": "bestvideo+bestaudio/best",
}


def _ydl_opts(
    out_path: Path,
    quality: str,
    hook: ProgressCB,
    http_headers: dict[str, str] | None = None,
) -> dict:
    fmt = QUALITY_MAP.get(quality.lower(), QUALITY_MAP["1080p"])
    opts: dict = {
        "outtmpl": str(out_path),
        "format": fmt,
        # When separate video/audio streams have to be merged, prefer MP4,
        # fall back to MKV. Our post-processor converts either to MP4 anyway.
        "merge_output_format": "mp4/mkv",
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 30,
        "http_chunk_size": 10 * 1024 * 1024,
        "user_agent": settings.user_agent,
        "progress_hooks": [hook],
        # Don't leak .part / .ytdl fragments into the final dir on abort
        "keepvideo": False,
        "nopart": False,
    }
    if http_headers:
        # yt-dlp merges these into every outgoing request — critical for
        # hosters like Vidmoly that refuse without the right Referer.
        opts["http_headers"] = http_headers
    if settings.proxy_url:
        opts["proxy"] = settings.proxy_url
    return opts


async def download(
    url: str,
    out_path: Path,
    quality: str,
    on_progress: ProgressCB,
    http_headers: dict[str, str] | None = None,
) -> Path:
    """
    Download `url` to `out_path` (template — yt-dlp fills in `%(ext)s`).
    Returns the final file path — the actual file that ended up on disk
    after any stream merging.

    The yt-dlp progress hook fires on the executor thread; we bounce it
    back onto the event loop via call_soon_threadsafe.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    final_path_holder: dict[str, Path] = {}

    def _thread_hook(d: dict) -> None:
        try:
            status = d.get("status")
            if status == "downloading":
                # Format speed/ETA ourselves from the raw numeric fields.
                # yt-dlp's `_speed_str` / `_eta_str` contain ANSI color
                # escape codes for terminal output (e.g. "\x1b[0;32m10.1 MiB/s\x1b[0m")
                # which would leak into the web UI as literal "[0;32m..." text.
                payload = {
                    "status": "downloading",
                    "percent": _parse_percent(d),
                    "speed": _format_speed(d.get("speed")),
                    "eta": _format_eta(d.get("eta")),
                }
                loop.call_soon_threadsafe(on_progress, payload)
            elif status == "finished":
                fn = d.get("filename")
                if fn:
                    final_path_holder["path"] = Path(fn)
                loop.call_soon_threadsafe(
                    on_progress, {"status": "finished", "percent": 100.0}
                )
        except Exception as e:
            log.debug("progress hook failed: %s", e)

    def run() -> str:
        opts = _ydl_opts(out_path, quality, _thread_hook, http_headers)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # prepare_filename uses info['ext'] which yt-dlp updates to
            # the merged container after download.
            return ydl.prepare_filename(info)

    filename = await loop.run_in_executor(None, run)

    # Resolution order:
    #   1. yt-dlp's prepare_filename (post-merge, authoritative)
    #   2. last path reported by the progress hook
    #   3. largest file in the scratch dir (last resort)
    candidate = Path(filename)
    if candidate.exists():
        return candidate

    hook_path = final_path_holder.get("path")
    if hook_path and hook_path.exists():
        return hook_path

    # Scan the scratch dir for whatever yt-dlp actually produced.
    parent = out_path.parent
    if parent.is_dir():
        files = [f for f in parent.iterdir() if f.is_file() and not f.name.endswith(".part")]
        if files:
            return max(files, key=lambda f: f.stat().st_size)

    raise FileNotFoundError(
        f"yt-dlp reported success but no output file found at {candidate}"
    )


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mK]")


def _strip_ansi(s: str | None) -> str | None:
    """Remove ANSI terminal color escape codes from a string."""
    if not s:
        return s
    return _ANSI_ESCAPE_RE.sub("", s).strip() or None


def _parse_percent(d: dict) -> float:
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    got = d.get("downloaded_bytes") or 0
    if total:
        return round(got / total * 100.0, 1)
    # Fallback: parse the pre-formatted string (strip ANSI first)
    txt = _strip_ansi(d.get("_percent_str")) or ""
    txt = txt.strip().rstrip("%")
    try:
        return float(txt)
    except Exception:
        return 0.0


def _format_speed(speed_bytes_per_sec: float | int | None) -> str | None:
    """Human-readable download speed — no ANSI, no yt-dlp string helpers."""
    if not speed_bytes_per_sec or speed_bytes_per_sec <= 0:
        return None
    s = float(speed_bytes_per_sec)
    if s >= 1024 * 1024 * 1024:
        return f"{s / (1024 * 1024 * 1024):.2f} GiB/s"
    if s >= 1024 * 1024:
        return f"{s / (1024 * 1024):.2f} MiB/s"
    if s >= 1024:
        return f"{s / 1024:.1f} KiB/s"
    return f"{int(s)} B/s"


def _format_eta(eta_seconds: int | float | None) -> str | None:
    """Human-readable ETA in `H:MM:SS` or `M:SS` form."""
    if eta_seconds is None or eta_seconds < 0:
        return None
    total = int(eta_seconds)
    if total >= 3600:
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"
