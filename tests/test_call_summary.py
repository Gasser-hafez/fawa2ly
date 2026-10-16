"""After the call, part 1 (roadmap item 6, 2026-09-11): a summary of every call, and a transcript
kept only when the user turned saving on.

The summary is built from the events the overlay itself receives, so what it counts is what was
on screen: a failed answer that Retry fixed is answered, lines added with More belong to their
question, and an early ask the turn never used is not a question.
"""

import asyncio
import datetime
import json
import os

import yaml

from fawa2ly import settings as user_settings
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.server import config_from
from fawa2ly.summary import CallRecord, save_transcript


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_the_summary_counts_what_was_on_screen_and_times_each_first_line():
    clock = Clock()
    r = CallRecord(clock=clock)

    def at(t, event):
        clock.t = t
        r.observe(event)

    at(100, {"type": "status", "state": "listening", "provider": "gemini/flash", "mode": "interview",
             "style": "full"})
    at(101, {"type": "question", "n": 1, "text": "Tell me about yourself"})
    at(102.5, {"type": "line", "n": 1, "text": "Final-year IT student"})
    at(103, {"type": "line", "n": 1, "text": "Flutter apps on Firebase"})
    at(103.2, {"type": "assist", "assist": {"n": 1, "question": "Tell me about yourself",
                                            "bullets": ["Final-year IT student", "Flutter apps on Firebase"],
                                            "question_end_to_fire_ms": 900}})
    at(104, {"type": "utterance", "utterance": {"stream": "user", "text": "Sure, I am in my final year",
                                                "trigger": "silence"}})
    at(104.5, {"type": "utterance", "utterance": {"stream": "other", "text": "Why Flutter",
                                                  "trigger": "wh_word", "speculative": True}})
    at(110, {"type": "question", "n": 2, "text": "Why Flutter?"})
    at(140, {"type": "assist", "assist": {"n": 2, "question": "Why Flutter?", "bullets": [], "error": "timeout"}})
    at(141, {"type": "question", "n": 2, "text": "Why Flutter?"})                        # Retry
    at(142, {"type": "line", "n": 2, "text": "One codebase for both platforms"})
    at(142.1, {"type": "assist", "assist": {"n": 2, "question": "Why Flutter?",
                                            "bullets": ["One codebase for both platforms"]}})
    at(150, {"type": "line", "n": 1, "text": "An offline queue for the chat app"})      # More
    at(150.1, {"type": "assist", "assist": {"n": 1, "bullets": ["An offline queue for the chat app"],
                                            "more": True}})
    clock.t = 160
    s = r.summary(role="Mobile developer (Flutter)", lang="en")

    assert (s["duration_s"], s["heard"], s["answered"]) == (60, 2, 2)
    q1, q2 = s["questions"]
    assert q1["lines"] == ["Final-year IT student", "Flutter apps on Firebase", "An offline queue for the chat app"]
    assert q1["first_line_ms"] == 1500 + 900, "the pipeline's wait before it fired counts too"
    assert q2["first_line_ms"] == 32000 and q2["error"] is None, "the wait through the failure counts"
    assert s["median_first_line_ms"] == 17200 and s["slowest_first_line_ms"] == 32000
    assert (s["provider"], s["mode"], s["role"]) == ("gemini/flash", "interview", "Mobile developer (Flutter)")
    assert [t["who"] for t in r.turns] == ["you"], "an early ask is not something anyone said twice"


def test_a_question_that_never_got_a_line_is_heard_not_answered():
    clock = Clock()
    r = CallRecord(clock=clock)
    r.observe({"type": "status", "state": "listening"})
    r.observe({"type": "question", "n": 1, "text": "What are your salary expectations?"})
    r.observe({"type": "assist", "assist": {"n": 1, "bullets": [], "error": "rate limited"}})
    s = r.summary()
    assert (s["heard"], s["answered"], s["median_first_line_ms"]) == (1, 0, None)
    assert s["questions"][0]["error"] == "rate limited"


def replay(tmp_path, transcript_dir):
    from fawa2ly.synth import Script, generate, write

    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "s.yaml"
    script.write_text(yaml.safe_dump({
        "name": "t",
        "turns": [{"who": "interviewer", "text": "What hours can you work?"},
                  {"who": "candidate", "text": "Nights, mostly.", "pause": 0.2}],
    }), encoding="utf-8")
    raw = tmp_path / "s.raw.jsonl"
    write(generate(Script.load(str(script))), str(raw))
    events = []
    cfg = EngineConfig(replay=str(raw), suggest=True, fake_llm=True, log_path=str(tmp_path / "out.jsonl"),
                       transcript_dir=transcript_dir)
    asyncio.run(asyncio.wait_for(Engine(cfg, on_event=events.append).run(), timeout=30))
    return events


def test_a_call_ends_with_its_summary_and_keeps_a_transcript_only_when_asked(tmp_path):
    folder = tmp_path / "transcripts"
    events = replay(tmp_path / "off", None)
    assert [x["type"] for x in events][-2:] == ["summary", "status"] and events[-1]["state"] == "stopped"
    s = events[-2]["summary"]
    assert s["heard"] == 1 and s["answered"] == 1
    assert s["questions"][0]["question"] == "What hours can you work?" and s["questions"][0]["lines"]
    assert s["questions"][0]["first_line_ms"] is not None
    assert s["transcript"] is None and not folder.exists()

    s = replay(tmp_path / "on", str(folder))[-2]["summary"]
    assert os.path.dirname(s["transcript"]) == str(folder)
    with open(s["transcript"], encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["version"] == 1 and saved["summary"]["heard"] == 1
    assert any(t["who"] == "them" and t["question"] and "What hours" in t["text"] for t in saved["transcript"])


def test_saving_is_off_by_default_and_reaches_the_call_only_when_turned_on(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert "save_transcripts" in user_settings.EDITABLE
    assert user_settings.load().save_transcripts is False
    assert config_from(EngineConfig(), {}).transcript_dir is None
    s = user_settings.load()
    s.save_transcripts = True
    user_settings.save(s)
    assert config_from(EngineConfig(), {}).transcript_dir == user_settings.transcripts_dir()


def test_saved_transcripts_are_counted_never_overwritten_and_deleted_on_request(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    now = datetime.datetime(2026, 9, 11, 10, 0, 0)
    first = save_transcript({"heard": 0}, [], user_settings.transcripts_dir(), now=now)
    second = save_transcript({"heard": 1}, [], user_settings.transcripts_dir(), now=now)
    assert first != second
    assert user_settings.load().to_json()["transcripts"]["count"] == 2
    assert user_settings.delete_transcripts() == 2
    assert user_settings.load().to_json()["transcripts"]["count"] == 0
