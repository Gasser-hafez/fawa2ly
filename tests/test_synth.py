import json

import pytest
import yaml

from fawa2ly.synth import (EPOCH, FINAL_LAG, INTERIM_LAG, Script, build_messages, generate,
                            lay_out_words, split_segments, write)

SCRIPT = {
    "name": "t",
    "turns": [
        {"who": "interviewer", "text": "What hours can you work?"},
        {"who": "candidate", "text": "From nine.", "pause": 0.2},
    ],
}


def write_script(tmp_path, data=SCRIPT):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


def test_script_load_and_validation(tmp_path):
    s = Script.load(write_script(tmp_path))
    assert s.name == "t" and len(s.turns) == 2
    with pytest.raises(ValueError, match="no turns"):
        Script.load(write_script(tmp_path, {"name": "x", "turns": []}))
    with pytest.raises(ValueError, match="no text"):
        Script.load(write_script(tmp_path, {"turns": [{"who": "interviewer"}]}))
    with pytest.raises(ValueError, match="unknown speaker"):
        Script.load(write_script(tmp_path, {"turns": [{"who": "robot", "text": "hi"}]}))


def test_words_are_laid_out_at_the_measured_speaking_rate(tmp_path):
    words = lay_out_words(Script.load(write_script(tmp_path)), rate=2.0, turn_pause=0.5)
    assert [w.text for w in words] == ["What", "hours", "can", "you", "work?", "From", "nine."]
    assert [w.speaker for w in words] == [0, 0, 0, 0, 0, 1, 1]
    assert words[0].start == 1.0                       # 0.5 lead-in + 0.5 turn pause
    assert words[1].start - words[0].start == 0.5      # 2 words/sec
    assert words[5].start == pytest.approx(words[4].start + 0.5 + 0.2)   # the turn's own pause


def test_segments_split_on_pauses_and_on_length():
    script = Script(name="x", turns=[{"who": "interviewer", "text": "one two three"},
                                     {"who": "candidate", "text": "four", "pause": 2.0}])
    words = lay_out_words(script, rate=4.0)
    segments = split_segments(words, segment_seconds=10, pause_split=0.5)
    assert [[w.text for w in seg] for seg in segments] == [["one", "two", "three"], ["four"]]

    long_turn = Script(name="x", turns=[{"who": "interviewer", "text": " ".join("w" * 1 for _ in range(20))}])
    segs = split_segments(lay_out_words(long_turn, rate=4.0), segment_seconds=1.0, pause_split=0.5)
    assert len(segs) > 1 and all(seg for seg in segs)


def test_a_segment_runs_across_speakers_when_the_gap_is_short():
    """Deepgram glued a question to the answer; the interim path exists for exactly that."""
    script = Script(name="x", turns=[{"who": "interviewer", "text": "Ready?"},
                                     {"who": "candidate", "text": "Yes.", "pause": 0.1}])
    segments = split_segments(lay_out_words(script, rate=4.0, turn_pause=0.1), pause_split=0.5)
    assert len(segments) == 1
    assert {w.speaker for w in segments[0]} == {0, 1}


def test_messages_are_cumulative_interims_then_a_final_with_the_measured_lags(tmp_path):
    msgs = generate(Script.load(write_script(tmp_path)), rate=2.0, interim_every=1.0)
    kinds = [(m["msg"]["type"], m["msg"].get("is_final")) for m in msgs]
    assert ("Results", True) in kinds and ("Results", False) in kinds
    assert kinds[-1][0] in ("Results", "UtteranceEnd")

    from fawa2ly.synth import SPEECH_FINAL_LAG
    finals = [m for m in msgs if m["msg"].get("is_final")]
    for m in finals:
        words = m["msg"]["channel"]["alternatives"][0]["words"]
        lag = SPEECH_FINAL_LAG if m["msg"].get("speech_final") else FINAL_LAG
        assert m["t_rel"] == pytest.approx(words[-1]["end"] + lag, abs=0.01)
        assert m["t_recv"] == pytest.approx(EPOCH + m["t_rel"], abs=0.01)

    interims = [m for m in msgs if m["msg"].get("is_final") is False]
    for m in interims:
        words = m["msg"]["channel"]["alternatives"][0]["words"]
        assert m["t_rel"] == pytest.approx(words[-1]["end"] + INTERIM_LAG, abs=0.01)
    # cumulative: each interim of a segment starts from that segment's first word
    first_seg = [m for m in interims if m["msg"]["channel"]["alternatives"][0]["words"][0]["start"]
                 == interims[0]["msg"]["channel"]["alternatives"][0]["words"][0]["start"]]
    lengths = [len(m["msg"]["channel"]["alternatives"][0]["words"]) for m in first_seg]
    assert lengths == sorted(lengths)


def test_messages_arrive_in_time_order_and_replay_through_the_pipeline(tmp_path):
    import asyncio

    from fawa2ly.pipeline import Pipeline
    from fawa2ly.stt import FakeSTT

    path = tmp_path / "s.raw.jsonl"
    write(generate(Script.load(write_script(tmp_path))), str(path))
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert [r["t_recv"] for r in rows] == sorted(r["t_recv"] for r in rows)

    async def run():
        pipe = Pipeline()
        out = []
        async for ev in FakeSTT(str(path)).events():
            out += pipe.feed(ev)
        return out

    utterances = asyncio.run(run())
    fired = [u for u in utterances if u.trigger == "question_mark"]
    assert fired and "What hours can you work?" in fired[0].text
    assert fired[0].question_end_to_fire_ms is not None


def test_the_shipped_script_matches_the_cv_domain():
    s = Script.load("docs/eval/scripts/flutter-junior-en.yaml")
    assert s.preset == "mobile" and len(s.turns) > 15
    text = " ".join(str(t["text"]) for t in s.turns).lower()
    for term in ("flutter", "firebase", "laravel", "riverpod"):
        assert term in text
    assert sum(1 for t in s.turns if str(t["text"]).rstrip().endswith("?")) >= 8


def test_the_last_interim_stops_just_short_of_the_final(tmp_path):
    """interview7: the tail an interim missed was 0.32 s at p50, so a question usually
    appears with its '?' in an interim before the final ever arrives."""
    from fawa2ly.synth import INTERIM_TAIL_GAP
    script = Script(name="x", turns=[{"who": "interviewer",
                                      "text": "So what hours would you be able to work for us here?"}])
    msgs = generate(script)
    # compare within the FIRST segment: its final is the first one whose words start at 0.85
    finals = [m for m in msgs if m["msg"].get("is_final")]
    final = finals[0]
    seg_start = final["msg"]["channel"]["alternatives"][0]["words"][0]["start"]
    interims = [m for m in msgs if m["msg"].get("is_final") is False
                and m["msg"]["channel"]["alternatives"][0]["words"][0]["start"] == seg_start]
    end = final["msg"]["channel"]["alternatives"][0]["words"][-1]["end"]
    last_interim_end = interims[-1]["msg"]["channel"]["alternatives"][0]["words"][-1]["end"]
    # the cut lands on a word boundary, so allow one word step (1 / words-per-second) of slack
    from fawa2ly.synth import WORDS_PER_SEC
    assert 0 <= end - last_interim_end <= INTERIM_TAIL_GAP + 1 / WORDS_PER_SEC
    assert interims[-1]["t_rel"] < final["t_rel"]


def test_a_pause_after_a_question_closes_the_segment_faster():
    """Endpointing finalises after real silence (933 ms) instead of the forced 1620 ms."""
    from fawa2ly.synth import FINAL_LAG, SPEECH_FINAL_LAG
    script = Script(name="x", turns=[{"who": "interviewer", "text": "What hours can you work?"},
                                     {"who": "candidate", "text": "From nine.", "pause": 1.0}])
    msgs = generate(script)
    finals = [m for m in msgs if m["msg"].get("is_final")]
    first = finals[0]
    words = first["msg"]["channel"]["alternatives"][0]["words"]
    assert first["msg"].get("speech_final") is True
    assert first["t_rel"] == pytest.approx(words[-1]["end"] + SPEECH_FINAL_LAG, abs=0.01)
    assert SPEECH_FINAL_LAG < FINAL_LAG


def test_speech_that_runs_on_gives_a_forced_final():
    """One long turn splits by length, so every segment but the last is followed by more speech."""
    from fawa2ly.synth import FINAL_LAG
    script = Script(name="x", turns=[{"who": "interviewer", "text": " ".join(["word"] * 60)}])
    msgs = generate(script)
    finals = [m for m in msgs if m["msg"].get("is_final")]
    assert len(finals) > 1
    assert not finals[0]["msg"].get("speech_final")          # cut off mid-speech
    words = finals[0]["msg"]["channel"]["alternatives"][0]["words"]
    assert finals[0]["t_rel"] == pytest.approx(words[-1]["end"] + FINAL_LAG, abs=0.01)
    assert finals[-1]["msg"].get("speech_final") is True     # the speaker finally stops


def test_messages_never_overtake_each_other():
    """A segment closed by silence must not deliver its final before an earlier forced one."""
    script = Script(name="x", turns=[
        {"who": "interviewer", "text": " ".join(["word"] * 14) + " here?"},
        {"who": "candidate", "text": "Yes.", "pause": 1.5},
    ])
    msgs = generate(script)
    times = [m["t_rel"] for m in msgs]
    assert times == sorted(times)
    finals = [m["t_rel"] for m in msgs if m["msg"].get("is_final")]
    assert finals == sorted(finals) and len(finals) >= 2
