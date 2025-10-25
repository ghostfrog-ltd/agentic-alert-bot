# core/contracts.py
from dataclasses import dataclass
from typing import Iterable, Optional
from datetime import datetime
from abc import ABC, abstractmethod

@dataclass
class Article:
    source_id: int
    source: str
    url: str
    type: str
    title: str
    summary: Optional[str]
    published_at: Optional[datetime]
    author: Optional[str]
    tags: list[str]
    content: str
    sentiment: str
    niche: str
    raw_html: Optional[str] = None
    hash_id: Optional[str] = None

class SiteAdapter(ABC):
    """One implementation per site/domain."""

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        ...

    @abstractmethod
    def fetch_listing_urls(self) -> Iterable[str]:
        """Return article URLs to visit (front page, section pages, RSS, etc.)."""
        ...

    @abstractmethod
    def parse_article(self, url: str) -> Article:
        """Fetch + parse one article into normalized fields."""
        ...