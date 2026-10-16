"""
OpenAICompatibleLLM: any vendor that speaks the OpenAI chat-completions protocol
(Cerebras, Groq, Gemini's compatibility endpoint, a local Ollama) through the
official `openai` package with a custom base_url. One file covers all the free
tiers we test with. Another provider is another class beside this one.

Vendor table below only lists endpoints verified against their docs; add a row
when you verify a new one.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import AsyncIterator, Optional

from .base import SuggestionEvent, SuggestionProvider, SuggestionRequest
from .prompts import user_content

# base_url, key env var and default model per the vendor's docs (cerebras verified 2026-09-08:
# inference-docs.cerebras.ai lists qwen-3.8-27b and gpt-oss-120b on the free tier; only n=1,
# no tool_stream). Override the model with <VENDOR>_MODEL in .env or --model.
VENDORS = {
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "key_env": "CEREBRAS_API_KEY", "model": "qwen-3.8-27b"},
    # ai.google.dev/gemini-api/docs/openai (verified 2026-09-08): compatibility layer is beta.
    # Gemini 3 models always think; "none" is 2.5-only and gemini-3.8-flash rejects "minimal"
    # (400 "Thinking level MINIMAL is not supported"), so "low" is the floor there. Thinking ran
    # 2-11 s before the first token and ate the max_tokens budget when left at the default.
    # stream_options.include_usage is documented as supported.
    # Measured 2026-09-08 on the free tier (5 requests/min per model): gemini-3.5-flash-lite at
    # "low" gives first token ~800 ms warm with full answers; gemini-3.8-flash 1-12 s and truncated.
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "key_env": "GEMINI_API_KEY", "model": "gemini-3.5-flash-lite",
               "extra": {"reasoning_effort": "low", "stream_options": {"include_usage": True}}},
    # console.groq.com/docs/openai + /docs/rate-limits (verified 2026-09-09): n=1 only, no logprobs.
    # Free tier, no card: 30 requests/min and 1000/day on openai/gpt-oss-20b, gpt-oss-120b,
    # qwen/qwen3.6-27b and qwen/qwen3.8-27b, but only 8K tokens/min, which is ~11 of our requests
    # per minute once a CV is in the prompt. Default is the smallest model because time to first
    # token is what this project is short of; switch with GROQ_MODEL or --model.
    "groq": {"base_url": "https://api.groq.com/openai/v1", "key_env": "GROQ_API_KEY",
             "model": "openai/gpt-oss-20b"},
    "ollama": {"base_url": "http://localhost:11434/v1", "key_env": None, "model": "llama3.2",
               "local": True},
}


def mute_asyncgen_close_noise() -> None:
    """Silence one cosmetic httpcore2 bug on the running loop.

    Closing the HTTP client after a streamed response athrows into a body generator that has
    already finished. The response is complete and nothing is lost, but at loop shutdown asyncio
    prints "an error occurred during closing of asynchronous generator" plus a RuntimeError
    traceback, in the middle of the user's output. Call this once inside the coroutine passed to
    asyncio.run; every other loop exception is still reported.
    """
    loop = asyncio.get_event_loop()
    previous = loop.get_exception_handler()

    def handler(lp, context):
        if "asynchronous generator" in context.get("message", ""):
            return
        (previous or lp.default_exception_handler)(lp, context)

    loop.set_exception_handler(handler)


class OpenAICompatibleLLM(SuggestionProvider):
    def __init__(self, base_url: str, api_key: Optional[str], model: str, name: str = "openai-compat",
                 temperature: float = 0.3, client=None, extra: Optional[dict] = None):
        self.base_url = base_url
        self.model = model
        self.name = name
        self.temperature = temperature
        self.extra = dict(extra or {})   # vendor-specific request fields, sent through extra_body
        if client is None:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(base_url=base_url, api_key=api_key or "none")
        self._client = client

    @classmethod
    def from_env(cls, vendor: str, model: Optional[str] = None, **kw) -> "OpenAICompatibleLLM":
        if vendor not in VENDORS:
            raise ValueError(f"unknown vendor {vendor!r}; known: {', '.join(VENDORS)}")
        v = VENDORS[vendor]
        key = os.environ.get(v["key_env"]) if v["key_env"] else None
        if v["key_env"] and not key and not v.get("key_optional"):
            raise RuntimeError(f"{v['key_env']} is not set (put it in .env)")
        model = model or os.environ.get(f"{vendor.upper()}_MODEL") or v["model"]
        kw.setdefault("extra", v.get("extra"))
        return cls(v["base_url"], key, model, name=vendor, **kw)

    async def list_models(self) -> list[str]:
        page = await self._client.models.list()
        return sorted(m.id for m in page.data)

    async def suggest(self, req: SuggestionRequest) -> AsyncIterator[SuggestionEvent]:
        content = (req.user if req.user is not None
                   else user_content(req.question, req.history, lead=req.lead))
        messages = [{"role": "system", "content": req.system},
                    {"role": "user", "content": content}]
        usage = None
        finish = None
        model = self.model
        first = True
        # Close the stream only if we did NOT drain it (an error mid-iteration). Closing an
        # already-exhausted stream makes httpcore2 athrow into a finished body generator and
        # print "generator didn't stop after athrow()"; draining it fully needs no close.
        stream = None
        drained = False
        try:
            kwargs = dict(model=self.model, messages=messages, stream=True,
                          max_tokens=req.max_tokens, temperature=self.temperature)
            if self.extra:
                kwargs["extra_body"] = self.extra   # merged into the JSON body by the openai SDK
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                u = getattr(chunk, "usage", None)
                if u is not None:
                    usage = {"input_tokens": getattr(u, "prompt_tokens", None),
                             "output_tokens": getattr(u, "completion_tokens", None)}
                if getattr(chunk, "model", None):
                    model = chunk.model
                if not chunk.choices:
                    continue
                # gateways vary in which fields they send; never fail the answer over one
                finish = getattr(chunk.choices[0], "finish_reason", None) or finish
                delta = chunk.choices[0].delta.content or ""
                if not delta:
                    continue
                yield SuggestionEvent("first_token" if first else "token", time.time(), delta, model=model)
                first = False
            drained = True
            if first:
                # Some gateways answer a streaming request with an
                # empty stream but answer the same request fine non-streamed. Retry once that way; the
                # first token then arrives with the whole text, so TTFT == total, which is the truth.
                kwargs["stream"] = False
                kwargs.pop("extra_body", None)
                extra = {k: v for k, v in self.extra.items() if k != "stream_options"}
                if extra:
                    kwargs["extra_body"] = extra
                resp = await self._client.chat.completions.create(**kwargs)
                u = getattr(resp, "usage", None)
                if u is not None:
                    usage = {"input_tokens": getattr(u, "prompt_tokens", None),
                             "output_tokens": getattr(u, "completion_tokens", None)}
                model = getattr(resp, "model", None) or model
                if resp.choices:
                    finish = getattr(resp.choices[0], "finish_reason", None) or finish
                text = (resp.choices[0].message.content or "") if resp.choices else ""
                if text:
                    yield SuggestionEvent("first_token", time.time(), text, model=model)
            yield SuggestionEvent("done", time.time(), usage=usage, model=model, finish_reason=finish)
        except Exception as e:  # the provider contract: never raise, report
            yield SuggestionEvent("error", time.time(), f"{type(e).__name__}: {e}", model=model)
        finally:
            if stream is not None and not drained:
                try:
                    await stream.close()
                except Exception:
                    pass

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await close()
