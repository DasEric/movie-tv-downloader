"""
aniworld.to scraper.

HTML contract (verified against phoenixthrush/AniWorld-Downloader::aniworld_to/episode.py):

  <ul class="row">
    <li class="… episodeLinkNNN" data-lang-key="1" data-link-id="NNN"
        data-link-target="/redirect/NNN">
      <a class="watchEpisode" href="/redirect/NNN">
        <h4>VOE</h4>
      </a>
    </li>
    …

Language key map (reference config.py::LANG_KEY_MAP):
  "1" -> German Dub       (Audio.GERMAN,   Subtitles.NONE)
  "2" -> English Sub      (Audio.JAPANESE, Subtitles.ENGLISH)
  "3" -> German Sub       (Audio.JAPANESE, Subtitles.GERMAN)
  "4" -> English Dub      (Audio.ENGLISH,  Subtitles.NONE)

Our UI uses simpler labels, so we translate them here:
  de / de-dub        -> "1"
  en / en-dub        -> "4"
  de-sub             -> "3"
  en-sub             -> "2"
"""
from __future__ import annotations

import logging
import re

from slugify import slugify

from app.scrapers._meta import absolutize, extract_poster, extract_title
from app.scrapers.base import BaseScraper, EpisodeRef, SearchResult, StreamCandidate
from app.services import settings_store
from app.services.captcha import CaptchaRequiredError, is_captcha_page
from app.services.hosters import headers_for, resolve_direct_url
from app.services.http import get, get_client

log = logging.getLogger(__name__)

BASE = "https://aniworld.to"

LANG_KEY = {
    "de": "1",
    "de-dub": "1",
    "en": "4",
    "en-dub": "4",
    "de-sub": "3",
    "en-sub": "2",
}

_LI_RE = re.compile(
    r'<li\s+[^>]*data-lang-key="(?P<key>\d+)"[^>]*>(?P<content>.*?)</li>',
    re.DOTALL,
)
_H4_RE = re.compile(r"<h4>(.*?)</h4>", re.DOTALL)
_WATCH_RE = re.compile(
    r'<a\s+[^>]*class="watchEpisode"[^>]*href="([^"]+)"',
    re.DOTALL,
)

class AniworldScraper(BaseScraper):
    name = "aniworld"

    async def search(self, query: str) -> list[SearchResult]:
        """
        Two-strategy search (same pattern as the s.to scraper):

          1. Direct URL paste → extract slug, fetch page, populate poster.
          2. Slug fallback:    slugify → fetch page, populate poster.

        In both cases we fetch the show page and pull out the poster +
        canonical title, so the grid card has everything it needs without
        a second round-trip.
        """
        query = query.strip()

        if query.startswith("http"):
            slug = slug_from_url(query)
            if not slug:
                return []
        else:
            slug = slugify(query, separator="-", lowercase=True)
            if not slug:
                return []

        url = f"{BASE}/anime/stream/{slug}"
        try:
            html = await get(url)
        except Exception as e:
            log.info("aniworld slug fallback %r failed: %s", slug, e)
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
        html = await get(f"{BASE}/anime/stream/{slug}")
        seasons: set[int] = set()
        for m in re.finditer(r"/staffel-(\d+)", html):
            seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        html = await get(f"{BASE}/anime/stream/{slug}/staffel-{season}")
        eps: set[int] = set()
        for m in re.finditer(r"/episode-(\d+)", html):
            eps.add(int(m.group(1)))
        for m in re.finditer(
            r'itemprop="episodeNumber"\s+content="(\d+)"', html
        ):
            eps.add(int(m.group(1)))
        return sorted(eps)

    async def fetch_show_details(self, slug: str) -> dict:
        """One-shot fetch for the UI: poster + season list + title."""
        html = await get(f"{BASE}/anime/stream/{slug}")
        poster = absolutize(extract_poster(html), BASE)
        seasons: set[int] = set()
        for m in re.finditer(r"/staffel-(\d+)", html):
            seasons.add(int(m.group(1)))
        title = extract_title(html, slug.replace("-", " ").title())
        return {
            "title": title,
            "poster": poster,
            "seasons": sorted(seasons),
        }

    async def get_stream(self, ep: EpisodeRef) -> StreamCandidate:
        url = f"{BASE}/anime/stream/{ep.slug}/staffel-{ep.season}/episode-{ep.episode}"
        html = await get(url)

        target_key = LANG_KEY.get(ep.language, "1")
        hosters = self._parse_providers(html, target_key)
        if not hosters:
            # fallback: any language
            hosters = self._parse_providers(html, None)
        if not hosters:
            # LATE captcha check — only if parsing yielded nothing
            if is_captcha_page(html, 200):
                raise CaptchaRequiredError(
                    f"Cloudflare / captcha challenge for {url}"
                )
            raise RuntimeError(f"aniworld: no hosters found for {url}")

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
                    "aniworld: trying %s for %s S%02dE%02d",
                    h["provider"],
                    ep.show,
                    ep.season,
                    ep.episode,
                )
                embed_url = await self._follow_redirect(h["redirect"])
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
                log.warning("aniworld hoster %s failed: %s", h["provider"], e)
                continue

        raise RuntimeError(f"aniworld: all hosters exhausted ({last_err})")

    # ---- helpers ----

    @staticmethod
    def _parse_providers(html: str, lang_key: str | None) -> list[dict]:
        """
        Returns [{provider, redirect, lang_key}, ...] matching the requested
        data-lang-key, or all of them when `lang_key` is None.
        """
        out: list[dict] = []
        for m in _LI_RE.finditer(html):
            key = m.group("key")
            if lang_key and key != lang_key:
                continue
            block = m.group("content")
            h4 = _H4_RE.search(block)
            a = _WATCH_RE.search(block)
            if not (h4 and a):
                continue
            out.append(
                {
                    "provider": h4.group(1).strip(),
                    "redirect": a.group(1).strip(),
                    "lang_key": key,
                }
            )
        return out

    @staticmethod
    async def _follow_redirect(redirect_path: str) -> str:
        """Follow /redirect/<id> -> actual hoster embed URL."""
        c = await get_client()
        target = (
            redirect_path if redirect_path.startswith("http") else BASE + redirect_path
        )
        r = await c.get(target, allow_redirects=True)
        r.raise_for_status()
        final = str(r.url)
        # We only flag if the final URL is still on aniworld AND the body
        # is a definitive challenge page. Otherwise fall through.
        if final.startswith(BASE) and is_captcha_page(r.text, r.status_code):
            raise CaptchaRequiredError(f"captcha on aniworld redirect {target}")
        return final


def _priority_rank(provider: str, priority) -> int:
    p = (provider or "").lower()
    for i, want in enumerate(priority):
        if want.lower() in p:
            return i
    return 999


def slug_from_url(url: str) -> str | None:
    m = re.search(r"/anime/stream/([^/]+)", url)
    return m.group(1) if m else None
