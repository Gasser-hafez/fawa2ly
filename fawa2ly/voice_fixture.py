"""
Voice a script, stream it through Deepgram at real speed, and score what the pipeline answers.

    python -m fawa2ly.voice_fixture docs/eval/scripts/hr-egyptian-ar.yaml --lang ar-EG
    python -m fawa2ly.voice_fixture docs/eval/scripts/hr-egyptian-ar.yaml --score-only

synth.py writes the transcript a script *should* produce. It cannot say what Deepgram actually
returns for a language, and for Egyptian Arabic that is the whole problem: ar-EG punctuated 4 of
rehearsal8's 17 Arabic finals, none of them Egyptian speech. So this voices every interviewer turn
with Gemini's text-to-speech, lays the turns out on one track with the script's pauses (a candidate
turn becomes the silence it takes to say), streams the track to Deepgram at real speed as the
loopback stream would, and replays Deepgram's raw log through the turn-mode pipeline the engine
runs for the other party. Each interviewer turn carries `expect` in the script (question,
statement, fragment), so the report is a score rather than a transcript to read.

Written next to the working directory, named after the script (or -o):
  <name>.voice.wav         the track, 16 kHz mono
  <name>.voice.raw.jsonl   Deepgram's messages; replayable with fawa2ly.replay
  <name>.voice.score.json  the report below
  <name>.voice.tts/        one clip per voiced turn, keyed by text and voice: a rerun costs nothing
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from .assist import QUESTION_TRIGGERS
from .audio.resample import TARGET_SR, to_16k_mono
from .console import safe_console
from .pipeline import Pipeline
from .stt.deepgram import DeepgramSTT
from .stt.fake import FakeSTT
from .synth import SPEAKERS, TURN_PAUSE, WORDS_PER_SEC, Script

TTS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_VOICE = "Kore"
TTS_STYLE = "Say it like an Egyptian HR interviewer on a video call, calm and natural:"
LEAD_IN_S = 0.5     # silence before the first turn, as in synth.lay_out_words
TAIL_S = 3.0        # silence after the last turn, so an UtteranceEnd closes it
CHUNK_S = 0.1       # audio sent per websocket message, as the capture loop sends it
PAD_S = 0.3         # a closed turn within this of a script turn is about that turn


@dataclass
class Span:
    index: int              # turn number in the script, from 1
    who: str
    text: str
    expect: Optional[str]   # question | statement | fragment, or None for candidate turns
    start: float            # seconds on the track
    end: float


@dataclass
class Closed:
    trigger: str
    text: str
    start: Optional[float]   # audio clock of the closed turn's first and last word
    end: Optional[float]
    t_close: Optional[float]  # audio clock when the pipeline closed it


def trim(pcm: bytes, threshold: int = 300, margin_s: float = 0.05) -> bytes:
    """Drop the silence a text-to-speech clip starts and ends with, so the script's pauses are
    the pauses on the track."""
    a = np.frombuffer(pcm, dtype=np.int16)
    loud = np.flatnonzero(np.abs(a.astype(np.int32)) > threshold)
    if loud.size == 0:
        return b""
    m = int(margin_s * TARGET_SR)
    return a[max(0, loud[0] - m): loud[-1] + m + 1].tobytes()


def speak(text: str, key: str, voice: str = TTS_VOICE, model: str = TTS_MODEL) -> tuple[bytes, int, int, str]:
    """One Gemini text-to-speech request -> (pcm, sample_rate, channels, what the response said)."""
    body = {"model": model, "input": f"{TTS_STYLE} {text}", "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}}
    data = None
    for attempt in range(6):
        req = urllib.request.Request(TTS_URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code in (429, 500, 503) and attempt < 5:
                wait = 15 * (attempt + 1)
                print(f"[voice] text-to-speech HTTP {e.code}, retrying in {wait} s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"text-to-speech HTTP {e.code}: {detail}") from None
    for step in (data or {}).get("steps", []):
        for part in step.get("content", []):
            if part.get("data"):
                rate = int(part.get("sample_rate") or 24000)
                channels = int(part.get("channels") or 1)
                meta = f"mime_type={part.get('mime_type')} sample_rate={part.get('sample_rate')} channels={part.get('channels')}"
                return base64.b64decode(part["data"]), rate, channels, meta
    raise RuntimeError("text-to-speech returned no audio")


def _clip_path(cache: Path, model: str, voice: str, text: str) -> Path:
    return cache / (hashlib.sha1(f"{model}|{voice}|{TTS_STYLE}|{text}".encode()).hexdigest()[:16] + ".pcm")


def voice_clips(script: Script, cache: Path, key: Optional[str], voice: str = TTS_VOICE,
                model: str = TTS_MODEL) -> dict[int, bytes]:
    """Turn number -> trimmed 16 kHz clip for every interviewer turn. A clip already in `cache` is
    reused whichever text-to-speech model made it: the first run (2026-09-11) ran into the free
    tier's quota for one model 11 clips in, and the rest had to come from another. With key=None a
    missing clip is an error rather than a request."""
    cache.mkdir(parents=True, exist_ok=True)
    clips: dict[int, bytes] = {}
    shown_meta = False
    known = (model, TTS_MODEL, "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")
    for i, turn in enumerate(script.turns, 1):
        if SPEAKERS[turn.get("who", "interviewer")] != 0:
            continue
        text = " ".join(str(turn["text"]).split())
        made = [_clip_path(cache, m, voice, text) for m in dict.fromkeys(known)]
        path = next((p for p in made if p.exists()), made[0])
        if not path.exists():
            if key is None:
                raise RuntimeError(f"turn {i} has not been voiced yet; run without --score-only first")
            pcm, rate, channels, meta = speak(text, key, voice, model)
            if not shown_meta:
                print(f"[voice] audio part: {meta}")
                shown_meta = True
            path.write_bytes(to_16k_mono(pcm, channels, rate))
            print(f"[voice] turn {i}: {len(pcm) / 2 / channels / rate:.1f} s")
        clips[i] = trim(path.read_bytes())
    return clips


def lay_out(script: Script, clips: dict[int, bytes]) -> tuple[bytes, list[Span]]:
    """Clips and pauses -> one 16 kHz mono track, and where each script turn sits on it."""
    track = bytearray()
    spans: list[Span] = []

    def now() -> float:
        return len(track) / 2 / TARGET_SR

    def silence(seconds: float) -> None:
        track.extend(b"\x00\x00" * int(seconds * TARGET_SR))

    silence(LEAD_IN_S)
    for i, turn in enumerate(script.turns, 1):
        silence(float(turn.get("pause", TURN_PAUSE)))
        text = " ".join(str(turn["text"]).split())
        start = now()
        if i in clips:
            track.extend(clips[i])
        else:
            silence(len(text.split()) / WORDS_PER_SEC)
        spans.append(Span(i, turn.get("who", "interviewer"), text, turn.get("expect"), start, now()))
    silence(TAIL_S)
    return bytes(track), spans


async def listen(track: bytes, key: str, lang: str, raw_path: Path) -> None:
    """Stream the track to Deepgram in real time and log every message it sends back."""
    step = int(CHUNK_S * TARGET_SR) * 2
    total = len(track) / 2 / TARGET_SR
    with open(raw_path, "w", encoding="utf-8") as raw:
        async with DeepgramSTT(key, lang, raw_log=raw) as stt:
            async def drain() -> None:
                async for _ in stt.events():
                    pass

            reader = asyncio.create_task(drain())
            t0 = time.time()
            next_note = 30.0
            for k, off in enumerate(range(0, len(track), step)):
                due = t0 + k * CHUNK_S
                delay = due - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                await stt.send_audio(track[off:off + step], t_capture=due)
                if k * CHUNK_S >= next_note:
                    print(f"[voice] streamed {next_note:.0f} of {total:.0f} s")
                    next_note += 30.0
            await stt.finish()
            try:
                await asyncio.wait_for(reader, timeout=15)
            except asyncio.TimeoutError:
                reader.cancel()


async def closed_turns(raw_path: Path) -> list[Closed]:
    """Replay the raw log through the pipeline the engine builds for the other party."""
    pipe = Pipeline(stream="other", turns=True, speculate=True)
    out: list[Closed] = []
    async for ev in FakeSTT(str(raw_path)).events():
        span = (pipe.turn_start, pipe.audio_end)   # the open turn, before this event can close it
        for u in pipe.feed(ev):
            if not u.speculative:
                out.append(Closed(u.trigger, u.text, span[0], span[1], ev.t_rel))
    return out


def _overlaps(c: Closed, sp: Span, pad: float) -> bool:
    return (c.start is not None and c.end is not None
            and c.start <= sp.end + pad and c.end >= sp.start - pad)


def score(spans: list[Span], closed: list[Closed], pad: float = PAD_S) -> dict:
    """A question is caught when a turn closed over it as a question. A statement is a false alarm
    when it fired on its own; one glued into the same turn as a real question is not."""
    rows = []
    for sp in spans:
        if sp.expect is None:
            continue
        hit = [c for c in closed if _overlaps(c, sp, pad)]
        fired = [c for c in hit if c.trigger in QUESTION_TRIGGERS]
        if sp.expect == "question":
            verdict = "caught" if fired else "missed"
        elif sp.expect == "statement":
            shared = any(o.expect == "question" and _overlaps(c, o, pad)
                         for c in fired for o in spans if o is not sp)
            verdict = "false alarm" if fired and not shared else "ok"
        else:
            verdict = "ok"
        latency = None
        if fired and fired[-1].t_close is not None:
            latency = round(fired[-1].t_close - sp.end, 2)
        rows.append({"turn": sp.index, "expect": sp.expect, "verdict": verdict,
                     "trigger": fired[-1].trigger if fired else (hit[-1].trigger if hit else None),
                     "latency_s": latency, "said": sp.text, "heard": " | ".join(c.text for c in hit)})
    questions = [r for r in rows if r["expect"] == "question"]
    statements = [r for r in rows if r["expect"] == "statement"]
    lat = sorted(r["latency_s"] for r in questions if r["verdict"] == "caught" and r["latency_s"] is not None)
    return {"rows": rows,
            "questions": len(questions), "caught": sum(r["verdict"] == "caught" for r in questions),
            "statements": len(statements),
            "false_alarms": sum(r["verdict"] == "false alarm" for r in statements),
            "median_latency_s": lat[len(lat) // 2] if lat else None}


def print_report(report: dict) -> None:
    for r in report["rows"]:
        lat = f"+{r['latency_s']:.1f} s" if r["latency_s"] is not None else ""
        print(f"#{r['turn']:>2} {r['expect']:<9} {r['verdict']:<11} {r['trigger'] or '-':<15} {lat}")
        print(f"     said:  {r['said']}")
        print(f"     heard: {r['heard'] or '(nothing)'}")
    print(f"[score] questions caught {report['caught']}/{report['questions']}, "
          f"statements answered {report['false_alarms']}/{report['statements']}, "
          f"median close after the question ended: {report['median_latency_s']} s")


async def _run(a) -> int:
    safe_console()
    script = Script.load(a.script)
    stem = Path(a.out) if a.out else Path(Path(a.script).stem + ".voice")
    raw_path = Path(f"{stem}.raw.jsonl")
    cache = Path(f"{stem}.tts")
    if a.score_only:
        track, spans = lay_out(script, voice_clips(script, cache, None, a.voice))
    else:
        try:
            from dotenv import load_dotenv
            from .settings import env_path
            load_dotenv(".env")
            load_dotenv(env_path())
        except ImportError:
            pass
        missing = [n for n in ("GEMINI_API_KEY", "DEEPGRAM_API_KEY") if not os.environ.get(n)]
        if missing:
            print(f"[voice] missing {', '.join(missing)} in .env")
            return 2
        track, spans = lay_out(script, voice_clips(script, cache, os.environ["GEMINI_API_KEY"], a.voice, a.tts_model))
        with wave.open(f"{stem}.wav", "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TARGET_SR)
            w.writeframes(track)
        print(f"[voice] {sum(1 for s in spans if s.expect)} voiced turns, "
              f"{len(track) / 2 / TARGET_SR:.0f} s track -> {stem}.wav; streaming to Deepgram ({a.lang})")
        await listen(track, os.environ["DEEPGRAM_API_KEY"], a.lang, raw_path)
    report = score(spans, await closed_turns(raw_path))
    print_report(report)
    Path(f"{stem}.score.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="script YAML with `expect` on interviewer turns (docs/eval/scripts/)")
    ap.add_argument("--lang", default="ar-EG", help="Deepgram language, as the Listening setting")
    ap.add_argument("--voice", default=TTS_VOICE, help="Gemini text-to-speech voice")
    ap.add_argument("--tts-model", default=TTS_MODEL,
                    help="text-to-speech model for clips not made yet; each model has its own quota")
    ap.add_argument("-o", "--out", default=None, help="output name (default: <script>.voice)")
    ap.add_argument("--score-only", action="store_true",
                    help="no text-to-speech or Deepgram: replay the existing raw log with the current detector")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
