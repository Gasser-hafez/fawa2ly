"""
FakeSearch: canned results after a configurable delay, so the search path, the overlay and the tests
run with no key and no network.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from .base import SearchProvider, SearchReply, SearchResult

DEFAULT_RESULTS = [
    SearchResult(title="Example company reports its latest results", url="https://www.example.com/news/results",
                 content="The company said revenue grew 12 percent last quarter and it opened two new offices."),
    SearchResult(title="Example company launches a new product", url="https://example.org/launch",
                 content="The new product went on sale this month for small teams, starting at 19 dollars a user."),
]


class FakeSearch(SearchProvider):
    name = "fake-search"

    def __init__(self, results: Optional[list[SearchResult]] = None, error: Optional[str] = None,
                 delay: float = 0.0):
        self.results = DEFAULT_RESULTS if results is None else results
        self.error = error
        self.delay = delay
        self.queries: list[str] = []          # what was searched, for assertions

    async def search(self, query: str) -> SearchReply:
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            return SearchReply(query, error=self.error, ms=round(self.delay * 1000))
        return SearchReply(query, results=list(self.results), ms=round(self.delay * 1000))
