"""Search across the three sites + TMDB metadata lookup."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models import ItemSource
from app.scrapers import get_scraper
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.aniworld import slug_from_url as aw_slug
from app.scrapers.sto import StoScraper
from app.scrapers.sto import slug_from_url as sto_slug
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


@router.get("/seasons")
async def list_seasons_route(source: ItemSource, slug: str):
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper)):
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
    if not isinstance(scraper, (StoScraper, AniworldScraper)):
        raise HTTPException(400, "source has no show details")
    try:
        details = await scraper.fetch_show_details(slug)
    except Exception as e:
        raise HTTPException(502, f"failed to fetch show details: {e}")
    return details


@router.get("/episodes")
async def list_episodes_route(source: ItemSource, slug: str, season: int):
    scraper = get_scraper(source)
    if not isinstance(scraper, (StoScraper, AniworldScraper)):
        raise HTTPException(400, "source has no episodes")
    try:
        eps = await scraper.list_episodes(slug, season)
    except Exception as e:
        raise HTTPException(502, f"failed to list episodes: {e}")
    return {"episodes": eps}


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
    return None
