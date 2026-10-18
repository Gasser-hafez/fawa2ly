"""
The live engine: audio in, questions and lines out. One wiring, every front end.

`live_transcribe.py` is now a thin console front end over this, and `fawa2ly.server` (M3)
serves the same engine to the desktop UI over a WebSocket. That is the point of the module:
the receiver was duplicated once before, and the copy inside the live script quietly stopped
feeding interim results to the pipeline, so the early-fire path only ever ran in replay
(2026-09-08). A second front end is exactly the situation that produced
that bug, so the second front end does not get its own wiring.

Everything a front end needs arrives through one callback as a plain dict with a "type":

    status      the engine came up, paused or went down; devices, provider, log paths. `stopped` is
                the last one of every call and carries `error` when the call ended for a reason the
                user did not give: a speech stream died mid-call (sent here, after the summary), or
                the call never started (sent by the server from what run() raised).
    log         a line of console text, already formatted (keeps the CLI byte-identical)
    interim     partial transcript for a live caption, replaced as it grows
    piece       one completed sentence from the pipeline, with its speaker
    question    a question was detected and the model has been asked; carries the number that
                the `line` and `assist` events for it will use (Utterance.n counts something
                else: every utterance, question or not)
    line        one line to say, the moment the model finishes writing it (before the rest)
    style       on the Dynamic profile (roadmap item 27), the shape the model chose for one
                question - full, keywords or direction - sent before that answer's first line
    utterance   a fired Utterance (a question, or a closed segment), as to_json()
    assist      the lines to say for one question, as Assist.to_json()
    pace        the user's speaking pace and filler words so far, after each of their final
                results; only when their microphone is transcribed (roadmap item 8)
    feedback    a verdict on one of the user's answers (on_point, missed_point, ran_long, drifted)
                and a short note, when live feedback is on (roadmap item 9)
    summary     once, when the call ends: questions heard, lines shown, time to the first line,
                and where the transcript went if one was kept (roadmap item 6)

Audio never waits for the model: suggestions run as background tasks and are awaited only on
shutdown, so a slow provider cannot delay the transcript.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Callable, Optional

from websockets.exceptions import InvalidStatus

from .assist import Suggester
from .audio import to_16k_mono
from .console import CLEAR, safe_console
from .context import ContextBuilder, Preset, load_cv
from .llm import FakeLLM, OpenAICompatibleLLM, mute_asyncgen_close_noise
from .pipeline import Pipeline
from .session import consume
from .feedback import Coach
from .pace import PaceMeter
from .summary import CallRecord, save_transcript
from .stt import DeepgramSTT, LocalWhisperSTT, whisper_available
from .stt.fake import FakeSTT
from .usage import MeteredProvider

CHUNK_MS = 50          # send audio every 50 ms
SUGGEST_DRAIN_S = 10   # how long shutdown waits for in-flight suggestions

@dataclass
class EngineConfig:
    lang: str = "multi"
    log_path: str = "session.jsonl"
    raw_path: Optional[str] = None      # default: <log stem>.raw.jsonl
    mic: bool = False                   # transcribe the microphone instead of system audio
    user_mic: bool = False              # add the microphone as a second "user" stream
    speaker_device: str = ""            # speakers to listen to, by name; "" = the Windows default
    mic_device: str = ""                # microphone, by name; "" = the Windows default
    seconds: float = 0                  # stop by itself after N seconds (0 = until stopped)
    suggest: bool = False
    vendor: str = "gemini"
    llm_model: Optional[str] = None
    stt_vendor: str = "deepgram"        # deepgram | whisper (roadmap item 26: whisper needs no key,
                                         # no network, and has no interim/early-fire path, ADR-5)
    whisper_model: Optional[str] = None  # faster-whisper size (tiny|base|small|medium|large-v3, ...)
    preset: Optional[str] = None
    cv: Optional[str] = None            # path to a background document
    cv_text: str = ""                   # or its text, when the UI already read the file
    documents: Optional[list] = None    # [(name, text)] stored for this kind of call (roadmap item 5)
    fake_llm: bool = False
    fake_delay: float = 0.0             # seconds before the fake model's first token (UI work)
    speculate: bool = True              # ask the model before the turn closes (more requests)
    style: str = "full"                 # answer profile: full | keywords | direction
    mode: Optional[str] = None          # interview | meeting; None: the preset's, else interview
    replay: Optional[str] = None        # a <session>.raw.jsonl to replay instead of live audio
    transcript_dir: Optional[str] = None    # roadmap item 6: keep the call's transcript here; None = not kept
    feedback: bool = False              # roadmap item 9: a verdict on each answer (needs suggest and user_mic)
    negotiation: bool = False           # roadmap item 25: a pay or price talk; adds the negotiation rule

    def raw(self) -> str:
        return self.raw_path or side_path(self.log_path, ".raw.jsonl")

    def check(self) -> None:
        if self.mic and self.user_mic:
            raise ValueError("mic already uses the microphone as the only stream; drop user_mic")
        if self.stt_vendor not in ("deepgram", "whisper"):
            raise ValueError(f"unknown stt_vendor {self.stt_vendor!r}; known: deepgram, whisper")


def stt_params(tag: str, lang: str) -> dict:
    """Deepgram options for one stream. The user's own microphone keeps its filler words on an
    English call (roadmap item 8): Deepgram removes uh and um unless asked, and offers filler words
    in English only. The other side's stream is left as it was, so question detection reads the
    same text as before."""
    return {"filler_words": "true"} if tag == "user" and (lang or "").startswith("en") else {}


def side_path(log_path: str, suffix: str) -> str:
    """The file kept beside a call's log. A call that is not logged (log_path is os.devnull) keeps
    nothing beside it either, rather than a file named after the null device."""
    return os.devnull if log_path == os.devnull else os.path.splitext(log_path)[0] + suffix


def raw_path_for(raw_path: str, tag: str) -> str:
    """other -> <x>.raw.jsonl (as given); user -> <x>.user.raw.jsonl"""
    if tag == "other" or raw_path == os.devnull:
        return raw_path
    stem = raw_path[: -len(".raw.jsonl")] if raw_path.endswith(".raw.jsonl") else os.path.splitext(raw_path)[0]
    return f"{stem}.{tag}.raw.jsonl"


def list_devices(pa=None) -> dict:
    """What can be listened to, by name (M3 step 5, for Settings): the WASAPI loopback of each output
    device, which is the other side of a call, and each WASAPI microphone."""
    import pyaudiowpatch as pyaudio
    own = pa is None
    pa = pa or pyaudio.PyAudio()
    try:
        speakers, mics = [], []
        for d in pa.get_device_info_generator_by_host_api(host_api_type=pyaudio.paWASAPI):
            if d["isLoopbackDevice"]:
                speakers.append(d["name"])
            elif d["maxInputChannels"] > 0:
                mics.append(d["name"])
        return {"speakers": speakers, "mics": mics}
    finally:
        if own:
            pa.terminate()


def find_input(pa, name: str = ""):
    """The microphone chosen in Settings, or the Windows default when none is chosen or the chosen
    one is gone (a headset unplugged since): a missing device must not stop the call."""
    import pyaudiowpatch as pyaudio
    if name:
        for d in pa.get_device_info_generator_by_host_api(host_api_type=pyaudio.paWASAPI):
            if d["name"] == name and not d["isLoopbackDevice"] and d["maxInputChannels"] > 0:
                return d
    return pa.get_default_input_device_info()


def pick_device(pa, use_mic: bool, speaker: str = "", mic: str = ""):
    """Return (device_info, is_loopback). Loopback = what the speakers are playing. A device chosen by
    name in Settings wins; one that is no longer there falls back to the Windows default."""
    import pyaudiowpatch as pyaudio
    if use_mic:
        return find_input(pa, mic), False
    if speaker:
        for lb in pa.get_loopback_device_info_generator():
            if lb["name"] == speaker:
                return lb, True
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
    speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not speakers.get("isLoopbackDevice"):
        for lb in pa.get_loopback_device_info_generator():
            if speakers["name"] in lb["name"]:
                return lb, True
        raise RuntimeError("No WASAPI loopback device found for: " + speakers["name"])
    return speakers, True


def start_capture(pa, dev, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    import pyaudiowpatch as pyaudio
    src_sr = int(dev["defaultSampleRate"])
    channels = int(dev["maxInputChannels"])
    frames = int(src_sr * CHUNK_MS / 1000)

    def callback(in_data, frame_count, time_info, status):
        if not loop.is_closed():
            # the callback fires once the chunk is complete, so its first sample is CHUNK_MS old
            t_capture = time.time() - CHUNK_MS / 1000
            loop.call_soon_threadsafe(queue.put_nowait, (t_capture, to_16k_mono(in_data, channels, src_sr)))
        return (None, pyaudio.paContinue)

    return pa.open(
        format=pyaudio.paInt16, channels=channels, rate=src_sr, input=True,
        input_device_index=dev["index"], frames_per_buffer=frames, stream_callback=callback,
    )


class _Pausable:
    """A replay source that honours pause(), the way live audio does.

    Live, pausing stops the audio pump, so nothing said while paused is ever transcribed. A
    replay had no pump to stop, so the panel said "paused, not listening" while questions kept
    appearing behind it - the UI telling the truth about a flag and a lie about the session.
    Events that arrive while paused are dropped rather than queued, which is what dropping the
    audio would have done.
    """

    def __init__(self, stt, engine: "Engine"):
        self._stt = stt
        self._engine = engine

    async def events(self):
        async for ev in self._stt.events():
            if self._engine.paused:
                continue
            yield ev


class Engine:
    """One live session. Build it, await run(); pause() and stop() are safe from any task."""

    def __init__(self, cfg: EngineConfig, on_event: Callable[[dict], None]):
        cfg.check()
        self.cfg = cfg
        self.on_event = on_event
        self.paused = False
        self._stop = asyncio.Event()
        self._pending: list = []
        self.provider = None
        self.suggester: Optional[Suggester] = None
        self._retired: list = []   # providers switched away from mid-call, closed when the call ends
        self.cv_words: Optional[int] = None   # background loaded for this call; None when not suggesting
        self.record = CallRecord()            # what the call looked like, for its summary (item 6)
        self.pace = PaceMeter(count_fillers=(cfg.lang or "").startswith("en"))   # the user's pace (item 8)
        self.coach: Optional[Coach] = None    # live feedback on the user's answers (item 9)

    # ---- control ----
    def pause(self) -> None:
        """Stop sending audio upstream. The socket stays open, so resuming is instant."""
        self.paused = True
        self.emit({"type": "status", "state": "paused"})

    def resume(self) -> None:
        self.paused = False
        self.emit({"type": "status", "state": "listening"})

    def retry(self, n: int) -> None:
        """Ask question `n` again. Ignored when nothing is suggesting or the number is unknown."""
        if self.suggester is not None:
            self._spawn(self.suggester.retry(n))

    def more(self, n: int) -> None:
        """Two or three more lines for question `n` (the overlay's More button)."""
        if self.suggester is not None:
            self._spawn(self.suggester.more(n))

    def action(self, kind: str, n: Optional[int] = None) -> None:
        """An on-demand button (roadmap item 17): say, recap, ask, or shorter for question `n`."""
        if self.suggester is not None:
            self._spawn(self.suggester.action(kind, n))

    def set_style(self, style: str) -> None:
        """Switch the answer profile for the questions after this one, and say so.

        The status carries it because the overlay draws by it: direction lines show as arrows and
        Dynamic names each card's shape (roadmap item 27), and until 2026-09-13 nothing told a
        running call's windows that the profile had changed, so the answers after a mid-call switch
        were drawn in the shape of the one before it until the next status happened to arrive."""
        if self.suggester is None:
            raise RuntimeError("this session is not suggesting")
        self.suggester.builder.set_style(style)          # raises before anything changes
        self.cfg.style = style
        self.emit({"type": "status", "state": "paused" if self.paused else "listening",
                   "style": style})

    @property
    def mode(self) -> Optional[str]:
        """The running call's kind: the builder's once suggesting, else what was configured."""
        return self.suggester.builder.mode if self.suggester is not None else self.cfg.mode

    def set_documents(self, documents) -> None:
        """Use newly added (or removed) documents from the next question, without ending the call.
        rehearsal7 is the case: the CV was missing, and the moment to notice is mid-interview."""
        if self.suggester is None:
            return
        self.suggester.builder.set_documents(documents)
        self.cv_words = len(self.suggester.builder.cv_text.split())
        self.emit({"type": "status", "state": "paused" if self.paused else "listening",
                   "cv_words": self.cv_words})

    def set_background(self, text: str) -> None:
        """One unnamed document, as callers from before documents had names pass it."""
        self.set_documents([("", text)] if text else [])

    def stop(self) -> None:
        self._stop.set()

    async def set_provider(self, vendor: str, model: Optional[str] = None) -> str:
        """Send the next question to a different model, without ending the call.

        rehearsal6 is why this exists: the pipeline closed turns in 1.8 s at p50 while the free
        Gemini tier took 43 s to a first token, alternating with 1 s answers as its 5 requests a
        minute ran out. When the provider is the problem, the fix has to be available in the
        middle of the interview. An answer already streaming finishes on the old model, which is
        closed only when the call ends.
        """
        if self.suggester is None:
            raise RuntimeError("this session is not suggesting")
        new = MeteredProvider(OpenAICompatibleLLM.from_env(vendor, model))
        self._retired.append(self.suggester.provider)
        self.suggester.provider = self.provider = new
        self.cfg.vendor, self.cfg.llm_model = vendor, model
        label = f"{new.name}/{new.model}"
        self.emit({"type": "status", "state": "paused" if self.paused else "listening",
                   "provider": label})
        return label

    async def _close_providers(self) -> None:
        for p in [self.provider, *self._retired]:
            if p is not None:
                await p.close()
        self._retired = []

    def emit(self, event: dict) -> None:
        self.record.observe(event)
        if self.coach is not None:
            self.coach.observe(event)
        self.on_event(event)

    def _log(self, text: str = "", end: str = "\n", flush: bool = False) -> None:
        self.emit({"type": "log", "text": text, "end": end})

    # ---- teardown: what is always let go, and why a call ended ----
    def _quiet(self, release, *args) -> None:
        """Run one release step so that its failing cannot stop the steps after it: a device that
        has gone must not leave the session files open."""
        try:
            release(*args)
        except Exception as e:
            self._log(f"[cleanup] {getattr(release, '__name__', 'a step')} failed: {e}")

    @staticmethod
    def _stop_stream(stream) -> None:
        try:
            stream.stop_stream()
        finally:
            stream.close()

    async def _drain(self) -> None:
        """Let the last suggestions land before the files they write to close, then close the
        models. One still running after SUGGEST_DRAIN_S is cancelled: left alone it would later
        write into a log that is closed by then."""
        try:
            if self._pending:
                _, late = await asyncio.wait(self._pending, timeout=SUGGEST_DRAIN_S)
                for task in late:
                    task.cancel()
            if self.provider is not None:
                await self._close_providers()
        except Exception as e:
            self._log(f"[cleanup] closing the model connections failed: {e}")

    def _end(self, error: str = "") -> None:
        """The last thing a call that got as far as listening does: its summary, then `stopped`,
        carrying the reason when the call did not end because the user (or `seconds`) said so."""
        self._finish()
        event = {"type": "status", "state": "stopped"}
        if error:
            event["error"] = error
        self.emit(event)

    @staticmethod
    def _why_ended(task: asyncio.Task, tag: str) -> str:
        """For a window: which side's speech stream stopped on its own, and what it said."""
        who = "your microphone" if tag == "user" else "the call"
        exc = None if task.cancelled() else task.exception()
        detail = (str(exc) or type(exc).__name__) if exc is not None else "the connection was closed"
        return f"Speech recognition stopped ({who}): {detail}"

    async def _wait_until_over(self, streams: dict) -> str:
        """Wait for the user to end the call, for `seconds` to run out, or for one of the stream
        tasks (task -> "other" | "user") to finish by itself. Returns "" for the first two and the
        reason for the third, which used to look exactly like the first: a dropped speech
        connection ended the call with a clean "Call ended" and nothing said (audit F3)."""
        stopper = asyncio.create_task(self._stop.wait())
        try:
            done, _ = await asyncio.wait([*streams, stopper], timeout=self.cfg.seconds or None,
                                         return_when=asyncio.FIRST_COMPLETED)
        except (asyncio.CancelledError, KeyboardInterrupt):
            return ""
        finally:
            stopper.cancel()
        if stopper in done or not done:
            return ""
        # the one that raised first; sorting reads every exception, so none is left unretrieved
        died = sorted(done, key=lambda t: t.cancelled() or t.exception() is None)[0]
        return self._why_ended(died, streams[died])

    def _role_title(self) -> str:
        if not self.cfg.preset:
            return ""
        try:
            from .context.builder import preset_info
            return preset_info().get(self.cfg.preset, {}).get("title", self.cfg.preset)
        except Exception:
            return self.cfg.preset

    def _finish(self) -> None:
        """The call's summary, sent just before `stopped`, and its transcript when the user keeps
        them (roadmap item 6). Called while the call shuts down, so it never raises."""
        try:
            summary = self.record.summary(role=self._role_title(), lang=self.cfg.lang)
            summary["transcript"] = None
            if self.cfg.transcript_dir:
                try:
                    summary["transcript"] = save_transcript(dict(summary), self.record.turns,
                                                            self.cfg.transcript_dir)
                except OSError as e:
                    summary["transcript_error"] = str(e)
            self.emit({"type": "summary", "summary": summary})
        except Exception as e:
            self._log(f"[summary] could not be built: {e}")

    # ---- construction ----
    def _build_suggester(self, assist_log) -> Suggester:
        provider = (FakeLLM(first_token_delay=self.cfg.fake_delay or 0.05,
                            token_delay=0.35 if self.cfg.fake_delay else 0.002)
                    if self.cfg.fake_llm
                    else MeteredProvider(OpenAICompatibleLLM.from_env(self.cfg.vendor, self.cfg.llm_model)))
        self.provider = provider          # held at once, so a failure below (a missing role) still closes it
        cv_text = self.cfg.cv_text or (load_cv(self.cfg.cv) if self.cfg.cv else "")
        builder = ContextBuilder(preset=Preset.find(self.cfg.preset) if self.cfg.preset else None,
                                 cv_text=cv_text, style=self.cfg.style, mode=self.cfg.mode,
                                 documents=self.cfg.documents, lang=self.cfg.lang)
        # rehearsal7 ran with no background at all and nothing on screen said so: every status from
        # here carries how much background the model actually has, so the overlay can
        self.cv_words = len(builder.cv_text.split())
        builder.negotiation = self.cfg.negotiation     # roadmap item 25, off unless turned on
        return Suggester(provider, builder, log=assist_log,
                         on_result=lambda a: self.emit({"type": "assist", "assist": a.to_json()}),
                         on_line=lambda n, text: self.emit({"type": "line", "n": n, "text": text}),
                         on_question=lambda n, text: self.emit({"type": "question", "n": n, "text": text,
                                                                # the action that opened it, "" for a
                                                                # question that was actually heard
                                                                "action": self.suggester.action_cards.get(n, "")}),
                         on_style=lambda n, style: self.emit({"type": "style", "n": n, "style": style}))

    def _coach_if_asked(self) -> Optional[Coach]:
        """Live feedback (item 9) needs the user's words and a model: only with feedback turned on,
        the microphone transcribed and the suggester built. It asks the model the answers use, and
        waits while any of the call's other requests is still running."""
        if not (self.cfg.feedback and self.cfg.user_mic and self.suggester is not None):
            return None
        coach = Coach(provider=lambda: self.suggester.provider, spawn=self._spawn, emit=self.emit,
                      busy=lambda: any(not t.done() for t in self._pending if t not in coach.tasks),
                      mode=self.suggester.builder.mode, lang=self.cfg.lang)
        return coach

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._pending.append(t)
        t.add_done_callback(lambda done: self._pending.remove(done) if done in self._pending else None)
        return t

    async def _pump(self, queue: asyncio.Queue, stt: DeepgramSTT) -> None:
        while True:
            t_capture, chunk = await queue.get()
            if self.paused:
                continue          # dropped, not buffered: stale audio is worse than a gap
            await stt.send_audio(chunk, t_capture=t_capture)

    def _pipeline_for(self, tag: str) -> Pipeline:
        def on_piece(spk, piece):
            self.emit({"type": "piece", "stream": tag, "speaker": spk, "text": piece})
            who = f"spk{spk}" if tag == "other" else "you"
            self._log(f"{CLEAR}   . {who}: {piece}")

        if tag == "other":
            # whole turns closed by a second of silence, asked early and shown at the close
            #; reopening stays off until a recording shows it helping
            return Pipeline(stream=tag, on_piece=on_piece, turns=True, speculate=self.cfg.speculate)
        # your own words are context, never questions to answer
        return Pipeline(stream=tag, classify=lambda _s: None, interim_detection=False,
                        on_piece=on_piece)

    async def _paced(self, events):
        """The user's microphone, measured on its way to the pipeline (roadmap item 8)."""
        async for ev in events:
            if ev.kind == "final" and ev.words and self.pace.feed(ev.words):
                self.emit({"type": "pace", **self.pace.snapshot()})
            yield ev

    def _consumer(self, stt, tag: str, log):
        on_interim = None
        events = stt.events()
        if tag == "other":
            on_interim = lambda text: self.emit({"type": "interim", "text": text})  # noqa: E731
        else:
            events = self._paced(events)
        # Both sides go to the suggester: the user's own words are history it must see ("you:" lines
        # in the prompt, the recap), and it never answers them - only the other stream can be a
        # question (assist.is_question, audit F17).
        return consume(events, self._pipeline_for(tag), log, tag, show=self._log,
                       suggester=self.suggester, spawn=self._spawn,
                       on_utterance=lambda u: self.emit({"type": "utterance", "utterance": u.to_json()}),
                       on_interim=on_interim)

    async def _open_side_files(self, stack: AsyncExitStack):
        """(utterance log, assist log). The suggester is built here too when asked for.

        Each thing is registered with `stack` the moment it exists, so a step after it failing (a
        model with no key, a role that is not there) does not leave the files open. `_drain` is
        registered after the files, so it runs before they close."""
        log = open(self.cfg.log_path, "a", encoding="utf-8")
        stack.callback(self._quiet, log.close)
        assist_log = None
        if self.cfg.suggest:
            assist_log = open(side_path(self.cfg.log_path, ".assist.jsonl"),
                              "a", encoding="utf-8")
            stack.callback(self._quiet, assist_log.close)
        stack.push_async_callback(self._drain)
        if self.cfg.suggest:
            self.suggester = self._build_suggester(assist_log)
            self.coach = self._coach_if_asked()
        return log, assist_log

    async def run_replay(self) -> None:
        """A recorded session, at its original pace, through the whole live path.

        No audio device and no Deepgram key, so a front end can be built and demonstrated
        against real speech without playing anything into the machine. It is the same
        `consume` loop and the same suggester as a live call; only the source of events
        differs, which is the property the console front end lost once before (ADR-5).
        """
        safe_console()
        mute_asyncgen_close_noise()
        listening = False
        try:
            async with AsyncExitStack() as stack:
                log, _ = await self._open_side_files(stack)
                self.emit({"type": "status", "state": "listening", "log": self.cfg.log_path,
                           "provider": None if self.provider is None
                           else f"{self.provider.name}/{self.provider.model}",
                           "replay": self.cfg.replay, "cv_words": self.cv_words, "style": self.cfg.style,
                           "mode": self.suggester.builder.mode if self.suggester is not None else self.cfg.mode})
                listening = True
                task = asyncio.create_task(self._consumer(
                    _Pausable(FakeSTT(self.cfg.replay, realtime=True), self), "other", log))
                stack.callback(task.cancel)
                stopper = asyncio.create_task(self._stop.wait())
                stack.callback(stopper.cancel)
                # `seconds` has to mean the same here as it does live, or a smoke test that passes
                # against a device hangs forever against a recording (found the hard way, 2026-09-10)
                await asyncio.wait([task, stopper], timeout=self.cfg.seconds or None,
                                   return_when=asyncio.FIRST_COMPLETED)
        finally:
            if listening:                    # a call that never got as far as listening has no summary
                self._end()

    async def run(self) -> None:
        if self.cfg.replay:
            await self.run_replay()
            return
        import pyaudiowpatch as pyaudio
        cfg = self.cfg
        local_stt = cfg.stt_vendor == "whisper"
        key = None
        if not local_stt:
            key = os.environ.get("DEEPGRAM_API_KEY")
            if not key:
                raise RuntimeError("No Deepgram key: add one in Settings, under API keys, then start again.")
        elif not whisper_available():
            raise RuntimeError("Offline transcription needs faster-whisper and webrtcvad-wheels: "
                               "pip install -r requirements.txt, then start again.")
        safe_console()
        mute_asyncgen_close_noise()

        listening, ended = False, ""
        try:
            # Everything the call holds is registered with the stack the moment it exists, and the
            # stack lets go in the opposite order: when the call ends, when a speech stream dies, and
            # when setup fails halfway (a second microphone that will not open, a rejected key). The
            # try used to begin after PortAudio, the files and the first connection were all open,
            # so a failed start leaked them, and again on every retry (audit F2).
            async with AsyncExitStack() as stack:
                pa = pyaudio.PyAudio()
                stack.callback(self._quiet, pa.terminate)
                dev, is_loopback = pick_device(pa, cfg.mic, cfg.speaker_device, cfg.mic_device)
                devices = [("other", dev, "LOOPBACK" if is_loopback else "MIC")]
                if cfg.user_mic:
                    devices.append(("user", find_input(pa, cfg.mic_device), "MIC"))
                for tag, d, kind in devices:
                    self.emit({"type": "status", "state": "device", "stream": tag, "kind": kind,
                               "name": d["name"], "rate": int(d["defaultSampleRate"]),
                               "channels": int(d["maxInputChannels"])})
                    self._log(f"[audio] {tag:<5} {kind}: {d['name']} | "
                              f"{int(d['defaultSampleRate'])} Hz x {int(d['maxInputChannels'])}ch")

                loop = asyncio.get_running_loop()
                log, assist_log = await self._open_side_files(stack)
                streams: dict = {}        # the pump and consumer tasks -> the side they listen to
                for tag, d, _ in devices:
                    raw = open(raw_path_for(cfg.raw(), tag), "a", encoding="utf-8")
                    stack.callback(self._quiet, raw.close)
                    try:
                        if local_stt:
                            stt = await stack.enter_async_context(
                                LocalWhisperSTT(cfg.lang, raw_log=raw, model_size=cfg.whisper_model))
                        else:
                            stt = await stack.enter_async_context(
                                DeepgramSTT(key, cfg.lang, raw_log=raw, **stt_params(tag, cfg.lang)))
                    except InvalidStatus as e:     # the socket's own message does not say it is the key
                        code = e.response.status_code
                        raise RuntimeError(
                            f"Deepgram refused the connection (HTTP {code})"
                            + (": check the key under Settings > API keys." if code in (401, 403) else ".")) from e
                    # open the audio stream only AFTER the socket is up, so no audio backlog piles up
                    # (a backlog makes Deepgram's audio clock run ahead of our wall clock -> wrong latency)
                    queue: asyncio.Queue = asyncio.Queue()
                    stack.callback(self._quiet, self._stop_stream, start_capture(pa, d, loop, queue))
                    for coro in (self._pump(queue, stt), self._consumer(stt, tag, log)):
                        task = asyncio.create_task(coro)
                        stack.callback(task.cancel)
                        streams[task] = tag

                self._log(f"[log] utterances -> {cfg.log_path} | raw -> "
                          + ", ".join(raw_path_for(cfg.raw(), t) for t, _, _ in devices))
                if self.suggester is not None:
                    self._log(f"[assist] {self.provider.name}/{self.provider.model} -> {assist_log.name}")
                self.emit({"type": "status", "state": "listening", "log": cfg.log_path,
                           "provider": None if self.provider is None
                           else f"{self.provider.name}/{self.provider.model}",
                           "cv_words": self.cv_words, "style": self.cfg.style,
                           "mode": self.suggester.builder.mode if self.suggester is not None else self.cfg.mode})
                listening = True
                self._log("[stt] connected - talk / play something. Ctrl+C to stop."
                          + (f" Auto-stop in {cfg.seconds:g} s." if cfg.seconds else "") + "\n")
                ended = await self._wait_until_over(streams)
        finally:
            if listening:                    # a call that never got as far as listening has no summary
                if ended:
                    self._log(f"[stt] {ended}")
                self._end(ended)
                self._log("\n[stopped]")
