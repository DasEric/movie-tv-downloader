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

BASE = "https://burning-series.io"

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
        else:
            slug = slugify(query, separator="-", lowercase=False)
            if not slug:
                return []

        url = f"{BASE}/serie/{slug}"
        try:
            html = await get(url)
        except Exception as e:
            log.info("burning-series slug fallback %r failed: %s", slug, e)
            # Try lowercase slug fallback
            slug_lower = slug.lower()
            if slug_lower != slug:
                url = f"{BASE}/serie/{slug_lower}"
                try:
                    html = await get(url)
                    slug = slug_lower
                except Exception:
                    return []
            else:
                return []

        poster = absolutize(extract_poster(html), BASE)
        title = extract_title(html, slug.replace("-", " ").title())

        return [
            SearchResult(
                title=title,
                url=url,
                source=self.name,
                poster=poster,
            )
        ]

    async def list_seasons(self, slug: str) -> list[int]:
        html = await get(f"{BASE}/serie/{slug}")
        seasons: set[int] = set()
        for m in re.finditer(r'href="/?serie/[^"]+/(\d+)(?:/[a-z]{2})?"', html, re.IGNORECASE):
            seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        # Try default and then de/en pages if needed
        html = None
        for lang_suffix in ("", "/de", "/en"):
            try:
                html = await get(f"{BASE}/serie/{slug}/{season}{lang_suffix}")
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
        html = await get(f"{BASE}/serie/{slug}")
        poster = absolutize(extract_poster(html), BASE)
        seasons = await self.list_seasons(slug)
        title = extract_title(html, slug.replace("-", " ").title())
        return {
            "title": title,
            "poster": poster,
            "seasons": seasons,
        }

    async def episode_has_language(
        self, slug: str, season: int, episode: int, language: str
    ) -> bool:
        bs_lang = BS_LANG.get(language, "de")
        try:
            html = await get(f"{BASE}/serie/{slug}/{season}/{bs_lang}")
        except Exception:
            return False

        m = re.search(rf'class="[^"]*epiInfo\s+{episode}\b[^"]*".*?<div class="links">(.*?)</div>', html, re.DOTALL)
        if not m:
            return False
        return "<a " in m.group(1)

    async def get_stream(self, ep: EpisodeRef) -> StreamCandidate:
        bs_lang = BS_LANG.get(ep.language, "de")
        url = f"{BASE}/serie/{ep.slug}/{ep.season}/{bs_lang}"
        html = await get(url)

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
                raise CaptchaRequiredError(f"Captcha challenge for {url}")
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
                player_url = BASE + "/" + h["path"].lstrip("/")
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
        target = play_url if play_url.startswith("http") else BASE + "/" + play_url.lstrip("/")

        async with _REDIRECT_SEM:
            await asyncio.sleep(_REDIRECT_DELAY)
            c = await get_client()
            r = await c.get(target, headers={"Referer": referer}, allow_redirects=True)
            r.raise_for_status()

            final = str(r.url)
            netloc = urlparse(final).netloc
            if not netloc.endswith("burning-series.io"):
                return final

            if is_captcha_page(r.text, r.status_code):
                raise CaptchaRequiredError("Turnstile / Captcha challenge on redirect page")

            raise RuntimeError(f"Redirect stayed on burning-series.io: {final}")


def _priority_rank(provider: str, priority) -> int:
    p = (provider or "").lower()
    for i, want in enumerate(priority):
        if want.lower() in p:
            return i
    return 999


def slug_from_url(url: str) -> str | None:
    m = re.search(r"/serie/([^/]+)", url)
    return m.group(1) if m else None
