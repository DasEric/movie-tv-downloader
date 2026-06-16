"""
filmpalast.to scraper.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from app.scrapers._meta import absolutize, extract_title
from app.scrapers.base import BaseScraper, SearchResult, StreamCandidate
from app.services import settings_store
from app.services.hosters import headers_for, resolve_direct_url
from app.services.http import get

log = logging.getLogger(__name__)

BASE = "https://filmpalast.to"


class FilmpalastScraper(BaseScraper):
    name = "filmpalast.to"

    async def search(self, query: str) -> list[SearchResult]:
        query = query.strip()

        if query.startswith("http"):
            # Direct URL paste
            try:
                html = await get(query)
                results = [await self._parse_detail_page(query, html)]
                return [r for r in results if r]
            except Exception as e:
                log.warning("filmpalast: direct URL fetch failed for %r: %s", query, e)
                return []

        # Otherwise title search
        url = f"{BASE}/search/title/{quote(query)}"
        try:
            html = await get(url)
        except Exception as e:
            log.warning("filmpalast: search failed for %r: %s", query, e)
            return []

        # Split by article blocks
        articles = html.split('<article class="liste')
        results: list[SearchResult] = []

        for art in articles[1:]:
            # Extract title and URL inside h2 tag
            h2_m = re.search(r'<h2[^>]*>(.*?)</h2>', art, re.DOTALL | re.IGNORECASE)
            if not h2_m:
                continue
            h2_content = h2_m.group(1)
            href_m = re.search(r'href="([^"]+)"', h2_content, re.IGNORECASE)
            title_m = re.search(r'title="([^"]+)"', h2_content, re.IGNORECASE)

            if not (href_m and title_m):
                continue

            movie_url = href_m.group(1).strip()
            title = title_m.group(1).strip()

            if movie_url.startswith("//"):
                movie_url = "https:" + movie_url
            elif movie_url.startswith("/"):
                movie_url = BASE + movie_url

            # Extract poster
            poster = None
            m_poster = re.search(r'<img\s+[^>]*src="([^"]+)"\s+class="cover-opacity"', art, re.IGNORECASE)
            if m_poster:
                poster = m_poster.group(1).strip()
                if poster.startswith("//"):
                    poster = "https:" + poster
                elif poster.startswith("/"):
                    poster = BASE + poster

            # Fetch the detail page of this movie to get year and language
            try:
                detail_html = await get(movie_url)
                info = await self._parse_detail_page(movie_url, detail_html)
                if info:
                    # Keep poster from search page if not found in detail page
                    if not info.poster:
                        info.poster = poster
                    results.append(info)
            except Exception as e:
                log.warning("filmpalast: failed to fetch/parse details for %s: %s", movie_url, e)
                # Fallback to rough search page info
                results.append(
                    SearchResult(
                        title=title,
                        url=movie_url,
                        source=self.name,
                        poster=poster,
                        language="en" if "-english" in movie_url.lower() or "english" in title.lower() else "de"
                    )
                )

        return results[:30]

    async def _parse_detail_page(self, movie_url: str, html: str) -> SearchResult | None:
        title = extract_title(html)
        if not title:
            return None

        # Clean title: e.g. "Film Finch Stream kostenlos online..." -> "Finch"
        title = title.strip()
        if title.lower().startswith("film "):
            title = title[5:].strip()
        title = re.sub(r'\s+stream\s+kostenlos\s+online.*$', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s+stream\s+kostenlos.*$', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s+stream\s*$', '', title, flags=re.IGNORECASE)
        title = re.sub(r'\s+kostenlos\s+online.*$', '', title, flags=re.IGNORECASE)
        title = title.strip()

        # Extract year: Ver&ouml;ffentlicht: 2022
        year = None
        m_year = re.search(r'Ver(?:&ouml;|ö)ffentlicht:\s*(\d{4})', html, re.IGNORECASE)
        if m_year:
            year = int(m_year.group(1))

        # Extract language
        language = "de"
        if "-english" in movie_url.lower() or "english" in title.lower():
            language = "en"

        # Extract poster
        m_poster = re.search(r'<div\s+id="poster"[^>]*>.*?src="([^"]+)"', html, re.DOTALL | re.IGNORECASE)
        if not m_poster:
            m_poster = re.search(r'src="([^"]+)"[^>]*class="cover-opacity"', html, re.IGNORECASE)
        poster = m_poster.group(1).strip() if m_poster else None
        if poster:
            if poster.startswith("//"):
                poster = "https:" + poster
            elif poster.startswith("/"):
                poster = BASE + poster

        return SearchResult(
            title=title,
            url=movie_url,
            year=year,
            source=self.name,
            poster=poster,
            language=language
        )

    async def get_stream(self, movie_url: str) -> StreamCandidate:
        html = await get(movie_url)

        # Parse hosters
        blocks = html.split('<li class="hostBg rb">')
        hosters = []

        for b in blocks[1:]:
            name_m = re.search(r'<p\s+class="hostName">([^<]+)</p>', b, re.IGNORECASE)
            href_m = re.search(r'<a\s+[^>]*href="([^"]+)"[^>]*>\s*<i[^>]*>\s*</i>\s*Play\s*</a>', b, re.DOTALL | re.IGNORECASE)
            if not href_m:
                # fallback: search for known hoster links in the block
                href_m = re.search(r'href="([^"]*(?:voe|vidmoly|vidoza|dood|filemoon|streamtape|vidara|vidsonic)[^"]*)"', b, re.IGNORECASE)

            if name_m and href_m:
                hosters.append({
                    "provider": name_m.group(1).replace(" HD", "").strip(),
                    "url": href_m.group(1).strip()
                })

        if not hosters:
            raise RuntimeError(f"filmpalast: no stream links found on {movie_url}")

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
                log.info("filmpalast: trying hoster=%s -> url=%s", h["provider"], h["url"])
                direct = await resolve_direct_url(h["provider"], h["url"])
                if direct:
                    return StreamCandidate(
                        url=direct,
                        hoster=h["provider"],
                        language="en" if "-english" in movie_url.lower() else "de",
                        headers=headers_for(h["provider"])
                    )
            except Exception as e:
                last_err = e
                log.warning("filmpalast: hoster %s failed: %s", h["provider"], e)
                continue

        raise RuntimeError(f"filmpalast: all hosters exhausted ({last_err})")


def _priority_rank(provider: str, priority) -> int:
    p = (provider or "").lower()
    for i, want in enumerate(priority):
        if want.lower() in p:
            return i
    return 999
