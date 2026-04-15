"""
Cloudscraper fallback for the HTTP client.

curl_cffi's Chrome-impersonation gets us through nearly every Cloudflare
gate we encounter on s.to / aniworld.to / megakino. When it doesn't — we
get a 403 body that reads "Just a moment…" — this module runs VeNoMouS's
cloudscraper in a thread executor as a last-resort fallback. Opt-in via
the `cloudflare_fallback_enabled` setting (default true).

The scraper is lazily initialised once per process. It is NOT async —
cloudscraper builds on top of `requests`, so the actual call is dispatched
to the default thread pool.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

_CF_MARKERS = (
    "just a moment",
    "cf-challenge-running",
    "cf_chl_opt",
    "cf-mitigated",
    "attention required",
    "checking your browser before accessing",
)


def looks_like_cloudflare_challenge(body: str, status_code: int) -> bool:
    """Heuristic: is this response a Cloudflare interstitial we could maybe
    defeat by switching to cloudscraper?"""
    if status_code not in (403, 503):
        return False
    if not body:
        return False
    lower = body[:4000].lower()
    return any(m in lower for m in _CF_MARKERS)


_scraper: Any | None = None
_import_failed = False


def _get_scraper() -> Any | None:
    """Lazy init. Returns None if cloudscraper isn't importable (e.g. a
    dev machine that hasn't installed the vendored package yet) — the
    caller treats None as "fallback unavailable" and re-raises."""
    global _scraper, _import_failed
    if _scraper is not None:
        return _scraper
    if _import_failed:
        return None
    try:
        import cloudscraper  # type: ignore

        _scraper = cloudscraper.create_scraper(
            browser={
                "browser": "chrome",
                "platform": "windows",
                "desktop": True,
            }
        )
        log.info("cloudscraper fallback initialised")
        return _scraper
    except Exception as e:
        _import_failed = True
        log.warning("cloudscraper not available (%s); fallback disabled", e)
        return None


def _blocking_fetch(url: str, headers: dict | None, proxy: str | None, timeout: int) -> tuple[str, int]:
    scraper = _get_scraper()
    if scraper is None:
        raise RuntimeError("cloudscraper fallback unavailable")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = scraper.get(url, headers=headers or None, proxies=proxies, timeout=timeout)
    return r.text, r.status_code


async def fetch_via_cloudscraper(
    url: str,
    *,
    headers: dict | None = None,
    proxy: str | None = None,
    timeout: int = 30,
) -> tuple[str, int]:
    """Run the blocking cloudscraper GET in a thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _blocking_fetch, url, headers, proxy, timeout
    )
