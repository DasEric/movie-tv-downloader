"""
s.to / serienstream.to scraper.

HTML contract:

    <... data-play-url="/r?t=..."
         data-provider-name="VOE"
         data-language-label="Deutsch">

  - Language selection is by the human-readable label "Deutsch" / "Englisch".
  - Attributes appear in the source in any order — we parse them separately
    and join by their enclosing <li> so attribute-order drift doesn't break us.
  - Clicking data-play-url bounces off s.to and lands on the provider embed.
    Rarely, the site shows an inline Turnstile modal (player-prepare-turnstile)
    — we detect that by name and surface it as a CaptchaRequiredError.
"""
from __future__ import annotations

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

BASE = "https://s.to"

# Language selection map — s.to uses human-readable labels
LANG_LABEL = {
    "de": "Deutsch",
    "de-dub": "Deutsch",
    "en": "Englisch",
    "en-dub": "Englisch",
    # s.to has no dedicated sub tracks — fall back to dub
    "de-sub": "Deutsch",
    "en-sub": "Englisch",
}

# Attribute-independent extraction: pick each attribute wherever it sits
# inside the containing element.
_PLAY_RE = re.compile(r'data-play-url="([^"]+)"')
_PROVIDER_NAME_RE = re.compile(r'data-provider-name="([^"]+)"')
_LANG_LABEL_RE = re.compile(r'data-language-label="([^"]+)"')

# The element that ties them together — usually <li ...> but we match any tag
# that carries all three attributes.
_PROVIDER_ELEMENT_RE = re.compile(
    r"<[a-zA-Z][^>]*?"
    r"(?=[^>]*data-play-url=)"
    r"(?=[^>]*data-provider-name=)"
    r"(?=[^>]*data-language-label=)"
    r"[^>]*>",
    re.DOTALL,
)

class StoScraper(BaseScraper):
    name = "s.to"

    async def search(self, query: str) -> list[SearchResult]:
        """
        Two-strategy search — s.to doesn't expose a reliable HTML search
        endpoint, so we rely on the slug fallback which is very accurate
        for exact titles.

          1. Direct URL paste → extract slug, fetch page, populate poster.
          2. Slug fallback:    slugify → fetch page, populate poster.

        Either way we ALWAYS fetch the show page so the grid card has a
        poster + canonical title without a second round-trip.
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

        url = f"{BASE}/serie/stream/{slug}"
        try:
            html = await get(url)
        except Exception as e:
            log.info("s.to slug fallback %r failed: %s", slug, e)
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
        html = await get(f"{BASE}/serie/stream/{slug}")
        seasons: set[int] = set()
        for m in re.finditer(r"/staffel-(\d+)", html):
            seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        html = await get(f"{BASE}/serie/stream/{slug}/staffel-{season}")
        eps: set[int] = set()
        for m in re.finditer(r"/episode-(\d+)", html):
            eps.add(int(m.group(1)))
        for m in re.finditer(r'itemprop="episodeNumber"\s+content="(\d+)"', html):
            eps.add(int(m.group(1)))
        return sorted(eps)

    async def fetch_show_details(self, slug: str) -> dict:
        """One-shot fetch for the UI: poster + season list + title."""
        html = await get(f"{BASE}/serie/stream/{slug}")
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
        url = f"{BASE}/serie/stream/{ep.slug}/staffel-{ep.season}/episode-{ep.episode}"
        html = await get(url)

        hosters = self._parse_providers(html, LANG_LABEL.get(ep.language, "Deutsch"))
        if not hosters:
            # Fallback: any language label
            hosters = self._parse_providers(html, None)
        if not hosters:
            # LATE captcha check: only if we can't find any providers AND
            # the page contains definitive challenge markers
            if is_captcha_page(html, 200):
                raise CaptchaRequiredError(
                    f"Cloudflare / captcha challenge for {url} — "
                    "curl_cffi TLS fingerprint was insufficient this time."
                )
            raise RuntimeError(f"s.to: no hosters found for {url}")

        priority = await settings_store.get(
            "hoster_priority", ["VOE", "Vidmoly", "Vidoza", "Doodstream"]
        )
        ordered = _order_by_priority(hosters, priority)

        last_err: Exception | None = None
        for h in ordered:
            try:
                log.info(
                    "s.to: trying hoster=%s for %s S%02dE%02d",
                    h["provider"],
                    ep.show,
                    ep.season,
                    ep.episode,
                )
                embed_url = await self._resolve_redirect(h["play"])
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
                log.warning("s.to hoster %s failed: %s", h["provider"], e)
                continue

        raise RuntimeError(f"s.to: all hosters exhausted ({last_err})")

    # ---- helpers ----

    @staticmethod
    def _parse_providers(html: str, language_label: str | None) -> list[dict]:
        """
        Parse provider elements regardless of attribute order.
        For each element that has all three required data- attributes, we
        extract them independently with attribute-name-anchored regexes.
        """
        out: list[dict] = []
        for m in _PROVIDER_ELEMENT_RE.finditer(html):
            element = m.group(0)
            play_m = _PLAY_RE.search(element)
            prov_m = _PROVIDER_NAME_RE.search(element)
            lang_m = _LANG_LABEL_RE.search(element)
            if not (play_m and prov_m and lang_m):
                continue
            lang = lang_m.group(1)
            if language_label and lang != language_label:
                continue
            out.append(
                {
                    "play": play_m.group(1),
                    "provider": prov_m.group(1),
                    "lang": lang,
                }
            )
        # de-duplicate on (provider, play)
        seen: set[tuple[str, str]] = set()
        dedup: list[dict] = []
        for h in out:
            key = (h["provider"], h["play"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(h)
        return dedup

    @staticmethod
    async def _resolve_redirect(play_url: str) -> str:
        """
        Follow the s.to /r?t=... redirect to the actual hoster embed page.
        Raises CaptchaRequiredError only if we land on a confirmed challenge.
        """
        c = await get_client()
        target = play_url if play_url.startswith("http") else BASE + play_url
        r = await c.get(target, allow_redirects=True)
        r.raise_for_status()

        final = str(r.url)
        netloc = urlparse(final).netloc
        # Still on s.to after the redirect → inline modal was shown
        if netloc.endswith("s.to") or netloc.endswith("serienstream.to"):
            if is_captcha_page(r.text, r.status_code):
                raise CaptchaRequiredError(
                    f"s.to inline Turnstile modal blocked redirect for {target}."
                )
            # Not a captcha — the redirect just didn't fire. Raise a normal error.
            raise RuntimeError(
                f"s.to redirect {target} did not leave the site (landed at {final})"
            )
        return final


def _order_by_priority(hosters: list[dict], priority) -> list[dict]:
    rank = {p.lower(): i for i, p in enumerate(priority)}
    return sorted(hosters, key=lambda h: rank.get(h["provider"].lower(), 999))


def slug_from_url(url: str) -> str | None:
    m = re.search(r"/serie/stream/([^/]+)", url)
    return m.group(1) if m else None
