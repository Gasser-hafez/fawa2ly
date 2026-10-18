"""The engine's contract with a front end (M3 step 1).

None of this needs an audio device or a network: the parts that touch hardware are the two
functions that import pyaudiowpatch, and everything a UI actually consumes is the event stream
built on top of `fawa2ly.session.consume`.
"""

import asyncio
import io
import json
import pathlib

import pytest
import yaml

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder
from fawa2ly.engine import Engine, EngineConfig, raw_path_for
from fawa2ly.llm import FakeLLM
from fawa2ly.stt.base import STTEvent, Word

QUESTION = [("What", 10.0, 10.2, 0), ("hours", 10.2, 10.5, 0), ("can", 10.5, 10.6, 0),
            ("you", 10.6, 10.7, 0), ("work?", 10.7, 11.0, 0)]


def ev(kind, t_recv, t_rel, spec):
    words = [Word(t, s, e, spk) for t, s, e, spk in spec]
    return STTEvent(kind, t_recv, t_rel, " ".join(w.text for w in words), words)


class FakeStream:
    """Stands in for DeepgramSTT: replays events, records what was sent to it."""

    def __init__(self, events=()):
        self._events = list(events)
        self.sent = []

    async def events(self):
        for e in self._events:
            yield e

    async def send_audio(self, chunk, t_capture=None):
        self.sent.append(chunk)


def engine(**kw):
    events = []
    e = Engine(EngineConfig(**kw), on_event=events.append)
    return e, events


def test_a_question_reaches_a_front_end_as_interim_piece_utterance_and_assist():
    e, events = engine()
    e.suggester = Suggester(FakeLLM(bullets=["I worked nights for a year and it stopped suiting me"]),
                            ContextBuilder())
    e.suggester.on_result = lambda a: e.emit({"type": "assist", "assist": a.to_json()})
    stream = FakeStream([ev("interim", 100.0, 11.4, QUESTION),
                         ev("final", 103.0, 14.0, QUESTION),
                         STTEvent("utterance_end", 104.0, 15.0)])

    async def go():
        await e._consumer(stream, "other", io.StringIO())
        await asyncio.wait(e._pending, timeout=2)

    asyncio.run(go())
    kinds = [x["type"] for x in events]
    assert "interim" in kinds and "utterance" in kinds and "assist" in kinds
    interim = next(x for x in events if x["type"] == "interim")
    assert interim["text"] == "What hours can you work?"
    utterance = next(x for x in events if x["type"] == "utterance")
    assert utterance["utterance"]["text"] == "What hours can you work?"
    assist = next(x for x in events if x["type"] == "assist")
    assert assist["assist"]["bullets"] == ["I worked nights for a year and it stopped suiting me"]


def test_the_user_stream_is_never_judged_as_a_question():
    """Your own microphone is history for the model, never a question to answer."""
    e, events = engine()
    stream = FakeStream([ev("final", 103.0, 14.0, QUESTION), STTEvent("utterance_end", 104.0, 15.0)])
    asyncio.run(e._consumer(stream, "user", io.StringIO()))
    assert [x["type"] for x in events].count("interim") == 0
    fired = [x for x in events if x["type"] == "utterance"]
    assert fired and all(u["utterance"]["trigger"] != "question_mark" for u in fired)


def test_pause_drops_audio_instead_of_buffering_it():
    """Resuming must not replay a backlog: stale audio is worse than a gap, and a backlog makes
    Deepgram's audio clock run ahead of the wall clock, which is how latency was measured wrong
    once already (2026-09-08)."""
    e, events = engine()
    stream = FakeStream()

    async def go():
        queue: asyncio.Queue = asyncio.Queue()
        pump = asyncio.create_task(e._pump(queue, stream))
        e.pause()
        queue.put_nowait((1.0, b"paused"))
        await asyncio.sleep(0.05)
        e.resume()
        queue.put_nowait((2.0, b"live"))
        await asyncio.sleep(0.05)
        pump.cancel()

    asyncio.run(go())
    assert stream.sent == [b"live"]
    assert [x.get("state") for x in events if x["type"] == "status"] == ["paused", "listening"]


def test_config_paths_and_the_one_impossible_combination():
    cfg = EngineConfig(log_path="interview9.jsonl")
    assert cfg.raw() == "interview9.raw.jsonl"
    assert raw_path_for(cfg.raw(), "user") == "interview9.user.raw.jsonl"
    assert EngineConfig(log_path="a.jsonl", raw_path="b.raw.jsonl").raw() == "b.raw.jsonl"
    with pytest.raises(ValueError):
        Engine(EngineConfig(mic=True, user_mic=True), on_event=lambda e: None)


def test_the_console_front_end_owns_no_stt_wiring():
    """The 2026-09-08 bug in one assertion.

    The live receiver was a second copy of the session loop, and the copy stopped feeding
    interim results to the pipeline, so early firing only ever worked in replay. A front end
    that names the STT provider or the session loop is that copy coming back.
    """
    src = pathlib.Path("live_transcribe.py").read_text(encoding="utf-8")
    for forbidden in ("DeepgramSTT", "consume(", "Pipeline(", "start_capture"):
        assert forbidden not in src, f"live_transcribe.py wires {forbidden} itself again"


def test_a_recorded_session_drives_the_whole_live_path_with_no_audio_and_no_key(tmp_path):
    """`--replay` is how the UI is built and demonstrated: no device, no Deepgram key, no spend.

    It has to be the same path as a live call or it is worth nothing, so this asserts the events
    a front end actually draws - question, line, assist - come out of a recorded file.
    """
    from fawa2ly.synth import Script, generate, write

    script = tmp_path / "s.yaml"
    script.write_text(yaml.safe_dump({
        "name": "t",
        "turns": [{"who": "interviewer", "text": "What hours can you work?"},
                  {"who": "candidate", "text": "Nights, mostly.", "pause": 0.2}],
    }), encoding="utf-8")
    raw = tmp_path / "s.raw.jsonl"
    write(generate(Script.load(str(script))), str(raw))

    events = []
    cfg = EngineConfig(replay=str(raw), suggest=True, fake_llm=True,
                       log_path=str(tmp_path / "out.jsonl"))
    e = Engine(cfg, on_event=events.append)
    asyncio.run(asyncio.wait_for(e.run(), timeout=30))

    kinds = [x["type"] for x in events]
    assert kinds[0] == "status" and events[0]["state"] == "listening"
    assert kinds[-1] == "status" and events[-1]["state"] == "stopped"
    for needed in ("question", "line", "assist", "utterance"):
        assert needed in kinds, f"a replay produced no {needed} event"
    question = next(x for x in events if x["type"] == "question")
    assert question["text"] == "What hours can you work?"
    assert question["n"] == next(x for x in events if x["type"] == "line")["n"]
    rows = [json.loads(l) for l in (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows, "a replay session must still write its utterance log"


def test_pause_stops_a_replay_too_not_only_live_audio(tmp_path):
    """Pausing has to mean the same thing in every mode, or the panel lies.

    Live, pause stops the audio pump. A replay had no pump, so the window said "paused, not
    listening" while questions kept arriving behind it.
    """
    from fawa2ly.synth import Script, generate, write

    script = tmp_path / "s.yaml"
    script.write_text(yaml.safe_dump({
        "name": "t",
        "turns": [{"who": "interviewer", "text": "What hours can you work?"},
                  {"who": "candidate", "text": "Nights, mostly.", "pause": 0.2},
                  {"who": "interviewer", "text": "And why did you leave your last job?"}],
    }), encoding="utf-8")
    raw = tmp_path / "s.raw.jsonl"
    write(generate(Script.load(str(script))), str(raw))

    events = []
    e = Engine(EngineConfig(replay=str(raw), log_path=str(tmp_path / "out.jsonl")),
               on_event=events.append)

    async def go():
        run = asyncio.create_task(e.run())
        e.pause()
        await asyncio.wait_for(run, timeout=30)

    asyncio.run(go())
    assert not [x for x in events if x["type"] in ("interim", "piece", "utterance")],         "a paused replay must transcribe nothing"


def test_seconds_stops_a_replay_as_it_stops_a_live_session(tmp_path):
    """--seconds is how every smoke test bounds a run. It only gated the live path, so the same
    command that finished against a microphone ran a 14-minute recording to the end."""
    from fawa2ly.synth import Script, generate, write

    script = tmp_path / "s.yaml"
    script.write_text(yaml.safe_dump({
        "name": "t",
        "turns": [{"who": "interviewer", "text": "What hours can you work?", "pause": 4.0},
                  {"who": "interviewer", "text": "And why did you leave your last job?", "pause": 4.0},
                  {"who": "interviewer", "text": "Where do you see yourself in five years?"}],
    }), encoding="utf-8")
    raw = tmp_path / "s.raw.jsonl"
    write(generate(Script.load(str(script))), str(raw))

    e = Engine(EngineConfig(replay=str(raw), seconds=1.0, log_path=str(tmp_path / "out.jsonl")),
               on_event=lambda _e: None)
    asyncio.run(asyncio.wait_for(e.run(), timeout=20))



# ---- the live path's setup, teardown and failures (audit F2, F3) ----

import sys  # noqa: E402
import types  # noqa: E402

from fawa2ly import engine as engine_module  # noqa: E402


class FakeCapture:
    def __init__(self):
        self.stopped = self.closed = False

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


def fake_pyaudio(monkeypatch, fail_open_number=0):
    """pyaudiowpatch with a loopback speaker and a microphone. The Nth open() raises, like a
    microphone in exclusive mode; every stream and the PyAudio itself record being let go."""
    speaker = {"name": "Speakers", "isLoopbackDevice": True, "defaultSampleRate": 48000,
               "maxInputChannels": 2, "index": 0}
    mic = {"name": "Mic", "isLoopbackDevice": False, "defaultSampleRate": 44100,
           "maxInputChannels": 1, "index": 1}
    made = types.SimpleNamespace(streams=[], terminated=False)

    class PyAudio:
        def get_host_api_info_by_type(self, _t):
            return {"defaultOutputDevice": 0}

        def get_device_info_by_index(self, _i):
            return speaker

        def get_device_info_generator_by_host_api(self, host_api_type=None):
            yield mic

        def get_default_input_device_info(self):
            return mic

        def open(self, **_kw):
            if len(made.streams) + 1 == fail_open_number:
                raise OSError("exclusive mode")
            made.streams.append(FakeCapture())
            return made.streams[-1]

        def terminate(self):
            made.terminated = True

    module = types.ModuleType("pyaudiowpatch")
    module.PyAudio, module.paInt16, module.paWASAPI, module.paContinue = PyAudio, 8, 13, 0
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    return made


class FakeDeepgram:
    """Stands in for DeepgramSTT. `die_with` makes events() raise, the way a dropped socket does;
    `refuse_at` makes the Nth connection fail with HTTP 401."""
    die_with = None
    connects = 0
    refuse_at = 0

    def __init__(self, key, lang, raw_log=None, **_params):
        FakeDeepgram.connects += 1
        if FakeDeepgram.connects == FakeDeepgram.refuse_at:
            raise engine_module.InvalidStatus(types.SimpleNamespace(status_code=401))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send_audio(self, chunk, t_capture=None):
        return None

    async def events(self):
        if FakeDeepgram.die_with is not None:
            await asyncio.sleep(0.05)
            raise FakeDeepgram.die_with
        await asyncio.Event().wait()
        yield  # pragma: no cover


@pytest.fixture
def live(monkeypatch, tmp_path):
    """A live Engine's world with no hardware, no key and no network."""
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setattr(engine_module, "DeepgramSTT", FakeDeepgram)
    FakeDeepgram.die_with, FakeDeepgram.connects, FakeDeepgram.refuse_at = None, 0, 0
    opened = []
    real_open = open

    def tracking_open(*a, **kw):
        f = real_open(*a, **kw)
        opened.append(f)
        return f

    monkeypatch.setattr(engine_module, "open", tracking_open, raising=False)
    return types.SimpleNamespace(opened=opened, log=str(tmp_path / "call.jsonl"))


def test_a_setup_that_fails_halfway_lets_go_of_everything_it_opened(monkeypatch, live):
    """Audit F2: the microphone's open() fails after the speaker's stream, the files and the first
    connection exist. The first stream must be stopped, PortAudio terminated, every file closed and
    no task left running - and the failure still reaches the caller, for the server to report."""
    made = fake_pyaudio(monkeypatch, fail_open_number=2)
    events = []
    e = Engine(EngineConfig(log_path=live.log, user_mic=True), on_event=events.append)

    async def go():
        with pytest.raises(OSError, match="exclusive mode"):
            await asyncio.wait_for(e.run(), timeout=10)
        await asyncio.sleep(0.05)
        return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    leftover = asyncio.run(go())
    assert [s.stopped and s.closed for s in made.streams] == [True]
    assert made.terminated is True
    assert live.opened and all(f.closed for f in live.opened)
    assert not leftover
    assert not [x for x in events if x["type"] == "status" and x.get("state") == "stopped"], \
        "a call that never listened has no summary and no `stopped` of its own: the server sends it"


def test_a_rejected_deepgram_key_is_said_in_words(monkeypatch, live):
    made = fake_pyaudio(monkeypatch)
    FakeDeepgram.refuse_at = 1
    e = Engine(EngineConfig(log_path=live.log), on_event=lambda _e: None)
    with pytest.raises(RuntimeError, match="check the key under Settings"):
        asyncio.run(asyncio.wait_for(e.run(), timeout=10))
    assert made.terminated and all(f.closed for f in live.opened)


def test_a_speech_stream_that_dies_mid_call_ends_it_with_the_reason(monkeypatch, live):
    """Audit F3: the connection drops 20 minutes in. The window used to get a clean `stopped` and say
    'Call ended'; it must get the reason, after the summary, and everything is still let go."""
    made = fake_pyaudio(monkeypatch)
    FakeDeepgram.die_with = ConnectionResetError("received 1011 (internal error) keepalive ping timeout")
    events = []
    e = Engine(EngineConfig(log_path=live.log), on_event=events.append)
    asyncio.run(asyncio.wait_for(e.run(), timeout=10))
    ends = [x for x in events if x["type"] == "summary" or (x["type"] == "status" and x.get("state") == "stopped")]
    assert [x["type"] for x in ends] == ["summary", "status"], "the summary, then `stopped`, once"
    assert "Speech recognition stopped (the call)" in ends[-1]["error"]
    assert "keepalive ping timeout" in ends[-1]["error"]
    assert made.streams[0].closed and made.terminated and all(f.closed for f in live.opened)


def test_ending_the_call_yourself_is_not_an_error(monkeypatch, live):
    fake_pyaudio(monkeypatch)
    events = []
    e = Engine(EngineConfig(log_path=live.log), on_event=events.append)

    async def go():
        run = asyncio.create_task(e.run())
        while not any(x.get("state") == "listening" for x in events if x["type"] == "status"):
            await asyncio.sleep(0.01)
        e.stop()
        await asyncio.wait_for(run, timeout=10)

    asyncio.run(go())
    stopped = [x for x in events if x["type"] == "status" and x.get("state") == "stopped"]
    assert len(stopped) == 1 and "error" not in stopped[0]


def test_a_replay_whose_model_cannot_be_built_closes_its_log(monkeypatch, live):
    """The same unwind for a replay: a model with no key raises after the log was opened."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    e = Engine(EngineConfig(log_path=live.log, replay="x.raw.jsonl", suggest=True, vendor="gemini"),
               on_event=lambda _e: None)
    with pytest.raises(Exception):
        asyncio.run(asyncio.wait_for(e.run(), timeout=10))
    assert live.opened and all(f.closed for f in live.opened)


def test_a_suggestion_still_running_when_the_call_closes_is_cancelled_not_left_to_write_to_a_closed_log(monkeypatch):
    """After the drain's grace period the late task used to be left running, to raise on a closed
    assist log in a task nobody awaits."""
    monkeypatch.setattr(engine_module, "SUGGEST_DRAIN_S", 0.05)
    e, events = engine()

    async def go():
        late = e._spawn(asyncio.sleep(30))
        await e._drain()
        await asyncio.sleep(0)
        return late

    assert asyncio.run(go()).cancelled()
