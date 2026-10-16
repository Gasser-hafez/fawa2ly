"""
Which model answers fastest, measured from this machine (2026-09-11, maintainer's request).

The delay a user feels is mostly the provider, and it changes by the hour, so the only
honest ranking is one taken now, from here. Every provider with a key gets the same short question
through the same prompt as a real answer, twice: the first request can include connection setup,
so the median of the two is reported. Time to first token is what ranks them, because it is when
the first line can start appearing; a provider that errors, times out or returns no usable line is
not a candidate however fast it failed.

The requests count toward usage like any other (fawa2ly/usage.py).
"""

from __future__ import annotations

import asyncio
import statistics
from typing import Callable, Optional

from .limits import DEFAULT_MAX_TOKENS
from .llm import OpenAICompatibleLLM, collect
from .llm.base import SuggestionRequest
from .llm.prompts import system_for
from .usage import MeteredProvider

QUESTION = "Tell me about a project you are proud of."
RUNS = 2
TIMEOUT_S = 45.0


def _real_provider(vendor: str, model: Optional[str]):
    return MeteredProvider(OpenAICompatibleLLM.from_env(vendor, model))


async def run(providers: list[dict], on_result: Optional[Callable[[dict], None]] = None,
              make: Callable = _real_provider, runs: int = RUNS, timeout_s: float = TIMEOUT_S,
              question: str = QUESTION) -> list[dict]:
    """Test each {"vendor", "model"} in turn (never at once: parallel requests would slow each
    other on a home connection). Returns one row per provider, also handed to `on_result` as it
    finishes so a window can fill its table while the rest are still running."""
    results = []
    for p in providers:
        vendor, model = p["vendor"], p.get("model")
        row = {"vendor": vendor, "model": model, "ttft_ms": None, "total_ms": None, "lines": 0,
               "runs": 0, "error": None}
        try:
            provider = make(vendor, model)
        except Exception as e:                       # no key, unknown vendor
            row["error"] = str(e)
        else:
            ttfts, totals = [], []
            try:
                for _ in range(runs):
                    req = SuggestionRequest(question, system_for("full"), max_tokens=DEFAULT_MAX_TOKENS)
                    try:
                        s = await asyncio.wait_for(collect(provider, req), timeout=timeout_s)
                    except asyncio.TimeoutError:
                        row["error"] = f"no reply within {timeout_s:.0f} s"
                        break
                    if s.error:
                        row["error"] = s.error[:200]
                        break
                    row["runs"] += 1
                    row["model"] = s.model or model
                    row["lines"] = max(row["lines"], len(s.bullets))
                    if s.ttft_ms is not None:
                        ttfts.append(s.ttft_ms)
                    totals.append(s.total_ms)
            finally:
                await provider.close()
            if ttfts:
                row["ttft_ms"] = round(statistics.median(ttfts))
            if totals:
                row["total_ms"] = round(statistics.median(totals))
        results.append(row)
        if on_result is not None:
            on_result(row)
    return results


def fastest(results: list[dict]) -> Optional[str]:
    """The vendor with the lowest time to first token among those that answered with lines."""
    ok = [r for r in results if r["ttft_ms"] is not None and r["lines"] > 0 and not r["error"]]
    return min(ok, key=lambda r: r["ttft_ms"])["vendor"] if ok else None
