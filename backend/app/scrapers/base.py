"""Scraper interface + shared data classes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    year: int | None = None
    source: str = ""
    poster: str | None = None
    language: str | None = None


@dataclass
class StreamCandidate:
    url: str           # direct, playable URL (m3u8, mp4, ...)
    hoster: str
    language: str
    headers: dict[str, str] | None = None


@dataclass
class EpisodeRef:
    show: str
    slug: str
    season: int
    episode: int
    language: str = "de"


class BaseScraper(ABC):
    name: str = ""

    @abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        ...
