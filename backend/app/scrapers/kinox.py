"""
kinox.to scraper.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse
import html as html_parser

from app.scrapers._meta import absolutize, extract_title
from app.scrapers.base import BaseScraper, EpisodeRef, SearchResult, StreamCandidate
from app.services import settings_store
from app.services.captcha import CaptchaRequiredError
from app.services.hosters import headers_for, resolve_direct_url
from app.services.http import get, get_client

log = logging.getLogger(__name__)

BASE = "https://kinox.to"


class KinoxScraper(BaseScraper):
    name = "kinox.to"

    async def search(self, query: str) -> list[SearchResult]:
        query = query.strip()

        if query.startswith("http"):
            # Direct URL paste
            try:
                html = await get(query)
                results = [await self._parse_detail_page(query, html)]
                return [r for r in results if r]
            except Exception as e:
                log.warning("kinox: direct URL fetch failed for %r: %s", query, e)
                return []

        # Otherwise title search
        url = f"{BASE}/Search.html?q={quote(query)}"
        try:
            html = await get(url)
        except Exception as e:
            log.warning("kinox: search failed for %r: %s", query, e)
            return []

        blocks = html.split('class="Opt leftOpt Headlne"')
        results: list[SearchResult] = []

        for b in blocks[1:]:
            # Extract URL and title from the headline link
            href_m = re.search(r'href="([^"]+)"', b, re.IGNORECASE)
            title_m = re.search(r'title="([^"]+)"', b, re.IGNORECASE)
            if not title_m:
                title_m = re.search(r'<h1>(.*?)</h1>', b, re.DOTALL | re.IGNORECASE)

            if not (href_m and title_m):
                continue

            movie_url = href_m.group(1).strip()
            title = title_m.group(1).strip()
            title = re.sub(r'<[^>]+>', '', title)  # strip any html tags

            if movie_url.startswith("/"):
                movie_url = BASE + movie_url

            # Extract poster
            poster = None
            m_img = re.search(r'<div class="Thumb"><img[^>]*src="([^"]+)"', b, re.IGNORECASE)
            if m_img:
                poster = m_img.group(1).strip()
                if poster.startswith("/"):
                    poster = BASE + poster

            # Extract language from search results: 1.png = de, 2.png = en
            lang = "de"
            m_lang = re.search(r'src="/gr/sys/lng/(\d+)\.png"', b, re.IGNORECASE)
            if m_lang:
                lang_id = m_lang.group(1)
                if lang_id == "1":
                    lang = "de"
                elif lang_id == "2":
                    lang = "en"

            # Fetch detail page of search result (top 8 only to avoid slowness)
            if len(results) < 8:
                try:
                    detail_html = await get(movie_url)
                    info = await self._parse_detail_page(movie_url, detail_html)
                    if info:
                        if not info.poster:
                            info.poster = poster
                        # Override language if detail page specifies it
                        if lang:
                            info.language = lang
                        results.append(info)
                        continue
                except Exception as e:
                    log.warning("kinox: failed to fetch details for %s: %s", movie_url, e)

            # Fallback to search results info
            results.append(
                SearchResult(
                    title=title,
                    url=movie_url,
                    source=self.name,
                    poster=poster,
                    language=lang
                )
            )

        return results[:30]

    async def _parse_detail_page(self, movie_url: str, html: str) -> SearchResult | None:
        title = extract_title(html)
        if not title:
            return None

        # Extract year: class="Year">(2009)
        year = None
        m_year = re.search(r'class="Year"[^>]*>\s*\(?(\d{4})\)?', html, re.IGNORECASE)
        if not m_year:
            m_year = re.search(r'\b(19|20)\d{2}\b', title)
        if m_year:
            year = int(m_year.group(1))

        # Extract language from header flag: /gr/sys/lng/(\d+).png
        language = "de"
        m_lang = re.search(r'class="[^"]*Flag"[^>]*>\s*<img[^>]*src="/gr/sys/lng/(\d+)\.png"', html, re.IGNORECASE)
        if m_lang:
            lang_id = m_lang.group(1)
            if lang_id == "2":
                language = "en"
        elif "-english" in movie_url.lower() or "english" in title.lower():
            language = "en"

        # Extract poster
        poster = None
        m_poster = re.search(r'class="[^"]*Gfx[^"]*"[^>]*>\s*<img[^>]*src="([^"]+)"', html, re.IGNORECASE)
        if not m_poster:
            m_poster = re.search(r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*cover[^"]*"', html, re.IGNORECASE)
        if m_poster:
            poster = m_poster.group(1).strip()
            if poster.startswith("/"):
                poster = BASE + poster

        return SearchResult(
            title=title,
            url=movie_url,
            year=year,
            source=self.name,
            poster=poster,
            language=language
        )

    async def list_seasons(self, slug: str) -> list[int]:
        html = await get(f"{BASE}/Stream/{slug}.html")
        m_sec = re.search(r'id="SeasonSelection".*?</select>', html, re.DOTALL)
        seasons = set()
        if m_sec:
            for m in re.finditer(r'<option\s+value="(\d+)"', m_sec.group(0)):
                seasons.add(int(m.group(1)))
        return sorted(seasons)

    async def list_episodes(self, slug: str, season: int) -> list[int]:
        html = await get(f"{BASE}/Stream/{slug}.html")
        m_sec = re.search(r'id="SeasonSelection".*?</select>', html, re.DOTALL)
        if m_sec:
            m_opt = re.search(rf'<option\s+value="{season}"\s+rel="([^"]+)"', m_sec.group(0), re.IGNORECASE)
            if m_opt:
                return [int(ep) for ep in m_opt.group(1).split(",") if ep.strip().isdigit()]
        return []

    async def episode_has_language(
        self, slug: str, season: int, episode: int, language: str
    ) -> bool:
        # Check if the requested language matches the show entry language
        html = await get(f"{BASE}/Stream/{slug}.html")
        info = await self._parse_detail_page(f"{BASE}/Stream/{slug}.html", html)
        if not info:
            return False

        expected_lang = "de" if language.startswith("de") else "en"
        show_lang = "de" if info.language.startswith("de") else "en"
        if expected_lang != show_lang:
            return False

        # Verify episode exists in that season
        eps = await self.list_episodes(slug, season)
        return episode in eps

    async def get_stream(self, target: str | EpisodeRef) -> StreamCandidate:
        if isinstance(target, str):
            return await self._get_movie_stream(target)
        else:
            return await self._get_episode_stream(target)

    async def _get_movie_stream(self, movie_url: str) -> StreamCandidate:
        html = await get(movie_url)
        # Parse movie mirrors directly from HTML
        hosters = self._parse_mirrors(html)
        if not hosters:
            raise RuntimeError(f"kinox: no mirror links found on {movie_url}")

        return await self._resolve_hosters(hosters, movie_url)

    async def _get_episode_stream(self, ep: EpisodeRef) -> StreamCandidate:
        url = f"{BASE}/Stream/{ep.slug}.html"
        html = await get(url)

        series_id = _extract_series_id(html)
        if not series_id:
            raise RuntimeError(f"kinox: could not extract SeriesID from {url}")

        # Fetch episode mirrors HTML
        mir_url = f"{BASE}/aGET/MirrorByEpisode/?Addr={ep.slug}&SeriesID={series_id}&Season={ep.season}&Episode={ep.episode}"
        mir_html = await get(mir_url)

        hosters = self._parse_mirrors(mir_html)
        if not hosters:
            raise RuntimeError(f"kinox: no mirror links found for episode S{ep.season:02d}E{ep.episode:02d}")

        return await self._resolve_hosters(hosters, url)

    def _parse_mirrors(self, html: str) -> list[dict]:
        # Extract <li id="Hoster_..." class="MirBtn..." rel="[rel]"> ... <div class="Named">[Hoster]</div>
        hosters = []
        for m in re.finditer(r'<li[^>]*class="[^"]*MirBtn[^"]*"[^>]*rel="([^"]+)"[^>]*>.*?<div class="Named">([^<]+)</div>', html, re.DOTALL | re.IGNORECASE):
            rel = html_parser.unescape(m.group(1).strip())
            name = m.group(2).strip()
            hosters.append({
                "provider": name.replace(".SX", "").replace(".to", "").strip(),
                "rel": rel
            })
        return hosters

    async def _resolve_hosters(self, hosters: list[dict], referer: str) -> StreamCandidate:
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
                log.info("kinox: trying hoster=%s rel=%s", h["provider"], h["rel"])
                
                # Fetch mirror api
                api_url = f"{BASE}/aGET/Mirror/{h['rel']}"
                res = await get(api_url, headers={"Referer": referer}, check_captcha=False)
                
                # Parse JSON
                import json
                data = json.loads(res)
                stream_iframe = data.get("Stream", "")
                
                # Extract iframe redirect URL
                m_iframe = re.search(r'src="(/redirect/[^"\s]+)"', stream_iframe)
                if not m_iframe:
                    raise RuntimeError(f"Could not extract redirect URL from iframe: {stream_iframe}")
                
                redirect_path = m_iframe.group(1)
                embed_url = await self._resolve_redirect(redirect_path, referer)

                direct = await resolve_direct_url(h["provider"], embed_url)
                if direct:
                    return StreamCandidate(
                        url=direct,
                        hoster=h["provider"],
                        language="de" if "1.png" in html_parser.unescape(referer) else "en",
                        headers=headers_for(h["provider"])
                    )
            except Exception as e:
                last_err = e
                log.warning("kinox: hoster %s failed: %s", h["provider"], e)
                continue

        raise RuntimeError(f"kinox: all hosters exhausted ({last_err})")

    @staticmethod
    async def _resolve_redirect(redirect_path: str, referer: str) -> str:
        c = await get_client()
        target = redirect_path if redirect_path.startswith("http") else BASE + redirect_path

        # We follow redirects automatically
        r = await c.get(target, headers={"Referer": referer}, allow_redirects=True)
        r.raise_for_status()

        final = str(r.url)
        netloc = urlparse(final).netloc.lower()

        # If it did not reach a known hoster, check if it's a captcha page or failed
        known_hosters = ["voe", "vidmoly", "vidoza", "dood", "filemoon", "streamtape", "vidara", "vidsonic"]
        if not any(h in netloc for h in known_hosters):
            body = r.text.lower()
            if any(term in body for term in ["verifizierung", "captcha", "rechteck", "verifiziert", "verification"]):
                raise CaptchaRequiredError(
                    f"KinoX Captcha-Verifizierung erforderlich für {final}. "
                    "Bitte KinoX im Browser öffnen und Verifizierung abschließen."
                )
            raise RuntimeError(f"KinoX redirect did not reach hoster (landed at {final})")

        return final


def _priority_rank(provider: str, priority) -> int:
    p = (provider or "").lower()
    for i, want in enumerate(priority):
        if want.lower() in p:
            return i
    return 999


def _extract_series_id(html: str) -> str | None:
    m = re.search(r'id="EntryID"[^>]*value="(\d+)"', html, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r'SeriesID=(\d+)', html, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def slug_from_url(url: str) -> str | None:
    # E.g. https://kinox.to/Stream/Avatar-Der_Herr_der_Elemente.html
    m = re.search(r"/Stream/([^/.]+)\.html", url)
    return m.group(1) if m else None
