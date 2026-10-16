"""
Deepgram Nova-3 streaming STT.

parse_message() is the single place that understands Deepgram's JSON. It is
shared with FakeSTT, which replays the raw log this class writes, so live and
replayed sessions go through byte-for-byte the same parsing.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Optional, TextIO

import websockets

from .base import STTEvent, STTProvider, Word

DEFAULT_PARAMS = {
    "model": "nova-3",
    "encoding": "linear16",
    "sample_rate": 16000,
    "channels": 1,
    "interim_results": "true",
    "punctuate": "true",
    "smart_format": "true",
    "endpointing": 300,          # ms of silence before a segment is finalized (tuned in M0)
    "utterance_end_ms": 1000,    # ms of silence before UtteranceEnd (tuned in M0)
    "vad_events": "true",
    "diarize": "true",
}


def build_url(lang: str, **overrides) -> str:
    params = {**DEFAULT_PARAMS, "language": lang, **overrides}
    return "wss://api.deepgram.com/v1/listen?" + "&".join(f"{k}={v}" for k, v in params.items())


def parse_message(d: dict, t_recv: float, t_rel: Optional[float]) -> Optional[STTEvent]:
    """Deepgram message -> STTEvent, or None for messages the pipeline ignores
    (Metadata, SpeechStarted, empty transcripts)."""
    kind = d.get("type")
    if kind == "Results":
        alt = d["channel"]["alternatives"][0]
        text = alt.get("transcript", "")
        if not text:
            return None
        words = [
            Word(w.get("punctuated_word", w.get("word", "")),
                 float(w.get("start", 0.0)), float(w.get("end", 0.0)), w.get("speaker"))
            for w in alt.get("words", [])
        ]
        return STTEvent("final" if d.get("is_final") else "interim", t_recv, t_rel, text, words, d,
                        endpointed=bool(d.get("speech_final")))
    if kind == "UtteranceEnd":
        return STTEvent("utterance_end", t_recv, t_rel, raw=d)
    if kind == "Error":
        return STTEvent("error", t_recv, t_rel, text=json.dumps(d), raw=d)
    return None


class DeepgramSTT(STTProvider):
    """
    Usage:
        async with DeepgramSTT(key, "en", raw_log=open("x.raw.jsonl", "a")) as stt:
            ... await stt.send_audio(chunk, t_capture=...) from one task
            ... async for ev in stt.events(): ... from another

    raw_log: every message is appended there as {"t_recv", "t_rel", "msg"} so
    FakeSTT can replay the session without spending credits.
    """

    def __init__(self, api_key: str, lang: str = "en", raw_log: Optional[TextIO] = None, **params):
        self._url = build_url(lang, **params)
        self._key = api_key
        self._raw_log = raw_log
        self._ws = None
        self._keepalive: Optional[asyncio.Task] = None
        self.sample_rate = int(params.get("sample_rate", DEFAULT_PARAMS["sample_rate"]))
        self.t_stream_start: Optional[float] = None  # wall clock of the first audio sample sent
        # Audio clock anchor: (audio seconds sent so far, wall clock of the last sample sent).
        # Deepgram's word times count audio bytes, not wall time, so any gap in capture (loopback
        # idles when nothing is playing; interview8 started 1.6 s before the video) would make a
        # "first byte + elapsed wall time" clock run ahead of the audio for the whole session.
        self._clock: Optional[tuple[float, float]] = None
        self._next_clock_log = 0.0

    async def __aenter__(self):
        self._ws = await websockets.connect(
            self._url, additional_headers={"Authorization": f"Token {self._key}"}
        )
        self._keepalive = asyncio.create_task(self._keepalive_loop())
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def _keepalive_loop(self):
        while True:
            await asyncio.sleep(5)
            await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def send_audio(self, chunk: bytes, t_capture: Optional[float] = None) -> None:
        """chunk: 16-bit mono PCM at self.sample_rate. t_capture: wall clock of its first sample
        (capture time, not send time, or queueing delay makes every latency look shorter)."""
        dur = len(chunk) / (self.sample_rate * 2)
        if t_capture is None:
            t_capture = time.time() - dur
        pos = (self._clock[0] if self._clock else 0.0) + dur
        self._clock = (pos, t_capture + dur)
        if self.t_stream_start is None:
            self.t_stream_start = t_capture
        if self._raw_log is not None and pos >= self._next_clock_log:  # every ~5 s, for post-mortems
            self._next_clock_log = pos + 5.0
            self._raw_log.write(json.dumps({"t_recv": t_capture + dur, "t_rel": pos,
                                            "msg": {"type": "AudioClock", "audio_s": pos}}) + "\n")
        await self._ws.send(chunk)

    def audio_clock(self, now: float) -> Optional[float]:
        """Audio-clock reading (seconds, same scale as words[].end) at wall time `now`:
        audio sent so far plus the wall time elapsed since its last sample was captured."""
        if self._clock is None:
            return None
        pos, t_last = self._clock
        return pos + (now - t_last)

    async def finish(self) -> None:
        """Tell Deepgram no more audio is coming; it flushes finals and closes the socket."""
        await self._ws.send(json.dumps({"type": "CloseStream"}))

    async def events(self) -> AsyncIterator[STTEvent]:
        async for msg in self._ws:
            d = json.loads(msg)
            t_recv = time.time()
            t_rel = self.audio_clock(t_recv)
            if self._raw_log is not None:
                self._raw_log.write(json.dumps({"t_recv": t_recv, "t_rel": t_rel, "msg": d},
                                               ensure_ascii=False) + "\n")
                self._raw_log.flush()
            ev = parse_message(d, t_recv, t_rel)
            if ev is not None:
                yield ev

    async def close(self) -> None:
        if self._keepalive is not None:
            self._keepalive.cancel()
            self._keepalive = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
