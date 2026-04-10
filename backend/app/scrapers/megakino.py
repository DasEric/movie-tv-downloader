"""
Megakino scraper.

Ported from https://github.com/Yezun-hikari/Megakino-Downloader (MIT).

Key tricks from the reference:

  1. **Dynamic domain**: Megakino rotates its top-level domain frequently.
     A community-maintained GitHub file tracks the current one. We fetch it
     as the primary source and only fall back to our own seed list + the
     persisted last-known-good domain if GitHub is unreachable.

  2. **Session token warm-up**: Every scraping session starts with
     `GET /index.php?yg=token` which sets cookies needed by the search
     endpoint. Without it the search just redirects you to the homepage.

  3. **Search URL**: `/index.php?do=search&subaction=search&search_start=0&
     full_search=0&result_from=1&story={keyword}`

  4. **Result parsing**: `<a class="poster">` with `<h3 class="poster__title">`
     and a nested `<img>` for the cover.

  5. **Stream extraction**:
     - If the movie page has a `<iframe data-src="…voe.sx…">` → VOE extractor.
     - Otherwise, the native Megakino player: scrape `"uid"`, `"md5"`, `"id"`
       from the iframe HTML, assemble the gxplayer m3u8 URL.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.scrapers.base import BaseScraper, SearchResult, StreamCandidate
from app.services.hosters import headers_for, resolve_direct_url
from app.services.http import get, get_client, get_with_final_url

log = logging.getLogger(__name__)

STATE_FILE = settings.config_path / "megakino.json"

# Community-maintained domain tracker (from the reference repo).
# Updated whenever megakino rotates their TLD.
DOMAIN_TRACKER_URL = (
    "https://raw.githubusercontent.com/Yezun-hikari/"
    "new-domain-check/refs/heads/main/monitors/megakino/domain.txt"
)

# Last-resort seeds if the tracker AND the persisted cache are both unavailable
SEED_DOMAINS = [
    "https://megakino2.biz",
    "https://megakino.to",
    "https://megakino.cc",
    "https://megakino3.to",
]


# ---- extraction regexes (ported 1:1 from the reference) ----

_UID_RE = re.compile(r'"uid":"(.*?)"')
_MD5_RE = re.compile(r'"md5":"(.*?)"')
_ID_RE = re.compile(r'"id":"(.*?)"')

GXPLAYER_TEMPLATE = (
    "https://watch.gxplayer.xyz/m3u8/{uid}/{md5}/master.txt?s=1&id={vid}&cache=1"
)


class MegakinoScraper(BaseScraper):
    name = "megakino"

    def __init__(self) -> None:
        self.base_url: str = self._load_persisted() or SEED_DOMAINS[0]
        self._token_fetched: bool = False

    # ---------- persistence ----------

    def _load_persisted(self) -> str | None:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())["base_url"]
            except Exception:
                return None
        return None

    def _persist(self, url: str) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({"base_url": url}))
            log.info("megakino: persisted new base_url=%s", url)
        except Exception as e:
            log.warning("megakino: persist failed: %s", e)

    # ---------- dynamic domain resolution ----------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def resolve_base(self) -> str:
        """
        Resolve Megakino's current base URL. Strategy:

          1. Ask the community domain tracker on GitHub (very reliable, updated
             by humans when Megakino rotates).
          2. Try the persisted last-known-good domain.
          3. Follow redirects from our seed domains.

        Whatever we successfully reach is persisted for next time.
        """
        # --- 1) community tracker ---
        try:
            c = await get_client()
            r = await c.get(DOMAIN_TRACKER_URL, timeout=10)
            r.raise_for_status()
            domain = (r.text or "").strip()
            if domain:
                # Normalise to a full URL
                if not domain.startswith("http"):
                    domain = f"https://{domain}"
                # Strip trailing slash if present
                domain = domain.rstrip("/")
                if domain != self.base_url:
                    log.info("megakino: tracker says domain is %s", domain)
                    self.base_url = domain
                    self._persist(domain)
                    # A fresh domain needs a fresh token cookie
                    self._token_fetched = False
                return self.base_url
        except Exception as e:
            log.warning("megakino domain tracker unreachable: %s", e)

        # --- 2) + 3) seed / persisted redirect follow ---
        candidates = [self.base_url, *SEED_DOMAINS]
        seen: set[str] = set()
        last_err: Exception | None = None
        for seed in candidates:
            if seed in seen:
                continue
            seen.add(seed)
            try:
                _body, final_url = await get_with_final_url(seed)
                parsed = urlparse(final_url)
                resolved = f"{parsed.scheme}://{parsed.netloc}"
                if resolved and resolved != self.base_url:
                    self.base_url = resolved
                    self._persist(resolved)
                    self._token_fetched = False
                return resolved or self.base_url
            except Exception as e:
                last_err = e
                log.warning("megakino seed %s failed: %s", seed, e)
        raise RuntimeError(f"megakino: no reachable domain ({last_err})")

    # ---------- session token warm-up ----------

    async def _ensure_token(self) -> None:
        """
        Megakino sets session cookies via `GET /index.php?yg=token`.
        Without these cookies the search endpoint just 302s to the homepage.
        We only need to do this once per process (curl_cffi keeps cookies
        on the shared AsyncSession).
        """
        if self._token_fetched:
            return
        try:
            await get(
                f"{self.base_url}/index.php",
                params={"yg": "token"},
                check_captcha=False,
            )
            self._token_fetched = True
        except Exception as e:
            log.warning("megakino token warm-up failed: %s", e)

    # ---------- search ----------

    async def search(self, query: str) -> list[SearchResult]:
        base = await self.resolve_base()
        await self._ensure_token()

        html = await get(
            f"{base}/index.php",
            params={
                "do": "search",
                "subaction": "search",
                "search_start": "0",
                "full_search": "0",
                "result_from": "1",
                "story": query,
            },
            check_captcha=False,
        )
        return self._parse_search(html, base)

    @staticmethod
    def _parse_search(html: str, base: str) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        results: list[SearchResult] = []

        # Primary: <a class="poster">...<h3 class="poster__title">...</h3>...</a>
        for a in soup.select("a.poster"):
            href = a.get("href") or ""
            if not href:
                continue
            if not href.startswith("http"):
                href = base.rstrip("/") + "/" + href.lstrip("/")

            title_el = a.select_one("h3.poster__title") or a.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if not title:
                continue

            # Poster/thumbnail
            poster: Optional[str] = None
            img = a.select_one("img[src], img[data-src]")
            if img:
                src = img.get("data-src") or img.get("src") or ""
                if src.startswith("//"):
                    poster = "https:" + src
                elif src.startswith("/"):
                    poster = base.rstrip("/") + src
                elif src.startswith("http"):
                    poster = src

            results.append(
                SearchResult(
                    title=title,
                    url=href,
                    year=_extract_year(title),
                    source="megakino",
                    poster=poster,
                )
            )

        # Fallback: the older list layout
        if not results:
            for article in soup.select("article, .short, .shortstory, .post"):
                link = article.select_one("a[href]")
                if not link:
                    continue
                href = link.get("href") or ""
                if not href.startswith("http"):
                    href = base.rstrip("/") + "/" + href.lstrip("/")
                title_el = article.select_one("h2, h3, .title, .stitle")
                title = (
                    title_el.get_text(strip=True)
                    if title_el
                    else link.get_text(strip=True)
                )
                img = article.select_one("img[src], img[data-src]")
                poster = None
                if img:
                    src = img.get("data-src") or img.get("src") or ""
                    if src.startswith("//"):
                        poster = "https:" + src
                    elif src.startswith("/"):
                        poster = base.rstrip("/") + src
                    elif src.startswith("http"):
                        poster = src
                if title and href:
                    results.append(
                        SearchResult(
                            title=title,
                            url=href,
                            year=_extract_year(title),
                            source="megakino",
                            poster=poster,
                        )
                    )

        return results[:30]

    # ---------- stream resolution ----------

    async def get_stream(self, movie_url: str) -> StreamCandidate:
        # Make sure the domain/token are current before we touch the page
        await self.resolve_base()
        await self._ensure_token()

        html = await get(movie_url, check_captcha=False)

        # Strategy A: a VOE (or other known hoster) iframe
        iframe_src = _find_hoster_iframe(html)
        if iframe_src:
            iframe_src = _absolutize(iframe_src, self.base_url)
            hoster_name = _hoster_from_url(iframe_src)
            log.info("megakino: found hoster iframe %s -> %s", hoster_name, iframe_src)
            direct = await resolve_direct_url(hoster_name, iframe_src)
            if direct:
                return StreamCandidate(
                    url=direct,
                    hoster=hoster_name,
                    language="de",
                    headers=headers_for(hoster_name),
                )

        # Strategy B: native Megakino player via gxplayer.xyz
        iframe_any = _find_any_iframe(html)
        if iframe_any:
            player_url = _absolutize(iframe_any, self.base_url)
            log.info("megakino: trying native gxplayer via %s", player_url)
            direct = await _extract_gxplayer(player_url, self.base_url)
            if direct:
                return StreamCandidate(
                    url=direct,
                    hoster="megakino",
                    language="de",
                    headers={"Referer": self.base_url + "/"},
                )

        # Strategy C: direct m3u8 sitting in the page
        m = re.search(r"https?://\S+?\.m3u8[^\"'\s]*", html)
        if m:
            return StreamCandidate(
                url=m.group(0),
                hoster="direct",
                language="de",
                headers={"Referer": self.base_url + "/"},
            )

        raise RuntimeError(f"megakino: no stream found on {movie_url}")


# ---------- helpers ----------

def _extract_year(title: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", title)
    return int(m.group(0)) if m else None


def _absolutize(url: str, base: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return base.rstrip("/") + url
    return url


def _hoster_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for needle in ("voe", "vidmoly", "vidoza", "dood", "filemoon", "streamtape"):
        if needle in host:
            return needle
    return host or "direct"


def _find_hoster_iframe(html: str) -> str | None:
    """
    Find an iframe pointing at a known video hoster. The reference uses
    `data-src` (lazy-loaded) which the initial `<iframe src="">` sometimes
    doesn't have — we check both.
    """
    soup = BeautifulSoup(html, "lxml")
    known = ("voe", "vidmoly", "vidoza", "dood", "filemoon", "streamtape")
    for iframe in soup.find_all("iframe"):
        for attr in ("data-src", "src"):
            val = iframe.get(attr) or ""
            if not val:
                continue
            low = val.lower()
            if any(n in low for n in known):
                return val
    return None


def _find_any_iframe(html: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    for iframe in soup.find_all("iframe"):
        for attr in ("data-src", "src"):
            val = iframe.get(attr) or ""
            if val:
                return val
    return None


async def _extract_gxplayer(player_url: str, referer: str) -> str | None:
    """
    Megakino's own player — the iframe at `player_url` embeds a script that
    contains `"uid":"…"`, `"md5":"…"`, `"id":"…"`. We assemble the final
    gxplayer m3u8 URL from those.
    """
    try:
        c = await get_client()
        r = await c.get(
            player_url,
            headers={"Referer": referer, "User-Agent": settings.user_agent},
            timeout=20,
        )
        r.raise_for_status()
        text = r.text
    except Exception as e:
        log.warning("megakino: player page fetch failed: %s", e)
        return None

    uid = _UID_RE.search(text)
    md5 = _MD5_RE.search(text)
    vid = _ID_RE.search(text)
    if not (uid and md5 and vid):
        log.info("megakino: gxplayer markers not found on %s", player_url)
        return None

    return GXPLAYER_TEMPLATE.format(
        uid=uid.group(1),
        md5=md5.group(1),
        vid=vid.group(1),
    )
