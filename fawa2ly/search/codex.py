"""
Web search through the Codex signed in on this machine (2026-11-05): Codex's own live web search,
so search works with no Tavily key. Codex does not hand back its search results, so it is asked to
write them, constrained to a JSON schema, and they go through the same path as Tavily's.

Measured 2026-11-05: 11-16 s a search against Tavily's ~1 s, so a Tavily key, when there is one, is
still used first (make_searcher). Letting Codex open pages doubled it (27 s); its "cached" and
"indexed" search modes were no faster than "live".
"""

from __future__ import annotations

import json
import time
from typing import Optional

from ..limits import SEARCH_RESULTS
from ..llm.codex import CodexLLM
from .base import SearchProvider, SearchReply, SearchResult
from .tavily import MAX_QUERY_CHARS

SYSTEM = ("You search the web for an assistant that helps someone during a live call, so speed matters "
          "most. Run one web search for the query and answer from its results right away: do not open "
          "pages and do not search again. Report up to {n} results, each the page's title, its address "
          "and one or two sentences of facts the result shows. Report only what the results say; never "
          "fill in from memory.")
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["results"],
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["title", "url", "content"],
        "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "content": {"type": "string"}}}}},
}


class CodexSearch(SearchProvider):
    name = "codex"

    def __init__(self, llm: Optional[CodexLLM] = None, max_results: int = SEARCH_RESULTS):
        self.llm = llm or CodexLLM()
        self.max_results = max_results

    async def search(self, query: str) -> SearchReply:
        query = " ".join((query or "").split())[:MAX_QUERY_CHARS]
        if not query:
            return SearchReply(query, error="nothing to search for")
        t0 = time.time()
        parts, error = [], None
        async for ev in self.llm.ask(SYSTEM.format(n=self.max_results), f"Search the web for: {query}",
                                     web_search=True, schema=SCHEMA):
            if ev.kind in ("first_token", "token"):
                parts.append(ev.text)
            elif ev.kind == "error":
                error = ev.text
        ms = round((time.time() - t0) * 1000)
        if error:
            return SearchReply(query, error=f"Codex could not search ({error})", ms=ms)
        try:
            rows = json.loads("".join(parts)).get("results") or []
        except (ValueError, AttributeError):
            return SearchReply(query, error="Codex sent results that are not JSON", ms=ms)
        results = [SearchResult(title=str(r.get("title") or "").strip(), url=str(r.get("url") or ""),
                                content=" ".join(str(r.get("content") or "").split()))
                   for r in rows if isinstance(r, dict) and str(r.get("content") or "").strip()]
        return SearchReply(query, results=results[: self.max_results], ms=ms)

    async def close(self) -> None:
        await self.llm.close()
