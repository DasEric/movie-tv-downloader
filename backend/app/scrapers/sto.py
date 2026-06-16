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

_DOMAINS = ["https://s.to", "https://serienstream.to"]
# Index into _DOMAINS — rotated on failure so the next call starts with
# the domain that last worked.
_active_idx: int = 0


async def _get_with_fallback(path: str, **kwargs) -> str:
    """Try GET on the active domain; on failure rotate to the next domain."""
    global _active_idx
    last_err: Exception | None = None
    for i in range(len(_DOMAINS)):
        base = _DOMAINS[(_active_idx + i) % len(_DOMAINS)]
        try:
            html = await get(f"{base}{path}", **kwargs)
            # Success — remember this domain for future calls.
            _active_idx = (_active_idx + i) % len(_DOMAINS)
            return html
        except Exception as e:
            log.warning("s.to domain %s failed for %s: %s", base, path, e)
            last_err = e
            continue
    raise last_err or RuntimeError(f"all s.to domains failed for {path}")


def _current_base() -> str:
    """Return the currently preferred base URL (for URL construction)."""
    return _DOMAINS[_active_idx]


# Keep BASE as a constant for the redirect check — both domains are valid.
BASE = _DOMAINS[0]

# Global rate limiter for s.to redirect requests. s.to blocks redirects
# when too many fire in parallel (the /r?t=... endpoint just stays on
# s.to instead of bouncing to the hoster). A semaphore of 1 serialises
# redirect requests so they go out one at a time.
_REDIRECT_SEM = asyncio.Semaphore(1)
_REDIRECT_DELAY = 1.5  # seconds between consecutive redirect requests

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

# s.to serves show covers from its own CDN under a predictable URL pattern:
#
#   /media/images/channel/desktop/<slug>-<hash>?format=jpg
#   /media/images/channel/2x-desktop/<slug>-<hash>?format=jpg   (retina)
#
# The img tag looks like this on a show page:
#
#   <img data-src="/media/images/channel/desktop/the-rookie-CeSLm4Rq?format=jpg"
#        class="img-fluid w-100 loaded" alt="The Rookie" ...>
#
# Notice the class is "img-fluid w-100 loaded" — not "cover", "poster" or
# "seriesCoverBox" — so the generic cover-class sniffer in _meta.py misses
# it, and the fallback to og:image returns a different (wrong) show on
# s.to. This regex matches the CDN path directly on either attribute so
# we always pick the correct cover.
_STO_COVER_URL_RE = re.compile(
    r'\b(?:data-src|src)="'
    r'(?P<url>/media/images/channel/(?:2x-)?desktop/[^"]+)"',
    re.IGNORECASE,
)


def _extract_sto_poster(html: str, slug: str | None = None) -> str | None:
    """Pull the show cover from s.to's CDN pattern.

    When `slug` is provided we prefer matches whose URL contains it — that
    way a show page that also embeds promo images for OTHER shows (e.g.
    "related shows" strips) can't trick us into returning the wrong cover.
    Falls back to the first match if nothing contains the slug.
    """
    if not html:
        return None
    first: str | None = None
    for m in _STO_COVER_URL_RE.finditer(html):
        url = m.group("url")
        if first is None:
            first = url
        if slug and slug.lower() in url.lower():
            return url
    return first


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

        try:
            html = await _get_with_fallback(f"/serie/stream/{slug}")
        except Exception as e:
            log.info("s.to slug fallback %r failed: %s", slug, e)
            return []

        base = _current_base()
        url = f"{base}/serie/stream/{slug}"
        poster = absolutize(
            _extract_sto_poster(html, slug) or extract_poster(html),
            base,
        )
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
        html = await _get_with_fallback(f"/serie/stream/{slug}")
        seasons: set[int] = set()
        for m in re.finditer(r"/staffel-(\d+)", html):
            seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        html = await _get_with_fallback(f"/serie/stream/{slug}/staffel-{season}")
        eps: set[int] = set()
        for m in re.finditer(r"/episode-(\d+)", html):
            eps.add(int(m.group(1)))
        for m in re.finditer(r'itemprop="episodeNumber"\s+content="(\d+)"', html):
            eps.add(int(m.group(1)))
        return sorted(eps)

    async def fetch_show_details(self, slug: str) -> dict:
        """One-shot fetch for the UI: poster + season list + title."""
        html = await _get_with_fallback(f"/serie/stream/{slug}")
        base = _current_base()
        poster = absolutize(
            _extract_sto_poster(html, slug) or extract_poster(html),
            base,
        )
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
        """
        Return True if the episode page lists at least one hoster for the
        requested language. Used by the SeasonWatch scheduler to avoid
        spawning queue items for episodes that aren't in the user's
        language yet.
        """
        try:
            html = await _get_with_fallback(
                f"/serie/stream/{slug}/staffel-{season}/episode-{episode}"
            )
        except Exception:
            return False
        label = LANG_LABEL.get(language, "Deutsch")
        hosters = self._parse_providers(html, label)
        return bool(hosters)

    async def get_stream(self, ep: EpisodeRef) -> StreamCandidate:
        html = await _get_with_fallback(
            f"/serie/stream/{ep.slug}/staffel-{ep.season}/episode-{ep.episode}"
        )

        label = LANG_LABEL.get(ep.language, "Deutsch")
        hosters = self._parse_providers(html, label)
        if not hosters:
            # LATE captcha check: only if we can't find any providers AND
            # the page contains definitive challenge markers
            if is_captcha_page(html, 200):
                url = f"{_current_base()}/serie/stream/{ep.slug}/staffel-{ep.season}/episode-{ep.episode}"
                raise CaptchaRequiredError(
                    f"Cloudflare / captcha challenge for {url} — "
                    "curl_cffi TLS fingerprint was insufficient this time."
                )
            raise RuntimeError(
                f"s.to: {ep.slug} S{ep.season:02d}E{ep.episode:02d} "
                f"is not available in {label}"
            )

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

        s.to rate-limits redirect requests: when too many fire in
        parallel the response just stays on s.to. We serialise them
        through a semaphore and add a small delay, retrying up to 3
        times with increasing backoff.
        """
        target = play_url if play_url.startswith("http") else _current_base() + play_url

        last_err: Exception | None = None
        for attempt in range(3):
            async with _REDIRECT_SEM:
                if attempt > 0:
                    wait = _REDIRECT_DELAY * (attempt + 1)
                    log.info(
                        "s.to: redirect retry #%d for %s (waiting %.1fs)",
                        attempt, target, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    await asyncio.sleep(_REDIRECT_DELAY)

                c = await get_client()
                r = await c.get(target, allow_redirects=True)
                r.raise_for_status()

                final = str(r.url)
                netloc = urlparse(final).netloc

                if not (netloc.endswith("s.to") or netloc.endswith("serienstream.to")):
                    return final

                # Still on s.to — check if it's a captcha or rate limit
                if is_captcha_page(r.text, r.status_code):
                    raise CaptchaRequiredError(
                        f"s.to inline Turnstile modal blocked redirect for {target}."
                    )

                last_err = RuntimeError(
                    f"s.to redirect {target} did not leave the site "
                    f"(landed at {final})"
                )
                log.warning(
                    "s.to: redirect stayed on site (attempt %d/3)", attempt + 1
                )

        raise last_err or RuntimeError(f"s.to redirect failed for {target}")


def _order_by_priority(hosters: list[dict], priority) -> list[dict]:
    rank = {p.lower(): i for i, p in enumerate(priority)}
    return sorted(hosters, key=lambda h: rank.get(h["provider"].lower(), 999))


def slug_from_url(url: str) -> str | None:
    m = re.search(r"/serie/stream/([^/]+)", url)
    return m.group(1) if m else None
