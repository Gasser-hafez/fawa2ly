"""Speaking pace and filler words (roadmap item 8, 2026-09-11), from the user's own microphone.

The arithmetic is where a meter like this lies: a pause counted as talking calls a careful speaker
slow, a filler counted as a word calls a nervous one fast, and a number from five words is noise.
And the other side must never be measured, or the interviewer's pace shows up as the user's.
"""

import asyncio
import io

import pytest

from fawa2ly import settings as user_settings
from fawa2ly.engine import Engine, EngineConfig, stt_params
from fawa2ly.pace import MIN_WORDS, WINDOW_WORDS, PaceMeter, is_filler
from fawa2ly.stt.base import STTEvent, Word
from fawa2ly.summary import CallRecord


def spoken(texts, start=0.0, duration=0.25, gap=0.05):
    out, t = [], start
    for text in texts:
        out.append(Word(text, t, t + duration, None))
        t += duration + gap
    return out


def test_only_talking_counts_as_time_and_a_filler_is_time_but_not_a_word():
    m = PaceMeter()
    first = spoken(["word"] * 6)
    m.feed(first)
    assert m.snapshot()["wpm"] is None, f"fewer than {MIN_WORDS} words is not a pace yet"
    m.feed(spoken(["um,"] + ["word"] * 6, start=first[-1].end + 3.0))   # a 3 s pause, then a filler
    s = m.snapshot()
    # 13 tokens: 13 x 0.25 s spoken, and 11 short gaps of 0.05 s; the 3 s pause is not talking
    assert s["wpm"] == pytest.approx(12 / (13 * 0.25 + 11 * 0.05) * 60, abs=1)
    assert (s["words"], s["fillers"], s["english"]) == (12, 1, True)
    assert s["average_wpm"] == s["wpm"]


def test_the_live_number_follows_the_last_words_and_the_average_the_whole_call():
    m = PaceMeter()
    fast = spoken(["word"] * 100, duration=0.2, gap=0.0)                         # 300 words a minute
    m.feed(fast)
    m.feed(spoken(["word"] * WINDOW_WORDS, start=fast[-1].end + 2.0, duration=0.4, gap=0.1))
    s = m.snapshot()
    assert s["wpm"] == pytest.approx(WINDOW_WORDS / (WINDOW_WORDS * 0.4 + (WINDOW_WORDS - 1) * 0.1) * 60, abs=1)
    assert s["wpm"] < s["average_wpm"] < 300


def test_filler_words_are_deepgrams_and_are_not_counted_outside_english():
    assert is_filler("Um,") and is_filler("uh-huh.") and is_filler("mhmm")
    assert not is_filler("umbrella") and not is_filler("like")
    m = PaceMeter(count_fillers=False)
    m.feed(spoken(["كلمة"] * 20))
    s = m.snapshot()
    assert s["fillers"] is None and s["english"] is False and s["wpm"]


def test_filler_words_are_asked_for_on_the_users_english_stream_only():
    assert stt_params("user", "en") == {"filler_words": "true"}
    assert stt_params("user", "en-US") == {"filler_words": "true"}
    for tag, lang in (("other", "en"), ("user", "ar-EG"), ("user", "multi")):
        assert stt_params(tag, lang) == {}


class Stream:
    def __init__(self, events):
        self._events = events

    async def events(self):
        for e in self._events:
            yield e


def final(words):
    return STTEvent("final", 100.0, words[-1].end, " ".join(w.text for w in words), words)


def test_the_users_microphone_is_measured_and_the_other_side_never_is():
    events = []
    e = Engine(EngineConfig(lang="en"), on_event=events.append)
    talk = [final(spoken(["word"] * 14)), STTEvent("utterance_end", 101.0, 6.0)]
    asyncio.run(e._consumer(Stream(talk), "user", io.StringIO()))
    paces = [x for x in events if x["type"] == "pace"]
    assert paces and paces[-1]["wpm"] and paces[-1]["english"] is True
    events.clear()
    other = Engine(EngineConfig(lang="en"), on_event=events.append)
    asyncio.run(other._consumer(Stream([final(spoken(["word"] * 14)), STTEvent("utterance_end", 101.0, 6.0)]),
                                "other", io.StringIO()))
    assert not [x for x in events if x["type"] == "pace"]


def test_the_call_summary_carries_the_users_pace_and_the_meter_is_a_setting(monkeypatch, tmp_path):
    r = CallRecord()
    r.observe({"type": "status", "state": "listening"})
    r.observe({"type": "pace", "wpm": 150, "average_wpm": 142, "words": 300, "fillers": 7, "english": True})
    s = r.summary()
    assert (s["you_wpm"], s["you_fillers"]) == (142, 7)
    assert CallRecord().summary()["you_wpm"] is None, "no microphone, no pace"
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert "pace_meter" in user_settings.EDITABLE and user_settings.load().pace_meter is True
