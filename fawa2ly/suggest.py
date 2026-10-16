"""
Ask a provider for lines to say for one question, and time it.

    python -m fawa2ly.suggest "Why did you leave your last job?"
    python -m fawa2ly.suggest "..." --preset swe --cv docs/sample_cv.txt --show-prompt
    python -m fawa2ly.suggest "..." --vendor gemini --repeat 5 --verbose
    python -m fawa2ly.suggest --list-models --vendor gemini
    python -m fawa2ly.suggest "..." --fake                       # no network, no key

Prints the bullets that pass the guardrail, the ones it dropped and why, and per
run the time to first token and the total. Keys come from .env (GEMINI_API_KEY,
GROQ_API_KEY, ...). Model comparison lives in `python -m fawa2ly.bench`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .limits import DEFAULT_MAX_TOKENS
from .context import ContextBuilder, Preset, available, filter_bullets, load_cv, summarise
from .console import safe_console
from .llm import FakeLLM, OpenAICompatibleLLM, collect, mute_asyncgen_close_noise

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass


async def run(a) -> int:
    safe_console()
    mute_asyncgen_close_noise()
    if a.fake:
        provider = FakeLLM(first_token_delay=0.05, token_delay=0.005)
    else:
        provider = OpenAICompatibleLLM.from_env(a.vendor, a.model)
        if a.reasoning is not None:   # e.g. "none" on Gemini 2.5, "minimal" on Gemini 3
            provider.extra["reasoning_effort"] = a.reasoning
    try:
        if a.list_models:
            for m in await provider.list_models():
                print(m)
            return 0
        builder = ContextBuilder(preset=Preset.find(a.preset) if a.preset else None,
                                 cv_text=load_cv(a.cv) if a.cv else "")
        req = builder.build(a.question, max_tokens=a.max_tokens)
        if a.show_prompt:
            print("--- system prompt ---\n" + req.system + "\n---------------------")
        ttfts = []
        for i in range(a.repeat):
            if i and a.interval:
                await asyncio.sleep(a.interval)   # free tiers cap requests per minute
            s = await collect(provider, req)
            if s.error:
                print(f"[{i + 1}] ERROR {s.error[:300]}")
                continue
            if s.ttft_ms is not None:
                ttfts.append(s.ttft_ms)
            kept, dropped = filter_bullets(s.bullets)
            note = f", dropped {summarise(dropped)}" if dropped else ""
            if s.truncated:
                note += "  TRUNCATED (raise --max-tokens; thinking tokens count against it)"
            print(f"[{i + 1}] {provider.name}/{s.model}: first token {s.ttft_ms} ms, total {s.total_ms} ms, "
                  f"usage {s.usage}, {len(kept)} bullet(s){note}")
            if not s.bullets:
                print(f"     (empty answer; raw text: {s.text[:120]!r})")
            if i == 0 or a.verbose:
                for b in kept:
                    print(f"     - {b}")
                for d in dropped:
                    print(f"     x [{d.reason}] {d.text[:90]}")
            if a.log:
                with open(a.log, "a", encoding="utf-8") as f:
                    f.write(json.dumps(s.to_json(), ensure_ascii=False) + "\n")
        if len(ttfts) > 1:
            ttfts.sort()
            print(f"TTFT over {len(ttfts)} runs: min {ttfts[0]} p50 {ttfts[len(ttfts) // 2]} max {ttfts[-1]} ms")
        return 0 if ttfts else 1
    finally:
        await provider.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?", default="Why do you think we should hire you for this position?")
    ap.add_argument("--vendor", default="gemini", help="gemini | groq | cerebras | ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--interval", type=float, default=0.0, help="seconds to wait between repeats (rate limits)")
    ap.add_argument("--reasoning", default=None, help="override reasoning_effort (vendor-specific)")
    ap.add_argument("--preset", default=None, help=f"role preset: {', '.join(available()) or 'none found'}")
    ap.add_argument("--cv", default=None, help="background document (.txt/.md, or .pdf with pypdf)")
    ap.add_argument("--show-prompt", action="store_true", help="print the assembled system prompt")
    ap.add_argument("--fake", action="store_true", help="use FakeLLM (no network)")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="print bullets on every run")
    ap.add_argument("--log", default=None, help="append each Suggestion as JSONL")
    a = ap.parse_args(argv)
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
