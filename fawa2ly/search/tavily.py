"""
Tavily web search (roadmap item 10, maintainer's choice 2026-09-12).

Verified against docs.tavily.com on 2026-09-12:
  - POST https://api.tavily.com/search, `Authorization: Bearer tvly-...`, JSON body with `query`.
  - `search_depth`: advanced (2 credits), basic, fast, ultra-fast (1 credit each). "fast" is
    described as "Prioritizes lower latency while maintaining good relevance. Returns multiple
    semantically relevant snippets per URL", which is what a live call needs.
  - `max_results` 0-20 (default 10); `include_answer` defaults to false and stays off: the model
    writing the lines reads the extracts itself.
  - Reply: `results[]` with `title`, `url`, `content`, `score`; `response_time` in seconds.
  - Errors carry {"detail": {"error": "..."}}: 400 bad parameters, 401 missing or invalid key, 429
    rate limit, 432 plan or key usage limit, 433 pay-as-you-go spending limit, 500 server error.
  - Free plan: 1,000 credits a month, no card. Development keys: 100 requests a minute.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx

from ..limits import SEARCH_RESULTS
from .base import SearchProvider, SearchReply, SearchResult

ENDPOINT = "https://api.tavily.com/search"
KEY_ENV = "TAVILY_API_KEY"
SEARCH_DEPTH = "fast"
TIMEOUT_S = 10.0
MAX_QUERY_CHARS = 300        # our own cap: a query is a few words, never a paragraph of the call

# What each documented status means for someone mid-call. The key itself never appears in a message.
STATUS_TEXT = {
    400: "Tavily refused the search request",
    401: "Tavily refused the key: check TAVILY_API_KEY under API keys",
    429: "too many searches a minute for this Tavily key",
    432: "this Tavily key has used its plan's searches",
    433: "this Tavily account has reached its spending limit",
}


def _error(response: httpx.Response) -> str:
    detail = ""
    try:
        body = response.json()
        detail = str(((body or {}).get("detail") or {}).get("error") or "")
    except (ValueError, AttributeError):
        pass
    text = STATUS_TEXT.get(response.status_code, f"Tavily answered {response.status_code}")
    return f"{text} ({detail})" if detail and response.status_code != 401 else text


class TavilySearch(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str, client: Optional[httpx.AsyncClient] = None,
                 max_results: int = SEARCH_RESULTS, depth: str = SEARCH_DEPTH, timeout_s: float = TIMEOUT_S):
        self._key = api_key
        self.max_results = max_results
        self.depth = depth
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    @classmethod
    def from_env(cls, **kw) -> "TavilySearch":
        key = os.environ.get(KEY_ENV)
        if not key:
            raise RuntimeError(f"{KEY_ENV} is not set (add it in Settings, under API keys)")
        return cls(key, **kw)

    async def search(self, query: str) -> SearchReply:
        query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
        if not query:
            return SearchReply(query, error="nothing to search for")
        t0 = time.time()
        body = {"query": query, "search_depth": self.depth, "max_results": self.max_results,
                "topic": "general", "include_answer": False}
        try:
            response = await self._client.post(ENDPOINT, json=body,
                                               headers={"Authorization": f"Bearer {self._key}"})
        except httpx.TimeoutException:
            return SearchReply(query, error="the search took too long", ms=round((time.time() - t0) * 1000))
        except httpx.HTTPError as e:
            return SearchReply(query, error=f"could not reach Tavily ({type(e).__name__})",
                               ms=round((time.time() - t0) * 1000))
        ms = round((time.time() - t0) * 1000)
        if response.status_code != 200:
            # a message from the service may quote the key back; it goes no further than here
            return SearchReply(query, error=_error(response).replace(self._key, "[key]"), ms=ms)
        try:
            data = response.json()
        except ValueError:
            return SearchReply(query, error="Tavily sent a reply that is not JSON", ms=ms)
        results = []
        for row in (data.get("results") if isinstance(data, dict) else None) or []:
            if not isinstance(row, dict):
                continue
            content = " ".join(str(row.get("content") or "").split())
            if content:
                results.append(SearchResult(title=str(row.get("title") or "").strip(),
                                            url=str(row.get("url") or ""), content=content))
        return SearchReply(query, results=results, ms=ms)

    async def close(self) -> None:
        await self._client.aclose()
