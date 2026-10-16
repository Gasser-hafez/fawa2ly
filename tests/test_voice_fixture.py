"""voice_fixture: laying out a voiced script and scoring the turns the pipeline closed (no network)."""

import numpy as np
import pytest

from fawa2ly.audio.resample import TARGET_SR
from fawa2ly.synth import TURN_PAUSE, Script
from fawa2ly.voice_fixture import LEAD_IN_S, TAIL_S, Closed, Span, lay_out, score, trim


def tone(seconds: float) -> bytes:
    return (np.ones(int(seconds * TARGET_SR), dtype=np.int16) * 5000).tobytes()


def test_trim_drops_the_silence_around_a_clip():
    quiet = b"\x00\x00" * TARGET_SR
    out = trim(quiet + tone(1.0) + quiet)
    assert len(out) / 2 / TARGET_SR == pytest.approx(1.1, abs=0.001)   # 50 ms kept on each side


def test_turns_sit_on_the_track_with_their_pauses_and_the_candidate_as_silence():
    script = Script(name="t", turns=[
        {"who": "interviewer", "text": "احكيلي عن نفسك", "expect": "question"},
        {"who": "candidate", "text": " ".join(["كلمة"] * 31), "pause": 0.5},   # 10 s at 3.1 words/s
        {"who": "interviewer", "text": "شكرا", "pause": 1.0, "expect": "statement"},
    ])
    track, (a, b, c) = lay_out(script, {1: tone(2.0), 3: tone(1.0)})
    assert (a.start, a.end) == pytest.approx((LEAD_IN_S + TURN_PAUSE, LEAD_IN_S + TURN_PAUSE + 2.0), abs=0.001)
    assert (b.start, b.end) == pytest.approx((a.end + 0.5, a.end + 10.5), abs=0.001)
    assert (c.start, c.end) == pytest.approx((b.end + 1.0, b.end + 2.0), abs=0.001)
    assert (a.expect, b.expect, c.expect) == ("question", None, "statement")
    assert len(track) / 2 / TARGET_SR == pytest.approx(c.end + TAIL_S, abs=0.001)


def test_score_counts_caught_missed_and_answered_statements():
    spans = [Span(1, "interviewer", "q1", "question", 0.0, 2.0),
             Span(2, "interviewer", "s1", "statement", 3.0, 4.0),
             Span(3, "interviewer", "q2", "question", 4.3, 6.0),
             Span(4, "interviewer", "q3", "question", 8.0, 9.0),
             Span(5, "interviewer", "s2", "statement", 11.0, 12.0)]
    closed = [Closed("request_opener", "q1", 0.1, 1.9, 3.0),
              Closed("wh_word", "s1 q2", 3.0, 5.9, 7.0),     # a statement glued to a real question
              Closed("silence", "q3", 8.0, 9.0, 10.0),
              Closed("yes_no", "s2", 11.0, 12.0, 13.0)]
    r = score(spans, closed)
    assert [row["verdict"] for row in r["rows"]] == ["caught", "ok", "caught", "missed", "false alarm"]
    assert (r["caught"], r["questions"], r["false_alarms"], r["statements"]) == (2, 3, 1, 2)
    assert r["rows"][0]["latency_s"] == 1.0
    assert r["median_latency_s"] == 1.0
