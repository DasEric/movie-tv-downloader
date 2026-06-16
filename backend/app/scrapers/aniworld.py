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
import time

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

# Human-readable labels for lang keys (used in error messages + UI)
LANG_LABEL = {
    "1": "GerDub",
    "2": "EngSub",
    "3": "GerSub",
    "4": "EngDub",
}

# Fallback chain: if the preferred language isn't available, try the next
# one in the chain. Typical anime scenario: GerDub rarely exists, but
# GerSub (Japanese audio + German subs) usually does.
LANG_FALLBACK: dict[str, list[str]] = {
    "de":     ["de-sub", "en-sub"],   # GerDub -> GerSub -> EngSub
    "de-dub": ["de-sub", "en-sub"],
    "de-sub": ["en-sub"],             # GerSub -> EngSub
    "en":     ["en-sub"],             # EngDub -> EngSub
    "en-dub": ["en-sub"],
    "en-sub": [],                     # no fallback
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

_season_german_cache: dict[tuple[str, int], tuple[float, bool]] = {}
_SEASON_CACHE_TTL = 300.0  # 5 minutes cache TTL for season page checks


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

    async def discover(self, page: int = 1, category: str | None = None) -> list[SearchResult]:
        url_path = "/"
        if category == "popular":
            url_path = "/beliebte-animes"
        elif category == "series":
            url_path = "/animes"

        try:
            html = await get(BASE + url_path)
        except Exception as e:
            log.warning("aniworld discover failed: %s", e)
            return []

        results = []
        seen_urls = set()
        import html as html_lib

        # 1. First search for cards with image tags (handles popular and home page)
        matches = re.finditer(r'<a[^>]*href="(/anime/stream/[a-z0-9\-]+)"[^>]*>.*?<img(?P<img>[^>]+)>', html, re.DOTALL | re.IGNORECASE)
        for m in matches:
            href = m.group(1)
            img_tag = m.group("img")
            
            # Find alt (title)
            alt_m = re.search(r'alt="([^"]+)"', img_tag, re.IGNORECASE)
            title = alt_m.group(1).strip() if alt_m else ""
            if not title:
                continue
                
            # Find data-src, fallback to src
            src_m = re.search(r'data-src="([^"]+)"', img_tag, re.IGNORECASE)
            if not src_m:
                src_m = re.search(r'src="([^"]+)"', img_tag, re.IGNORECASE)
            
            img_src = src_m.group(1) if src_m else None
            
            full_url = BASE + href
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                results.append(SearchResult(
                    title=html_lib.unescape(title),
                    url=full_url,
                    source=self.name,
                    poster=absolutize(img_src, BASE)
                ))

        # 2. Fallback / full list (handles /animes list page which has no images)
        if not results:
            matches = re.finditer(r'<a[^>]*href="(/anime/stream/(?P<slug>[a-z0-9\-]+))"[^>]*>(?P<title>[^<]+)</a>', html, re.IGNORECASE)
            for m in matches:
                href = m.group(1)
                title = m.group("title").strip()
                full_url = BASE + href
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    results.append(SearchResult(
                        title=html_lib.unescape(title),
                        url=full_url,
                        source=self.name,
                        poster=None
                    ))
                    
        # Apply pagination slicing
        start = (page - 1) * 30
        end = page * 30
        return results[start:end]

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

    async def episode_has_language(
        self, slug: str, season: int, episode: int, language: str
    ) -> bool:
        """True iff the episode is available in the requested language OR
        any of its fallbacks (e.g. GerDub -> GerSub -> EngSub)."""
        resolved = await self.resolve_episode_language(
            slug, season, episode, language
        )
        return resolved is not None

    async def _get_season_candidates(
        self, slug: str, season: int, language: str, candidates: list[str]
    ) -> list[str]:
        # Robust fallback control: if the user wants German, but German is not yet available for this episode,
        # we check if other episodes in this season have German. If they do, we do NOT fall back to English
        # (we want to wait for the German release). We only fall back to English if the entire season has
        # no German releases at all.
        if language in ("de", "de-dub", "de-sub") and any("en" in c for c in candidates):
            cache_key = (slug, season)
            now = time.monotonic()
            has_german_in_season = None
            if cache_key in _season_german_cache:
                ts, val = _season_german_cache[cache_key]
                if now - ts < _SEASON_CACHE_TTL:
                    has_german_in_season = val

            if has_german_in_season is None:
                try:
                    season_url = f"{BASE}/anime/stream/{slug}/staffel-{season}"
                    season_html = await get(season_url)
                    has_german_in_season = ("german.svg" in season_html or "japanese-german.svg" in season_html)
                    _season_german_cache[cache_key] = (now, has_german_in_season)
                except Exception as e:
                    log.warning("Failed to fetch season page to verify language fallback: %s", e)
                    has_german_in_season = False

            if has_german_in_season:
                # Prune English candidates
                candidates = [c for c in candidates if "en" not in c]
        return candidates

    async def resolve_episode_language(
        self, slug: str, season: int, episode: int, language: str
    ) -> str | None:
        """
        Return the actual language that will be used for the episode,
        walking the fallback chain. Returns None if nothing is available.

        Example: language="de" (GerDub) but only GerSub exists
                 -> returns "de-sub"
        """
        url = f"{BASE}/anime/stream/{slug}/staffel-{season}/episode-{episode}"
        try:
            html = await get(url)
        except Exception:
            return None

        # Try requested language first, then each fallback
        candidates = [language] + LANG_FALLBACK.get(language, [])
        candidates = await self._get_season_candidates(slug, season, language, candidates)
        for lang in candidates:
            key = LANG_KEY.get(lang, "1")
            if self._parse_providers(html, key):
                return lang
        return None

    async def get_stream(self, ep: EpisodeRef) -> StreamCandidate:
        url = f"{BASE}/anime/stream/{ep.slug}/staffel-{ep.season}/episode-{ep.episode}"
        html = await get(url)

        # Walk the fallback chain: e.g. GerDub -> GerSub -> EngSub
        candidates = [ep.language] + LANG_FALLBACK.get(ep.language, [])
        candidates = await self._get_season_candidates(ep.slug, ep.season, ep.language, candidates)
        hosters: list[dict] = []
        actual_lang = ep.language
        for lang in candidates:
            key = LANG_KEY.get(lang, "1")
            hosters = self._parse_providers(html, key)
            if hosters:
                actual_lang = lang
                if lang != ep.language:
                    log.info(
                        "aniworld: %s S%02dE%02d not in %s, falling back to %s",
                        ep.slug, ep.season, ep.episode, ep.language, lang,
                    )
                break

        if not hosters:
            # LATE captcha check — only if parsing yielded nothing
            if is_captcha_page(html, 200):
                raise CaptchaRequiredError(
                    f"Cloudflare / captcha challenge for {url}"
                )
            tried = ", ".join(
                LANG_LABEL.get(LANG_KEY.get(l, "1"), l) for l in candidates
            )
            raise RuntimeError(
                f"aniworld: {ep.slug} S{ep.season:02d}E{ep.episode:02d} "
                f"is not available in any of: {tried}"
            )

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
                        language=actual_lang,
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
