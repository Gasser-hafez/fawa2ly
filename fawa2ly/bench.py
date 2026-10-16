"""
Benchmark suggestion models: time to first token, prompt overhead, usable output.

    python -m fawa2ly.bench --vendor gemini --models gemini-3.5-flash-lite,gemini-3.8-flash
    python -m fawa2ly.bench --vendor gemini --models-file models.txt --runs 3
    python -m fawa2ly.bench --vendor gemini --models gemini-3.5-flash-lite --runs 5 --interval 13

TTFT is the number that matters: the project wants question end -> first line
under 1.2 s, and STT already spends ~700 ms of it (M1), so the model has ~500 ms.

Four columns beyond latency, each of which has bitten us:
  in_tok    input tokens. A gateway route that fronts an IDE assistant prepends its own system
            prompt, so our ~110-token request arrives as ~4300. That costs latency and quota.
  usable    bullets within the word limit. A refusal renders as 1-2 long
            sentences, so it scores 0 usable and is visible without reading the text.
  refused   the route returned something other than an answer: a decline ("I can't help with
            this"), or any reply with no usable bullet at all, which also catches gateway notices
            such as "Gemini 3.5 Flash is no longer available". Read the text before concluding
            which of the two it was.
  cut       hit max_tokens (finish_reason "length"), so the last bullet is cut mid-phrase.
            Thinking models spend the budget on reasoning they never show, so this appears long
            before the visible output looks large.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional

from .limits import DEFAULT_MAX_TOKENS, MAX_BULLET_WORDS
from .console import safe_console
from .llm import FakeLLM, OpenAICompatibleLLM, collect, mute_asyncgen_close_noise
from .llm.base import Suggestion, SuggestionRequest
from .llm.prompts import SYSTEM_V1

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

# Default question set: real interviewer questions from the labeled eval sessions (docs/eval).
QUESTIONS = [
    "What is your biggest weakness?",
    "Why did you leave your last job?",
    "Chris, can you tell me about a challenge or a conflict that you've faced in the workplace?",
    "Where do you see yourself in five years specifically within our company?",
]

_REFUSAL = re.compile(
    r"\b(i can'?t|i cannot|i won'?t|i'?m not able|unable) (help|assist|support|provide|do)\b"
    r"|\bfalls outside\b|\bi'?m (kiro|an ai)\b|\bcheating\b",
    re.IGNORECASE,
)


def usable_bullets(s: Suggestion) -> list[str]:
    """Bullets short enough to show in the overlay (the M2 guardrail, previewed here)."""
    return [b for b in s.bullets if len(b.split()) <= MAX_BULLET_WORDS]


def looks_refused(s: Suggestion) -> bool:
    return bool(_REFUSAL.search(s.text)) or (bool(s.text) and not usable_bullets(s))


@dataclass
class ModelResult:
    model: str
    ttfts: list[int] = field(default_factory=list)
    totals: list[int] = field(default_factory=list)
    input_tokens: list[int] = field(default_factory=list)
    usable: list[int] = field(default_factory=list)
    refusals: int = 0
    truncated: int = 0
    errors: list[str] = field(default_factory=list)
    routed_to: set = field(default_factory=set)
    samples: list[str] = field(default_factory=list)

    def add(self, s: Suggestion) -> None:
        if s.error:
            self.errors.append(s.error)
            return
        self.routed_to.add(s.model)
        if s.ttft_ms is not None:
            self.ttfts.append(s.ttft_ms)
        self.totals.append(s.total_ms)
        if s.usage and s.usage.get("input_tokens"):
            self.input_tokens.append(s.usage["input_tokens"])
        if looks_refused(s):
            self.refusals += 1
        if s.truncated:
            self.truncated += 1
        good = usable_bullets(s)
        self.usable.append(len(good))
        if good and len(self.samples) < 2:
            self.samples.append(good[0])

    @property
    def ok(self) -> int:
        return len(self.totals)

    def stat(self, values, fn=statistics.median) -> Optional[float]:
        return fn(values) if values else None

    def row(self) -> str:
        def num(v, width=6):
            return " " * (width - 1) + "-" if v is None else f"{v:>{width}.0f}"
        p50 = self.stat(self.ttfts)
        worst = max(self.ttfts) if self.ttfts else None
        return (f"{self.model[:44]:<44} {self.ok:>3}/{self.ok + len(self.errors):<3} "
                f"{num(p50)} {num(worst)} {num(self.stat(self.input_tokens))} "
                f"{num(self.stat(self.usable), 4)} {self.refusals:>4} {self.truncated:>4}")


async def bench_model(provider, model: str, questions: list[str], runs: int,
                      interval: float, max_tokens: int) -> ModelResult:
    provider.model = model
    res = ModelResult(model=model)
    for i in range(runs):
        if i and interval:
            await asyncio.sleep(interval)
        question = questions[i % len(questions)]
        res.add(await collect(provider, SuggestionRequest(question, SYSTEM_V1, max_tokens=max_tokens)))
    return res


async def run(a) -> int:
    safe_console()
    mute_asyncgen_close_noise()
    models = [m.strip() for m in (a.models or "").split(",") if m.strip()]
    if a.models_file:
        with open(a.models_file, encoding="utf-8") as f:
            models += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not models:
        print("give --models or --models-file")
        return 2
    provider = FakeLLM() if a.fake else OpenAICompatibleLLM.from_env(a.vendor, models[0])
    results = []
    try:
        # flush: a sweep of several models takes minutes, and a redirected stdout would
        # otherwise show nothing until the end, which reads as a hang
        print(f"{'model':<44} {'ok':>7} {'p50':>6} {'worst':>6} {'in_tok':>6} {'use':>4} {'ref':>4} {'cut':>4}",
              flush=True)
        for m in models:
            res = await bench_model(provider, m, QUESTIONS, a.runs, a.interval, a.max_tokens)
            results.append(res)
            print(res.row(), flush=True)
            if res.errors:
                print(f"{'':<44} error: {res.errors[0][:100]}", flush=True)
            for sample in res.samples[:1]:
                print(f"{'':<44} e.g. {sample[:80]}", flush=True)
    finally:
        await provider.close()
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            for res in results:
                f.write(json.dumps({"model": res.model, "ok": res.ok, "ttfts": res.ttfts,
                                    "input_tokens": res.input_tokens, "usable": res.usable,
                                    "refusals": res.refusals, "truncated": res.truncated, "errors": res.errors,
                                    "routed_to": sorted(res.routed_to)}, ensure_ascii=False) + "\n")
    ranked = [r for r in results if r.ttfts and not r.refusals]
    if ranked:
        best = min(ranked, key=lambda r: statistics.median(r.ttfts))
        print(f"\nfastest without refusals: {best.model} "
              f"(p50 {statistics.median(best.ttfts):.0f} ms, {statistics.median(best.input_tokens or [0]):.0f} input tokens)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendor", default="gemini")
    ap.add_argument("--models", default=None, help="comma-separated model ids")
    ap.add_argument("--models-file", default=None, help="file with one model id per line")
    ap.add_argument("--runs", type=int, default=2, help="requests per model (questions rotate)")
    ap.add_argument("--interval", type=float, default=0.0, help="seconds between requests")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--json", default=None, help="write raw results as JSONL")
    ap.add_argument("--fake", action="store_true")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
