"""
Health check for everything this project talks to.

    python -m fawa2ly.doctor              # config, offline paths, then one live call per provider
    python -m fawa2ly.doctor --offline    # config and offline paths only, no network, no quota
    python -m fawa2ly.doctor --vendor gemini

Checks, in order:
  config    which keys are present in .env (names only, never values), presets, sample CV
  offline   the paths that must work with no network: FakeSTT replay -> Pipeline -> detector,
            FakeLLM -> guardrail, and whether local transcription (faster-whisper) is installed
  stt       Deepgram: connect, send a second of silence, close cleanly
  llm       each configured provider: one tiny request, reporting what answered and how fast

A provider with no key is reported as skipped, not failed. Exit code is 1 only if something
that is configured is broken.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

from .context import ContextBuilder, Preset, available, filter_bullets, load_cv
from .console import safe_console
from .llm import VENDORS, FakeLLM, OpenAICompatibleLLM, collect, mute_asyncgen_close_noise
from .pipeline import Pipeline
from .stt import FakeSTT

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

OK, FAIL, SKIP, WARN = "ok", "FAIL", "skip", "warn"

# The sample CV is a development fixture in the project folder, and it is not shipped: the installer
# carries the engine and the presets, not a fictional person's CV. The doctor runs in both places, so
# where it is missing the config check skips rather than fails (a tester reading FAIL would think
# their install was broken, which is what a packaged build reported until 2026-09-12), and the
# builder check below falls back to the few lines here, which is all it needs to prove the prompt is
# assembled and the guardrail keeps what comes back.
SAMPLE_CV = os.path.join("docs", "sample_cv.txt")
FALLBACK_CV = ("Sample Candidate. Software engineer, four years. Cut an order search endpoint from "
               "1.4 s to 180 ms at p95 by fixing N+1 queries and adding an index. Python, Django, "
               "PostgreSQL.")


@dataclass
class Check:
    area: str
    name: str
    status: str
    detail: str = ""

    def line(self) -> str:
        mark = {OK: "  ok  ", FAIL: " FAIL ", SKIP: " skip ", WARN: " warn "}[self.status]
        return f"[{mark}] {self.area:<7} {self.name:<34} {self.detail}"


def check_config() -> list[Check]:
    out = []
    for vendor, v in VENDORS.items():
        env = v.get("key_env")
        if not env:
            out.append(Check("config", f"{vendor} key", OK, "no key needed"))
        elif os.environ.get(env):
            out.append(Check("config", f"{vendor} key", OK, f"{env} is set"))
        elif v.get("key_optional"):
            out.append(Check("config", f"{vendor} key", WARN, f"{env} not set (optional)"))
        else:
            out.append(Check("config", f"{vendor} key", SKIP, f"{env} not set"))
    out.append(Check("config", "DEEPGRAM_API_KEY", OK if os.environ.get("DEEPGRAM_API_KEY") else SKIP,
                     "set" if os.environ.get("DEEPGRAM_API_KEY") else "not set, STT check skipped"))
    names = available()
    out.append(Check("config", "presets", OK if names else FAIL, ", ".join(names) or "none found"))
    if not os.path.isfile(SAMPLE_CV):
        out.append(Check("config", "sample CV", SKIP, f"no {SAMPLE_CV} here; not shipped with the app"))
    else:
        try:
            n = len(load_cv(SAMPLE_CV).split())
            out.append(Check("config", "sample CV", OK, f"{SAMPLE_CV}, {n} words"))
        except Exception as e:
            out.append(Check("config", "sample CV", FAIL, f"{type(e).__name__}: {e}"))
    return out


def _raw_line(t_recv, t_rel, msg) -> str:
    return json.dumps({"t_recv": t_recv, "t_rel": t_rel, "msg": msg})


def _sample_raw_log(path: str) -> None:
    """A two-segment session: a question, then an answer, with word timings."""
    def final(words):
        return {"type": "Results", "is_final": True, "channel": {"alternatives": [
            {"transcript": " ".join(w[0] for w in words),
             "words": [{"word": t.lower().strip("?."), "punctuated_word": t,
                        "start": s, "end": e, "speaker": spk} for t, s, e, spk in words]}]}}
    q = [("What", 1.0, 1.2, 0), ("hours", 1.2, 1.5, 0), ("can", 1.5, 1.6, 0),
         ("you", 1.6, 1.7, 0), ("work?", 1.7, 2.1, 0)]
    a = [("From", 2.5, 2.7, 1), ("nine.", 2.7, 3.1, 1)]
    with open(path, "w", encoding="utf-8") as f:
        f.write(_raw_line(100.0, 2.5, final(q)) + "\n")
        f.write(_raw_line(101.0, 3.6, final(a)) + "\n")
        f.write(_raw_line(102.0, 4.6, {"type": "UtteranceEnd", "last_word_end": 3.1}) + "\n")


async def check_offline(tmp_dir: str) -> list[Check]:
    out = []
    path = os.path.join(tmp_dir, "doctor_sample.raw.jsonl")
    try:
        _sample_raw_log(path)
        pipe = Pipeline()
        utterances = []
        async for ev in FakeSTT(path).events():
            utterances += pipe.feed(ev)
        fired = [u for u in utterances if u.trigger == "question_mark"]
        if fired and fired[0].text.strip() == "What hours can you work?":
            out.append(Check("offline", "STT replay -> detector", OK,
                             f"{len(utterances)} utterances, question caught at "
                             f"{fired[0].question_end_to_fire_ms} ms"))
        else:
            out.append(Check("offline", "STT replay -> detector", FAIL,
                             f"expected the question to fire, got {[u.trigger for u in utterances]}"))
    except Exception as e:
        out.append(Check("offline", "STT replay -> detector", FAIL, f"{type(e).__name__}: {e}"))
    finally:
        if os.path.exists(path):
            os.remove(path)

    try:
        cv = load_cv(SAMPLE_CV) if os.path.isfile(SAMPLE_CV) else FALLBACK_CV
        builder = ContextBuilder(preset=Preset.find("swe"), cv_text=cv)
        req = builder.build("What is your biggest weakness?")
        s = await collect(FakeLLM(), req)
        kept, _ = filter_bullets(s.bullets)
        ok = bool(kept) and "Software engineer" in req.system and "N+1" in req.system
        out.append(Check("offline", "builder -> FakeLLM -> guardrail", OK if ok else FAIL,
                         f"prompt {len(req.system.split())} words, {len(kept)} bullet(s) kept"))
    except Exception as e:
        out.append(Check("offline", "builder -> FakeLLM -> guardrail", FAIL, f"{type(e).__name__}: {e}"))
    return out


def check_whisper() -> list[Check]:
    """Local STT libraries for offline mode (roadmap item 26): faster-whisper and
    webrtcvad-wheels, and whether a model has already been downloaded into
    %APPDATA%/Fawa2ly/models/whisper. The state machine itself (VAD framing, endpointing, utterance_end) is
    exercised by tests/test_stt.py with a fake VAD and a fake model, not here, so this check
    needs no download and no audio device."""
    from .stt import whisper_local
    if not whisper_local.available():
        missing = []
        for name in ("faster_whisper", "webrtcvad"):
            try:
                __import__(name)
            except ImportError:
                missing.append(name)
        # Offline STT is opt-in (roadmap item 26): a machine that has never asked for --stt-vendor whisper should not show a doctor FAIL.
        return [Check("offline", "whisper libraries", SKIP,
                      f"not importable: {', '.join(missing) or 'unknown'}; "
                      "only needed for offline transcription (roadmap item 26)")]
    out = [Check("offline", "whisper libraries", OK, "faster-whisper and webrtcvad import")]
    d = whisper_local.models_dir()
    downloaded = os.path.isdir(d) and bool(os.listdir(d))
    out.append(Check("offline", "whisper model", OK if downloaded else SKIP,
                     f"in {d}" if downloaded else
                     f"not downloaded yet ({whisper_local.DEFAULT_MODEL}, ~480 MB int8 on first use)"))
    return out


async def check_stt(seconds: float = 1.0, timeout: float = 25.0) -> Check:
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        return Check("stt", "deepgram", SKIP, "DEEPGRAM_API_KEY not set")
    from .stt import DeepgramSTT
    raw = io.StringIO()
    t0 = time.time()
    try:
        async with DeepgramSTT(key, "en", raw_log=raw) as stt:
            connected = (time.time() - t0) * 1000

            async def send():
                t = time.time()
                for i in range(int(seconds / 0.05)):
                    await stt.send_audio(bytes(1600), t_capture=t + i * 0.05)
                    await asyncio.sleep(0.01)
                await stt.finish()

            async def receive():
                try:
                    async for _ in stt.events():
                        pass
                except Exception:
                    pass

            await asyncio.wait_for(asyncio.gather(send(), receive()), timeout=timeout)
        messages = sum(1 for line in raw.getvalue().splitlines() if line)
        return Check("stt", "deepgram", OK, f"connected in {connected:.0f} ms, {messages} messages")
    except Exception as e:
        return Check("stt", "deepgram", FAIL, f"{type(e).__name__}: {str(e)[:90]}")


async def check_llm(vendor: str, timeout: float = 60.0) -> Check:
    v = VENDORS[vendor]
    env = v.get("key_env")
    if env and not os.environ.get(env) and not v.get("key_optional"):
        return Check("llm", vendor, SKIP, f"{env} not set")
    if not v.get("model"):
        return Check("llm", vendor, SKIP, "no default model; pass --model")
    try:
        provider = OpenAICompatibleLLM.from_env(vendor)
    except Exception as e:
        return Check("llm", vendor, FAIL, f"{type(e).__name__}: {str(e)[:90]}")
    # The real system prompt, so this checks the provider against what production sends. A
    # simplified prompt made models answer in first-person sentences, which the guardrail then
    # rejected, and the provider looked broken when it was not.
    req = ContextBuilder().build("What is your biggest weakness?")
    try:
        s = await asyncio.wait_for(collect(provider, req), timeout=timeout)
    except asyncio.TimeoutError:
        return Check("llm", vendor, FAIL, f"no answer within {timeout:.0f} s")
    except Exception as e:
        return Check("llm", vendor, FAIL, f"{type(e).__name__}: {str(e)[:90]}")
    finally:
        await provider.close()
    if s.error and v.get("local") and "APIConnectionError" in s.error:
        # a local server that is simply not running is not a broken setup
        return Check("llm", vendor, SKIP, f"nothing listening at {v['base_url']}")
    if s.error:
        return Check("llm", vendor, FAIL, s.error[:110])
    kept, _ = filter_bullets(s.bullets)
    detail = (f"{s.model}, first token {s.ttft_ms} ms, {len(kept)} bullet(s)"
              f"{', in=' + str(s.usage['input_tokens']) if s.usage and s.usage.get('input_tokens') else ''}"
              f"{', TRUNCATED' if s.truncated else ''}")
    if not kept:
        return Check("llm", vendor, FAIL, f"{detail}; raw: {s.text[:70]!r}")
    return Check("llm", vendor, OK if not s.truncated else WARN, detail)


async def run(a) -> int:
    safe_console()
    mute_asyncgen_close_noise()
    checks = check_config()
    checks += await check_offline(a.tmp_dir)
    checks += check_whisper()
    if not a.offline:
        checks.append(await check_stt())
        vendors = [a.vendor] if a.vendor else list(VENDORS)
        for vendor in vendors:
            checks.append(await check_llm(vendor))
    for c in checks:
        print(c.line())
    counts = {s: sum(1 for c in checks if c.status == s) for s in (OK, WARN, SKIP, FAIL)}
    print(f"\n{counts[OK]} ok, {counts[WARN]} warn, {counts[SKIP]} skipped, {counts[FAIL]} failed")
    if counts[FAIL]:
        print("failing: " + ", ".join(f"{c.area}/{c.name}" for c in checks if c.status == FAIL))
    return 1 if counts[FAIL] else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip every network call")
    ap.add_argument("--vendor", default=None, help="check only this provider")
    ap.add_argument("--tmp-dir", default=".", help="where to write the temporary replay sample")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
