from .base import SearchProvider, SearchReply, SearchResult
from .fake import FakeSearch
from .tavily import KEY_ENV, TavilySearch

__all__ = ["SearchProvider", "SearchReply", "SearchResult", "FakeSearch", "TavilySearch", "KEY_ENV",
           "make_searcher"]


def make_searcher() -> SearchProvider:
    """Tavily when there is a key (faster), else the Codex signed in on this machine. RuntimeError
    with the reason when there is neither."""
    try:
        return TavilySearch.from_env()
    except RuntimeError as e:
        from ..llm.codex import codex_ready
        if not codex_ready():
            raise RuntimeError(f"{e}, or sign in to Codex on this machine") from None
        from .codex import CodexSearch
        return CodexSearch()
