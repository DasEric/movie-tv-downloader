"""Search across the three sites + TMDB metadata lookup."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.models import ItemSource
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.aniworld import slug_from_url as aw_slug
from app.scrapers.sto import StoScraper
from app.scrapers.sto import slug_from_url as sto_slug
from app.scrapers.burningseries import BurningSeriesScraper
from app.scrapers.burningseries import slug_from_url as bs_slug
from app.scrapers.kinox import KinoxScraper
from app.scrapers.kinox import slug_from_url as kx_slug
from app.services import tmdb

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search(
    source: ItemSource = Query(...),
    q: str = Query(..., min_length=1),
):
    scraper = get_scraper(source)
    try:
        results = await scraper.search(q)
    except Exception as e:
        raise HTTPException(502, f"search failed: {e}")
    return [
        {
            "title": r.title,
            "url": r.url,
            "year": r.year,
            "source": r.source,
            "poster": r.poster,
            "slug": _slug_for(source, r.url),
        }
        for r in results
    ]


@router.get("/catalog")
async def get_catalog(
    source: ItemSource = Query(...),
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    language: str | None = Query(None),
    hoster: str | None = Query(None),
    sort: str | None = Query(None),
):
    scraper = get_scraper(source)
    try:
        results = await scraper.discover(page=page, category=category)
    except Exception as e:
        raise HTTPException(502, f"catalog discovery failed: {e}")

    filtered = []
    for r in results:
        # Check language
        if language:
            r_lang = (r.language or "").lower()
            q_lang = language.lower()
            if q_lang in ("de", "de-dub", "de-sub"):
                if r_lang and not any(x in r_lang for x in ("de", "german", "ger", "dub")):
                    continue
            elif q_lang in ("en", "en-dub", "en-sub"):
                if r_lang and not any(x in r_lang for x in ("en", "english", "eng")):
                    continue
            elif q_lang != r_lang:
                continue

        # Check hoster
        if hoster and r.hosters is not None:
            if not any(hoster.lower() in h.lower() for h in r.hosters):
                continue

        filtered.append(r)

    # Sort
    if sort == "newest":
        filtered.sort(key=lambda r: r.year or 0, reverse=True)
    elif sort == "title":
        filtered.sort(key=lambda r: (r.title or "").lower())

    return [
        {
            "title": r.title,
            "url": r.url,
            "year": r.year,
            "source": r.source,
            "poster": r.poster,
            "slug": _slug_for(source, r.url),
            "language": r.language,
            "hosters": r.hosters,
        }
        for r in filtered
    ]


@router.get("/seasons")
async def list_seasons_route(source: ItemSource, slug: str):
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper, BurningSeriesScraper, KinoxScraper)):
        raise HTTPException(400, "source has no seasons")
    try:
        seasons = await scraper.list_seasons(slug)
    except Exception as e:
        raise HTTPException(502, f"failed to list seasons: {e}")
    return {"seasons": seasons}


@router.get("/details")
async def show_details(source: ItemSource, slug: str):
    """
    Single call that returns poster + title + season list for the UI.
    Replaces the separate seasons/poster dance on result-pick.
    """
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper, BurningSeriesScraper, KinoxScraper)):
        raise HTTPException(400, "source has no show details")
    try:
        details = await scraper.fetch_show_details(slug)
    except Exception as e:
        raise HTTPException(502, f"failed to fetch show details: {e}")
    return details


@router.get("/episodes")
async def list_episodes_route(source: ItemSource, slug: str, season: int):
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper, BurningSeriesScraper, KinoxScraper)):
        raise HTTPException(400, "source has no episodes")
    try:
        eps = await scraper.list_episodes(slug, season)
    except Exception as e:
        raise HTTPException(502, f"failed to list episodes: {e}")
    return {"episodes": eps}


@router.get("/episodes-with-lang")
async def list_episodes_with_language(
    source: ItemSource = Query(...),
    slug: str = Query(...),
    season: int = Query(...),
    language: str = Query("de"),
):
    """
    Return every episode in the season together with language availability.

    For aniworld: walks the fallback chain (e.g. GerDub -> GerSub -> EngSub)
    and returns which language will actually be used via `actual_language`.

    For s.to: strict match, no fallback — `actual_language` equals `language`
    when available, or null when not.
    """
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper, BurningSeriesScraper, KinoxScraper)):
        raise HTTPException(400, "source has no episodes")
    try:
        eps = await scraper.list_episodes(slug, season)
    except Exception as e:
        raise HTTPException(502, f"failed to list episodes: {e}")

    # Check all episodes concurrently (bounded to 5 parallel requests
    # so we don't hammer the source site).
    sem = asyncio.Semaphore(5)

    async def check_ep(ep: int) -> dict:
        async with sem:
            try:
                if isinstance(scraper, AniworldScraper):
                    resolved = await scraper.resolve_episode_language(
                        slug, season, ep, language
                    )
                    return {
                        "episode": ep,
                        "has_language": resolved is not None,
                        "actual_language": resolved,
                    }
                else:
                    has_lang = await scraper.episode_has_language(
                        slug, season, ep, language
                    )
                    return {
                        "episode": ep,
                        "has_language": has_lang,
                        "actual_language": language if has_lang else None,
                    }
            except Exception:
                return {
                    "episode": ep,
                    "has_language": False,
                    "actual_language": None,
                }

    result = await asyncio.gather(*[check_ep(ep) for ep in eps])
    # Preserve episode order
    return {"episodes": sorted(result, key=lambda r: r["episode"])}


@router.get("/tmdb/movie")
async def tmdb_search_movie(q: str = Query(..., min_length=1)):
    return await tmdb.search_movie(q)


@router.get("/tmdb/tv")
async def tmdb_search_tv(q: str = Query(..., min_length=1)):
    return await tmdb.search_tv(q)


def _slug_for(source: ItemSource, url: str) -> str | None:
    if source == ItemSource.STO:
        return sto_slug(url)
    if source == ItemSource.ANIWORLD:
        return aw_slug(url)
    if source == ItemSource.BURNING_SERIES:
        return bs_slug(url)
    if source == ItemSource.KINOX:
        return kx_slug(url)
    return None
