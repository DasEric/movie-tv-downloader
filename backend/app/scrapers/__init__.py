"""Scraper registry."""
from __future__ import annotations

from app.models import ItemSource
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.base import BaseScraper
from app.scrapers.megakino import MegakinoScraper
from app.scrapers.sto import StoScraper
from app.scrapers.filmpalast import FilmpalastScraper
from app.scrapers.burningseries import BurningSeriesScraper
from app.scrapers.kinox import KinoxScraper

REGISTRY: dict[ItemSource, type[BaseScraper]] = {
    ItemSource.STO: StoScraper,
    ItemSource.ANIWORLD: AniworldScraper,
    ItemSource.MEGAKINO: MegakinoScraper,
    ItemSource.FILMPALAST: FilmpalastScraper,
    ItemSource.BURNING_SERIES: BurningSeriesScraper,
    ItemSource.KINOX: KinoxScraper,
}


def get_scraper(source: ItemSource) -> BaseScraper:
    return REGISTRY[source]()
