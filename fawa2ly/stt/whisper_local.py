"""
Local Whisper STT, 2026-09-15.

Runs entirely on this machine: faster-whisper (CTranslate2, CPU, int8 by default)
transcribes each utterance once WebRTC VAD says the speaker paused, so a call
needs no Deepgram key and no audio ever leaves the machine. Together with the
"ollama" LLM vendor (openai_compat.py, already local), this is the last cloud
dependency a call has to make offline.

The trade against Deepgram is latency, and it is a real one, not a detail:
Deepgram streams partial (INTERIM) results as words are spoken, which is what
lets the pipeline fire on a growing "...what hours can you" before the
sentence ends (ADR-5). Whisper decodes a finished array, not a growing one, so
this provider has no interim path - Pipeline(interim_detection=...) simply
never sees one - and a question is judged only once VAD says the speaker
stopped. Endpointing and utterance-end mirror DeepgramSTT's two knobs so the
same tuning experience applies, both measured from the same last-speech point
(Deepgram's own semantics):
  - `endpointing_ms` of silence closes the open segment and transcribes it:
    a "final", marked endpointed=True (WebRTC VAD gives no partial segment to
    cut early the way a forced Deepgram final can).
  - `utterance_end_ms` of continued silence fires "utterance_end", same event
    the pipeline already flushes an open turn on.

Not yet true, and worth stating plainly (this is a first cut
item 26 "to verify" list): no benchmark yet of decode speed on a GPU-less
Windows machine (a segment must decode faster than it took to speak, or a
long call's latency grows without bound - see `available()` and the note on
model size below), and no Egyptian Arabic quality check (the M6 gate applies
here too; Whisper's punctuation, which the detector's sentence-splitter
depends on, is weakest exactly where it is needed).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator, Optional, TextIO

from .base import STTEvent, STTProvider, Word

FRAME_MS = 30                     # webrtcvad only accepts 10, 20 or 30 ms frames
DEFAULT_ENDPOINTING_MS = 500      # silence since the last speech frame that closes a segment
DEFAULT_UTTERANCE_END_MS = 1200   # further silence (same clock) that fires utterance_end
MIN_SPEECH_MS = 200               # a shorter segment is a cough or a click, not worth a decode
DEFAULT_MODEL = "small"           # ~480 MB int8 on disk; see the module note on sizing
DEFAULT_COMPUTE = "int8"

# STT `lang` values this project already uses (server.py, EngineConfig.lang) -> a Whisper
# language code, or None to let Whisper detect it (its "multi" default already means that).
_WHISPER_LANG = {"en": "en", "en-US": "en", "ar": "ar", "ar-EG": "ar", "multi": None}

# One lock for every LocalWhisperSTT in this process: CTranslate2 is not asked to run two
# decodes at once here, so a rehearsal with both the loopback and the microphone transcribed
# (roadmap items: user_mic) takes turns rather than contending for the same CPU cores. Local
# mode already trades speed for staying offline; adding contention on top would not make sense.
_TRANSCRIBE_LOCK = asyncio.Lock()


def models_dir() -> str:
    from .. import settings as user_settings
    return os.path.join(user_settings.app_dir(), "models", "whisper")


def available() -> bool:
    """Whether the libraries this provider needs are installed. The model weights themselves
    download on first use."""
    try:
        import faster_whisper  # noqa: F401
        import webrtcvad  # noqa: F401
    except ImportError:
        return False
    return True


class _Model:
    """One faster-whisper model per (size, compute_type), shared by every stream in this
    process; loading takes a few seconds and downloading a fresh size much longer."""

    _cache: dict = {}
    _lock = asyncio.Lock()

    @classmethod
    async def get(cls, size: str, compute_type: str):
        key = (size, compute_type)
        async with cls._lock:
            if key not in cls._cache:
                loop = asyncio.get_running_loop()
                cls._cache[key] = await loop.run_in_executor(None, cls._load, size, compute_type)
            return cls._cache[key]

    @staticmethod
    def _load(size: str, compute_type: str):
        from faster_whisper import WhisperModel
        return WhisperModel(size, device="cpu", compute_type=compute_type, download_root=models_dir())


class LocalWhisperSTT(STTProvider):
    """
    Usage is the same shape as DeepgramSTT, minus the network:

        async with LocalWhisperSTT("en", raw_log=open(...)) as stt:
            ... await stt.send_audio(chunk, t_capture=...) from one task
            ... async for ev in stt.events(): ... from another

    raw_log gets the same {"t_recv", "t_rel", "msg"} shape DeepgramSTT writes, with
    Deepgram-shaped `msg` values (type Results / UtteranceEnd), so a session recorded offline
    replays, labels and evaluates through the exact same tools (FakeSTT, fawa2ly.replay,
    fawa2ly.eval, fawa2ly.label) with no change to any of them.

    `vad` and `model` are injection points for tests: anything with `.is_speech(frame, rate)`
    and `.transcribe(audio, **kw) -> (segments, info)` respectively, so the real libraries
    never have to be installed to exercise the state machine.
    """

    def __init__(self, lang: str = "en", raw_log: Optional[TextIO] = None,
                 model_size: Optional[str] = None, compute_type: Optional[str] = None,
                 endpointing_ms: int = DEFAULT_ENDPOINTING_MS,
                 utterance_end_ms: int = DEFAULT_UTTERANCE_END_MS,
                 vad_mode: int = 2, vad=None, model=None):
        self.sample_rate = 16000
        self._lang = _WHISPER_LANG.get(lang, None)
        self._model_size = model_size or os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
        self._compute_type = compute_type or os.environ.get("WHISPER_COMPUTE", DEFAULT_COMPUTE)
        self._endpointing_ms = endpointing_ms
        self._utterance_end_ms = utterance_end_ms
        self._vad_mode = vad_mode
        self._vad = vad
        self._model = model
        self._raw_log = raw_log
        self._frame_bytes = int(self.sample_rate * FRAME_MS / 1000) * 2   # int16 mono
        self._out: asyncio.Queue = asyncio.Queue()
        self._buf = bytearray()            # sub-frame remainder between send_audio calls
        self._seg = bytearray()            # audio of the segment being built (speech + hangover)
        self._seg_start: Optional[float] = None   # audio clock (s) when the open segment began
        self._in_speech = False
        self._segment_closed = False       # this segment already finalized on endpointing_ms
        self._utterance_ended = False      # utterance_end already fired for this silence run
        self._silence_ms = 0.0             # since the last speech frame
        self._pos = 0.0                    # audio clock: seconds of audio received so far
        self._closing = False

    async def __aenter__(self) -> "LocalWhisperSTT":
        if self._vad is None:
            import webrtcvad
            self._vad = webrtcvad.Vad(self._vad_mode)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def audio_clock(self, now: float) -> float:
        return self._pos

    async def send_audio(self, chunk: bytes, t_capture: Optional[float] = None) -> None:
        """chunk: 16-bit mono PCM at self.sample_rate. t_capture is accepted for symmetry with
        DeepgramSTT but unused: there is no wall-clock socket to anchor against here, only the
        audio itself, so the clock is audio seconds received, full stop."""
        self._buf += chunk
        while len(self._buf) >= self._frame_bytes:
            frame = bytes(self._buf[: self._frame_bytes])
            del self._buf[: self._frame_bytes]
            self._pos += FRAME_MS / 1000
            await self._step(frame, self._pos)

    async def _step(self, frame: bytes, frame_end: float) -> None:
        try:
            speech = bool(self._vad.is_speech(frame, self.sample_rate))
        except Exception:
            speech = False   # a frame webrtcvad refuses (wrong length at a stream's tail): treat as silence
        if speech:
            if not self._in_speech:
                self._in_speech = True
                self._segment_closed = False
                self._seg_start = frame_end - FRAME_MS / 1000
                self._seg = bytearray()
            self._seg += frame
            self._silence_ms = 0.0
            self._utterance_ended = False
            return
        if self._in_speech:
            self._seg += frame   # keep trailing silence: it is the tail of the last word, not noise
        self._silence_ms += FRAME_MS
        if self._in_speech and not self._segment_closed and self._silence_ms >= self._endpointing_ms:
            await self._finish_segment(endpointed=True)
            self._segment_closed = True
            self._in_speech = False
        if not self._utterance_ended and self._silence_ms >= self._utterance_end_ms:
            self._utterance_ended = True
            await self._out.put(STTEvent("utterance_end", time.time(), frame_end))
            if self._raw_log is not None:
                self._log({"type": "UtteranceEnd", "last_word_end": frame_end}, frame_end)

    async def _finish_segment(self, endpointed: bool) -> None:
        seg, start = bytes(self._seg), self._seg_start
        self._seg = bytearray()
        if len(seg) / (self.sample_rate * 2) * 1000 < MIN_SPEECH_MS:
            return
        async with _TRANSCRIBE_LOCK:
            text, words = await self._transcribe(seg, start)
        if not text:
            return
        ev = STTEvent("final", time.time(), words[-1].end if words else start, text, words,
                      endpointed=endpointed)
        await self._out.put(ev)
        if self._raw_log is not None:
            msg = {"type": "Results", "is_final": True, "speech_final": endpointed,
                   "channel": {"alternatives": [{"transcript": text, "words": [
                       {"word": w.text, "punctuated_word": w.text, "start": w.start,
                        "end": w.end, "speaker": w.speaker} for w in words]}]}}
            self._log(msg, ev.t_rel)

    async def _transcribe(self, pcm: bytes, start: Optional[float]) -> tuple[str, list[Word]]:
        model = self._model
        if model is None:
            model = await _Model.get(self._model_size, self._compute_type)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._decode, model, pcm, start)

    def _decode(self, model, pcm: bytes, start: Optional[float]) -> tuple[str, list[Word]]:
        import numpy as np
        start = start or 0.0
        audio = np.frombuffer(pcm, dtype="<i2").astype("float32") / 32768.0
        segments, _info = model.transcribe(audio, language=self._lang, word_timestamps=True,
                                           vad_filter=False, condition_on_previous_text=False)
        pieces: list[str] = []
        words: list[Word] = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                pieces.append(text)
            for w in (seg.words or []):
                token = (w.word or "").strip()
                if token:
                    words.append(Word(token, start + w.start, start + w.end, None))
        return " ".join(pieces).strip(), words

    def _log(self, msg: dict, t_rel: Optional[float]) -> None:
        assert self._raw_log is not None
        self._raw_log.write(json.dumps({"t_recv": time.time(), "t_rel": t_rel, "msg": msg},
                                       ensure_ascii=False) + "\n")
        self._raw_log.flush()

    async def events(self) -> AsyncIterator[STTEvent]:
        while True:
            ev = await self._out.get()
            if ev is None:
                return
            yield ev

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._in_speech and self._seg and not self._segment_closed:
            await self._finish_segment(endpointed=True)
        await self._out.put(None)
