"""
SearchProvider interface (roadmap item 10, 2026-09-12).

A provider turns a query into a SearchReply: a few results, each a title, an address and a short
extract of the page. Like SuggestionProvider it never raises; a failure is a reply with `error`
set, so a search that goes wrong mid-call costs a line on the card and nothing else.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urlparse


@dataclass
class SearchResult:
    title: str
    url: str
    content: str                 # the extract the model reads

    @property
    def site(self) -> str:
        """example.com for https://www.example.com/page, which is what the card shows."""
        host = urlparse(self.url).netloc.lower()
        return host[4:] if host.startswith("www.") else host


@dataclass
class SearchReply:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    error: Optional[str] = None
    ms: Optional[int] = None     # how long the search took

    def sources(self) -> list[dict]:
        return [{"title": r.title, "url": r.url, "site": r.site} for r in self.results]

    def to_json(self) -> dict:
        return asdict(self)


class SearchProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def search(self, query: str) -> SearchReply:
        """Search the web for `query`. Must never raise."""

    async def close(self) -> None:
        return None
