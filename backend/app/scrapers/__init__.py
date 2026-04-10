"""Scraper registry."""
from __future__ import annotations

from app.models import ItemSource
from app.scrapers.aniworld import AniworldScraper
from app.scrapers.base import BaseScraper
from app.scrapers.megakino import MegakinoScraper
from app.scrapers.sto import StoScraper

REGISTRY: dict[ItemSource, type[BaseScraper]] = {
    ItemSource.STO: StoScraper,
    ItemSource.ANIWORLD: AniworldScraper,
    ItemSource.MEGAKINO: MegakinoScraper,
}


def get_scraper(source: ItemSource) -> BaseScraper:
    return REGISTRY[source]()
