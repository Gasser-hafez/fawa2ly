"""
Checks before a call (2026-10-16).

A model a provider still lists can refuse a request: Gemini listed gemini-2.5-flash and answered
404 "no longer available to new users" in the first question of a real interview. Listing models
proves the key, not the model. So the model is asked for a few tokens before the call starts, and a
call whose model or Deepgram key does not work is not started: the user finds out in Settings, not
in front of an interviewer.

A model that answered is remembered for a while, so starting several calls in a row does not spend
a request on each one.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

PROBE_PROMPT = "Reply with the single word: ready"
PROBE_TOKENS = 16
VERIFIED_FOR_S = 30 * 60
_verified: dict[tuple[str, str], float] = {}     # (vendor, model) -> when it last answered


def explain(error: str) -> str:
    """The provider's error in words a user can act on."""
    e = error.lower()
    if "404" in e or "not found" in e or "no longer available" in e or "does not exist" in e:
        return "this model is not available on your account; pick another one"
    if "401" in e or "403" in e or "invalid api key" in e or "unauthorized" in e or "permission" in e:
        return "the key was refused; check it under Settings > API keys"
    if "429" in e or "rate limit" in e or "quota" in e or "resource_exhausted" in e:
        return "the provider is rate limiting this key right now; wait a minute or pick another provider"
    if "connect" in e or "timed out" in e or "no answer in time" in e:
        return "the provider did not answer; check the address and your connection"
    return error


async def probe_model(vendor: str, model: Optional[str], timeout: float = 20.0) -> dict:
    """Ask the model for a few tokens. Any answer, even an empty one, counts: the point is that the
    request is accepted, not what comes back."""
    from .llm.openai_compat import OpenAICompatibleLLM
    t0 = time.monotonic()
    llm = None
    try:
        llm = OpenAICompatibleLLM.from_env(vendor, model)
        kwargs = {"model": llm.model, "messages": [{"role": "user", "content": PROBE_PROMPT}],
                  llm.token_param: PROBE_TOKENS}
        if llm.temperature is not None:
            kwargs["temperature"] = llm.temperature
        extra = {k: v for k, v in llm.extra.items() if k != "stream_options"}
        if extra:
            kwargs["extra_body"] = extra
        await asyncio.wait_for(llm._client.chat.completions.create(**kwargs), timeout)
        _verified[(vendor, llm.model)] = time.monotonic()
        return {"vendor": vendor, "model": llm.model, "ok": True, "ms": round((time.monotonic() - t0) * 1000)}
    except Exception as e:
        raw = "no answer in time" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
        return {"vendor": vendor, "model": model or (llm.model if llm else ""), "ok": False,
                "error": explain(raw), "detail": raw[:300]}
    finally:
        if llm is not None:
            try:
                await llm.close()
            except Exception:
                pass


async def probe_models(vendor: str, models: list[str], limit: int = 25, parallel: int = 3) -> list[dict]:
    """Several models of one provider, a few at a time so the probe is not itself rate limited."""
    gate = asyncio.Semaphore(parallel)

    async def one(m):
        async with gate:
            return await probe_model(vendor, m)
    return list(await asyncio.gather(*(one(m) for m in models[:limit])))


async def check_deepgram(timeout: float = 10.0) -> Optional[str]:
    """None when the Deepgram key works, else what is wrong."""
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        return "no Deepgram key: add one under Settings > API keys, or set Listening to this computer"
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get("https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {key}"})
    except Exception as e:
        return f"could not reach Deepgram: {type(e).__name__}"
    if r.status_code in (401, 403):
        return "Deepgram refused the key; check it under Settings > API keys"
    if r.status_code >= 400:
        return f"Deepgram answered {r.status_code}"
    return None


async def preflight_call(chosen: dict) -> list[str]:
    """What would break this call, in plain words; empty when it can start."""
    from . import settings as user_settings
    s = user_settings.load()
    vendor = chosen.get("vendor") or s.vendor
    model = chosen.get("model") if "model" in chosen else s.model
    stt = chosen.get("stt_vendor") or s.stt_vendor
    suggest = chosen.get("suggest", s.suggest)

    async def model_problem():
        if suggest is False:
            return None
        from .llm.openai_compat import all_vendors
        v = all_vendors().get(vendor)
        label = v["label"] if v else vendor
        resolved = model or (v or {}).get("model") or ""
        if resolved and time.monotonic() - _verified.get((vendor, resolved), -1e9) < VERIFIED_FOR_S:
            return None
        r = await probe_model(vendor, model)
        return None if r["ok"] else f"{label} {r['model']}: {r['error']}".strip()

    async def stt_problem():
        return await check_deepgram() if stt == "deepgram" else None

    return [p for p in await asyncio.gather(model_problem(), stt_problem()) if p]
