"""
burning-series.io scraper.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from slugify import slugify

from app.scrapers._meta import absolutize, extract_poster, extract_title
from app.scrapers.base import BaseScraper, EpisodeRef, SearchResult, StreamCandidate
from app.services import settings_store
from app.services.captcha import CaptchaRequiredError, is_captcha_page
from app.services.hosters import headers_for, resolve_direct_url
from app.services.http import get, get_client

log = logging.getLogger(__name__)

_DOMAINS = ["https://burning-series.io", "https://burning-series.net", "https://bs.to"]
_active_idx: int = 0


async def _get_with_fallback(path: str, **kwargs) -> str:
    """Try GET on the active domain; on failure rotate to the next domain."""
    global _active_idx
    kwargs.setdefault("timeout", 5)
    last_err: Exception | None = None
    for i in range(len(_DOMAINS)):
        base = _DOMAINS[(_active_idx + i) % len(_DOMAINS)]
        try:
            html = await get(f"{base}{path}", **kwargs)
            # Success — remember this domain for future calls.
            _active_idx = (_active_idx + i) % len(_DOMAINS)
            return html
        except Exception as e:
            log.warning("burning-series domain %s failed for %s: %s", base, path, e)
            last_err = e
            continue
    raise last_err or RuntimeError(f"all burning-series domains failed for {path}")


def _current_base() -> str:
    """Return the currently preferred base URL."""
    return _DOMAINS[_active_idx]


# Keep BASE as the first domain for some legacy checks
BASE = _DOMAINS[0]

BS_LANG = {
    "de": "de",
    "de-dub": "de",
    "de-sub": "de",
    "en": "en",
    "en-dub": "en",
    "en-sub": "en",
}

# Semaphore to serialize stream redirect requests
_REDIRECT_SEM = asyncio.Semaphore(1)
_REDIRECT_DELAY = 1.5


class BurningSeriesScraper(BaseScraper):
    name = "burning-series.io"

    async def search(self, query: str) -> list[SearchResult]:
        query = query.strip()

        if query.startswith("http"):
            slug = slug_from_url(query)
            if not slug:
                return []
            try:
                html = await _get_with_fallback(f"/serie/{slug}")
                base = _current_base()
                poster = absolutize(extract_poster(html), base)
                title = extract_title(html, slug.replace("-", " ").title())
                return [
                    SearchResult(
                        title=title,
                        url=f"{base}/serie/{slug}",
                        source=self.name,
                        poster=poster,
                    )
                ]
            except Exception:
                return []

        # Otherwise, search in `/andere-serien` page (in-memory lookup across all titles and slugs)
        try:
            html_content = await _get_with_fallback("/andere-serien")
        except Exception as e:
            log.warning("burning-series search index fetch failed: %s", e)
            return []

        import html as html_lib
        matches = re.finditer(r'<a[^>]*href=["\']/?(serie/([^"\'/]+))["\'][^>]*>(.*?)</a>', html_content, re.IGNORECASE)
        results = []
        seen_slugs = set()
        
        q_lower = query.lower()
        base = _current_base()
        
        for m in matches:
            slug = m.group(2)
            text = m.group(3)
            text_clean = re.sub(r'<[^>]+>', '', text).strip()
            text_unescaped = html_lib.unescape(text_clean)
            
            if q_lower in text_unescaped.lower() or q_lower in slug.lower():
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    results.append(SearchResult(
                        title=text_unescaped,
                        url=f"{base}/serie/{slug}",
                        source=self.name,
                        poster=None
                    ))
                    
        # Sort results: exact match or starts-with first, then general substring
        def sort_key(item: SearchResult):
            title_lower = item.title.lower()
            if title_lower == q_lower:
                return 0
            if title_lower.startswith(q_lower):
                return 1
            return 2

        results.sort(key=sort_key)
        return results[:30]

    async def discover(self, page: int = 1, category: str | None = None) -> list[SearchResult]:
        import html as html_lib
        base = _current_base()
        if category == "popular":
            # Fetch homepage for popular/recent series
            try:
                html_content = await _get_with_fallback("/")
            except Exception as e:
                log.warning("burningseries popular discover failed: %s", e)
                return []
            
            # Extract series links from homepage
            matches = re.finditer(r'<a[^>]*href=["\']/?(serie/([^"\'/]+)(/[^"\']*)?)["\'][^>]*>(.*?)</a>', html_content, re.IGNORECASE)
            
            results = []
            seen_slugs = set()
            for m in matches:
                slug = m.group(2)
                text = m.group(4)
                text_clean = re.sub(r'<[^>]+>', '', text).strip()
                if not text_clean or text_clean.lower() in ("home", "suche", "forum", "hilfe", "team", "regeln", "statistiken"):
                    continue
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    results.append(SearchResult(
                        title=html_lib.unescape(text_clean),
                        url=f"{base}/serie/{slug}",
                        source=self.name,
                        poster=None
                    ))
            
            start = (page - 1) * 30
            end = page * 30
            return results[start:end]
            
        else:
            # Fetch all series from /andere-serien
            try:
                html_content = await _get_with_fallback("/andere-serien")
            except Exception as e:
                log.warning("burningseries discover failed: %s", e)
                return []

            matches = re.finditer(r'<a[^>]*href=["\']/?(serie/([^"\'/]+))["\'][^>]*>(.*?)</a>', html_content, re.IGNORECASE)
            results = []
            seen_slugs = set()
            
            for m in matches:
                slug = m.group(2)
                text = m.group(3)
                text_clean = re.sub(r'<[^>]+>', '', text).strip()
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    results.append(SearchResult(
                        title=html_lib.unescape(text_clean),
                        url=f"{base}/serie/{slug}",
                        source=self.name,
                        poster=None
                    ))
            
            start = (page - 1) * 30
            end = page * 30
            return results[start:end]

    async def list_seasons(self, slug: str) -> list[int]:
        html = await _get_with_fallback(f"/serie/{slug}")
        seasons: set[int] = set()
        for m in re.finditer(r'href="/?serie/[^"]+/(\d+)(?:/[a-z]{2})?"', html, re.IGNORECASE):
            seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        html = None
        for lang_suffix in ("", "/de", "/en"):
            try:
                html = await _get_with_fallback(f"/serie/{slug}/{season}{lang_suffix}")
                break
            except Exception:
                continue
        if not html:
            return []

        eps: set[int] = set()
        for m in re.finditer(r'class="e(\d+)', html):
            eps.add(int(m.group(1)))
        for m in re.finditer(r'class="epiInfo\s+(\d+)"', html):
            eps.add(int(m.group(1)))
        return sorted(eps)

    async def fetch_show_details(self, slug: str) -> dict:
        html = await _get_with_fallback(f"/serie/{slug}")
        base = _current_base()
        poster = absolutize(extract_poster(html), base)
        seasons: set[int] = set()
        for m in re.finditer(r'href="/?serie/[^"]+/(\d+)(?:/[a-z]{2})?"', html, re.IGNORECASE):
            seasons.add(int(m.group(1)))
        title = extract_title(html, slug.replace("-", " ").title())
        return {
            "title": title,
            "poster": poster,
            "seasons": sorted(seasons),
        }

    async def episode_has_language(
        self, slug: str, season: int, episode: int, language: str
    ) -> bool:
        bs_lang = BS_LANG.get(language, "de")
        try:
            html = await _get_with_fallback(f"/serie/{slug}/{season}/{bs_lang}")
        except Exception:
            return False

        m = re.search(rf'class="[^"]*epiInfo\s+{episode}\b[^"]*".*?<div class="links">(.*?)</div>', html, re.DOTALL)
        if not m:
            return False
        return "<a " in m.group(1)

    async def get_stream(self, ep: EpisodeRef) -> StreamCandidate:
        bs_lang = BS_LANG.get(ep.language, "de")
        base = _current_base()
        url_path = f"/serie/{ep.slug}/{ep.season}/{bs_lang}"
        html = await _get_with_fallback(url_path)

        m = re.search(rf'class="[^"]*epiInfo\s+{ep.episode}\b[^"]*".*?<div class="links">(.*?)</div>', html, re.DOTALL)
        if not m:
            raise RuntimeError(f"burning-series: S{ep.season:02d}E{ep.episode:02d} not found")

        links_block = m.group(1)
        hosters = []
        for href_m, title_m in re.findall(r'href="([^"]+)"\s+title="([^"]+)"', links_block):
            hosters.append({
                "provider": title_m.strip(),
                "path": href_m.strip(),
            })

        if not hosters:
            if is_captcha_page(html, 200):
                raise CaptchaRequiredError(f"Captcha challenge for {base}{url_path}")
            raise RuntimeError(f"burning-series: S{ep.season:02d}E{ep.episode:02d} has no stream links")

        priority = await settings_store.get(
            "hoster_priority", ["VOE", "Vidmoly", "Vidoza", "Doodstream"]
        )
        ordered = sorted(
            hosters,
            key=lambda h: _priority_rank(h["provider"], priority),
        )

        last_err: Exception | None = None
        for h in ordered:
            try:
                log.info(
                    "burning-series: trying hoster=%s for %s S%02dE%02d",
                    h["provider"],
                    ep.show,
                    ep.season,
                    ep.episode,
                )
                player_url = base + "/" + h["path"].lstrip("/")
                player_html = await get(player_url)

                # Find the redirect link inside the hoster player
                m_redirect = re.search(
                    r'class="hoster-player"[^>]*onclick="[^"]*window\.open\(\'([^\']+)\'',
                    player_html
                )
                if not m_redirect:
                    m_redirect = re.search(r"window\.open\('([^']+)'", player_html)

                if not m_redirect:
                    raise RuntimeError("Could not extract player stream link from onclick/window.open")

                stream_redirect_url = m_redirect.group(1)
                embed_url = await self._resolve_redirect(stream_redirect_url, player_url)

                direct = await resolve_direct_url(h["provider"], embed_url)
                if direct:
                    return StreamCandidate(
                        url=direct,
                        hoster=h["provider"],
                        language=ep.language,
                        headers=headers_for(h["provider"]),
                    )
            except Exception as e:
                last_err = e
                log.warning("burning-series hoster %s failed: %s", h["provider"], e)
                continue

        raise RuntimeError(f"burning-series: all hosters exhausted ({last_err})")

    @staticmethod
    async def _resolve_redirect(play_url: str, referer: str) -> str:
        base = _current_base()
        target = play_url if play_url.startswith("http") else base + "/" + play_url.lstrip("/")

        async with _REDIRECT_SEM:
            await asyncio.sleep(_REDIRECT_DELAY)
            c = await get_client()
            r = await c.get(target, headers={"Referer": referer}, allow_redirects=True)
            r.raise_for_status()

            final = str(r.url)
            netloc = urlparse(final).netloc.lower()
            if not any(netloc.endswith(d) for d in ("burning-series.io", "burning-series.net", "bs.to")):
                return final

            if is_captcha_page(r.text, r.status_code):
                raise CaptchaRequiredError("Turnstile / Captcha challenge on redirect page")

            raise RuntimeError(f"Redirect stayed on {netloc}: {final}")


def _priority_rank(provider: str, priority) -> int:
    p = (provider or "").lower()
    for i, want in enumerate(priority):
        if want.lower() in p:
            return i
    return 999


def slug_from_url(url: str) -> str | None:
    m = re.search(r"/serie/([^/]+)", url)
    return m.group(1) if m else None
