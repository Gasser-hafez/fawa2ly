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

