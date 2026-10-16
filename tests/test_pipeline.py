import asyncio
import json

from fawa2ly.pipeline import Pipeline, split_by_speaker
from fawa2ly.replay import replay
from fawa2ly.stt.base import STTEvent, Word


def final(t_recv, t_rel, spec):
    """spec: list of (text, start, end, speaker)."""
    words = [Word(t, s, e, spk) for t, s, e, spk in spec]
    return STTEvent("final", t_recv, t_rel, " ".join(w.text for w in words), words)


def utterance_end(t_recv, t_rel):
    return STTEvent("utterance_end", t_recv, t_rel)


def test_split_by_speaker_runs_and_fallback():
    words = [Word("Hi?", 0.0, 0.5, 0), Word("Yeah.", 0.9, 1.2, 1), Word("Ok.", 1.3, 1.5, 1)]
    assert split_by_speaker(words, "ignored") == [(0, "Hi?", 0.5), (1, "Yeah. Ok.", 1.5)]
    assert split_by_speaker([], "raw text") == [(None, "raw text", None)]


def test_question_fires_immediately_and_listener_filler_is_separate():
    pipe = Pipeline()
    ev = final(100.0, 3.5, [
        ("Could", 1.0, 1.2, 0), ("you", 1.2, 1.3, 0), ("tell", 1.3, 1.5, 0), ("me", 1.5, 1.6, 0),
        ("about", 1.6, 1.8, 0), ("yourself?", 1.8, 2.4, 0),
        ("Yeah.", 2.8, 3.0, 1),
    ])
    out = pipe.feed(ev)
    assert len(out) == 1
    u = out[0]
    assert (u.n, u.speaker, u.trigger, u.segments) == (1, 0, "question_mark", 1)
    assert u.text == "Could you tell me about yourself?"
    assert u.speech_to_text_ms == 1100          # (3.5 - 2.4) * 1000
    assert u.gap_ms == 0 and u.t_flush == 100.0
    assert u.proc_ms >= 0

    out = pipe.feed(utterance_end(101.0, 4.2))
    assert len(out) == 1
    assert (out[0].n, out[0].speaker, out[0].trigger, out[0].text) == (2, 1, "silence", "Yeah.")
    assert out[0].speech_to_text_ms == 1200     # (4.2 - 3.0) * 1000, the "Yeah." word end
    assert out[0].gap_ms == 1000


def test_speaker_change_uses_previous_speakers_audio_end():
    pipe = Pipeline()
    assert pipe.feed(final(10.0, 5.2, [("I", 4.0, 4.1, 0), ("worked", 4.1, 4.5, 0), ("there.", 4.5, 5.0, 0)])) == []
    out = pipe.feed(final(12.0, 7.0, [("Okay.", 6.0, 6.5, 1)]))
    assert len(out) == 1
    u = out[0]
    assert (u.speaker, u.trigger, u.text) == (0, "speaker_change", "I worked there.")
    assert u.speech_to_text_ms == 2000          # 7.0 - 5.0, not 7.0 - 6.5
    assert u.gap_ms == 2000                     # 12.0 - 10.0


def test_multi_segment_utterance_accumulates_until_silence():
    pipe = Pipeline()
    assert pipe.feed(final(1.0, 1.0, [("My", 0.0, 0.2, 0), ("name", 0.2, 0.5, 0)])) == []
    assert pipe.feed(final(2.0, 2.0, [("is", 1.0, 1.1, 0), ("Sam.", 1.1, 1.6, 0)])) == []
    out = pipe.feed(utterance_end(3.0, 3.0))
    u = out[0]
    assert (u.text, u.segments, u.t_first_final, u.t_last_final) == ("My name is Sam.", 2, 1.0, 2.0)
    assert u.speech_to_text_ms == 1400 and u.gap_ms == 1000


def test_no_words_falls_back_to_text_and_unknown_latency():
    pipe = Pipeline()
    out = pipe.feed(STTEvent("final", 5.0, 2.0, "Walk me through your resume."))
    assert len(out) == 1
    assert (out[0].speaker, out[0].trigger, out[0].speech_to_text_ms) == (None, "request_opener", None)


def test_interim_and_error_are_ignored_and_silence_with_empty_buffer_is_noop():
    pipe = Pipeline()
    assert pipe.feed(STTEvent("interim", 1.0, 1.0, "hel")) == []
    assert pipe.feed(STTEvent("error", 1.0, 1.0, "boom")) == []
    assert pipe.feed(utterance_end(2.0, 2.0)) == []
    assert pipe.n == 0


def test_on_piece_callback_sees_every_piece_before_detection():
    seen = []
    pipe = Pipeline(on_piece=lambda spk, piece: seen.append((spk, piece)))
    pipe.feed(final(1.0, 1.0, [("Hi?", 0.0, 0.5, 0), ("Yeah.", 0.9, 1.2, 1)]))
    assert seen == [(0, "Hi?"), (1, "Yeah.")]


def raw_line(t_recv, t_rel, msg):
    return json.dumps({"t_recv": t_recv, "t_rel": t_rel, "msg": msg})


def dg_final(transcript, words):
    return {"type": "Results", "is_final": True,
            "channel": {"alternatives": [{"transcript": transcript, "words": [
                {"word": t.lower().strip("?."), "punctuated_word": t, "start": s, "end": e, "speaker": spk}
                for t, s, e, spk in words]}]}}


def test_replay_writes_same_schema_as_live(tmp_path):
    raw = tmp_path / "s.raw.jsonl"
    raw.write_text("\n".join([
        raw_line(1.0, None, {"type": "Metadata"}),
        raw_line(5.0, 3.0, dg_final("What is your name?", [
            ("What", 1.0, 1.2, 0), ("is", 1.2, 1.3, 0), ("your", 1.3, 1.5, 0), ("name?", 1.5, 2.0, 0)])),
        raw_line(8.0, 6.0, dg_final("Sam.", [("Sam.", 4.0, 4.5, 1)])),
        raw_line(9.5, 7.5, {"type": "UtteranceEnd", "last_word_end": 4.5}),
    ]) + "\n", encoding="utf-8")
    out = tmp_path / "u.jsonl"

    utts = asyncio.run(replay(raw, str(out), quiet=True))
    assert [(u.n, u.speaker, u.trigger) for u in utts] == [(1, 0, "question_mark"), (2, 1, "silence")]
    # Turn mode, live since 2026-09-10: the question is decided when its turn closes - here at the
    # next speaker's final (t_rel 6.0), not at its own (3.0) - so 6.0 - 2.0 = 4000 ms.
    assert [u.speech_to_text_ms for u in utts] == [4000, 3000]

    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["text"] == "What is your name?"
    assert set(rows[0]) >= {"n", "speaker", "trigger", "text", "segments", "t_first_final",
                            "t_last_final", "t_flush", "gap_ms", "speech_to_text_ms", "proc_ms"}
