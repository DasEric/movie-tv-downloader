"""
Shared HTTP client.

We use curl_cffi which impersonates a real Chrome TLS fingerprint — this
sails through most Cloudflare JS/Turnstile gates on s.to, aniworld.to and
megakino WITHOUT a headless browser. No FlareSolverr, no Playwright.

The client is long-lived (single AsyncSession per process) so we don't
leak file descriptors — that was one of the root causes of the stall-bug
in the reference repo. Each public helper (`get`, `get_with_final_url`)
additionally runs the captcha-detection heuristics against the response
so callers never silently work on a challenge page.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from curl_cffi.requests import AsyncSession

from app.config import settings
from app.services.captcha import raise_if_captcha

log = logging.getLogger(__name__)

# Transient curl error codes that should be retried automatically.
_RETRIABLE_CURL_CODES = {
    16,  # HTTP/2 stream error
    28,  # Operation timed out
    18,  # Partial file / transfer closed with data remaining
    56,  # Failure in receiving network data
}

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0  # seconds, multiplied by attempt number

# Browser-ish defaults matching phoenixthrush/AniWorld-Downloader's GLOBAL_SESSION.
_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Upgrade-Insecure-Requests": "1",
    "Priority": "u=0, i",
}

_client: AsyncSession | None = None
_lock = asyncio.Lock()


async def get_client() -> AsyncSession:
    global _client
    async with _lock:
        if _client is None:
            kwargs: dict[str, Any] = {
                "impersonate": "chrome124",
                "timeout": 25,
                "headers": {
                    "User-Agent": settings.user_agent,
                    **_BROWSER_HEADERS,
                },
            }
            if settings.proxy_url:
                kwargs["proxy"] = settings.proxy_url
            _client = AsyncSession(**kwargs)
            log.info("HTTP client initialised (curl_cffi chrome124 impersonation)")
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
        _client = None


def _is_retriable(exc: Exception) -> bool:
    """Check if an exception is a transient curl error worth retrying."""
    code = getattr(exc, "code", None)
    if code is not None and code in _RETRIABLE_CURL_CODES:
        return True
    # Also match wrapped exceptions whose __cause__ carries the code.
    cause = exc.__cause__
    if cause is not None:
        code = getattr(cause, "code", None)
        if code is not None and code in _RETRIABLE_CURL_CODES:
            return True
    return False


async def get(url: str, *, check_captcha: bool = False, **kwargs) -> str:
    """
    Fetch a URL with automatic retry on transient network errors.

    Captcha detection is OPT-IN (default off) because s.to / aniworld.to
    legitimately preload Cloudflare Turnstile scripts on every page for
    their inline player modal — flagging on that would cause false
    positives on perfectly valid episode pages.

    Scrapers should call `raise_if_captcha` themselves from the failure
    path (when parsing yields zero providers) so challenge pages still
    surface a useful error message.
    """
    c = await get_client()
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = await c.get(url, **kwargs)
            r.raise_for_status()
            text = r.text
            if check_captcha:
                raise_if_captcha(text, r.status_code, url)
            return text
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1 and _is_retriable(e):
                wait = _RETRY_BACKOFF * (attempt + 1)
                log.warning(
                    "GET %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    url, attempt + 1, _MAX_RETRIES, e, wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    raise last_err  # type: ignore[misc]


async def get_with_final_url(
    url: str, *, check_captcha: bool = False, **kwargs
) -> tuple[str, str]:
    """Return (body, final_url) following redirects. Captcha check opt-in.
    Retries automatically on transient network errors."""
    c = await get_client()
    kwargs.setdefault("allow_redirects", True)
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            r = await c.get(url, **kwargs)
            r.raise_for_status()
            text = r.text
            if check_captcha:
                raise_if_captcha(text, r.status_code, url)
            return text, str(r.url)
        except Exception as e:
            last_err = e
            if attempt < _MAX_RETRIES - 1 and _is_retriable(e):
                wait = _RETRY_BACKOFF * (attempt + 1)
                log.warning(
                    "GET %s failed (attempt %d/%d): %s — retrying in %.1fs",
                    url, attempt + 1, _MAX_RETRIES, e, wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    raise last_err  # type: ignore[misc]


async def head_final_url(url: str) -> str:
    c = await get_client()
    r = await c.head(url, allow_redirects=True)
    return str(r.url)
