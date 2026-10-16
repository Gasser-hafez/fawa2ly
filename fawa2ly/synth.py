"""
Build a synthetic session that replays exactly like a real one.

    python -m fawa2ly.synth docs/eval/scripts/flutter-junior-en.yaml -o synth1.raw.jsonl
    python -m fawa2ly.replay synth1.raw.jsonl --suggest --live --preset mobile --cv cv.txt

Why this exists: the real recordings (interview6-8) are someone else's mock interview in someone
else's field, so pairing them with your CV produces nonsense, and they cannot be varied. A script
gives a fixture that is repeatable, matches the domain being tested, and can be edited to probe a
specific behaviour (a question split across two segments, two questions in a row, a long pause).

**The timings are measured, not invented.** Every default below comes from interview7
(2245 words, 199 final segments, 13.7 minutes) so that latency measured on a synthetic session
means the same thing as latency measured on a real one:

    speaking rate        3.10 words/sec
    word duration        0.24 s, with no gap between words inside a segment
    interim cadence      one message per second of audio
    interim lag          335 ms after the last word it contains
    final lag            1620 ms when forced mid-speech (n=214), 933 ms when a real silence
                         closed the segment (n=3). 214 of 217 finals were forced, which is
                         exactly why the detector cannot wait for them
    final segment        3.5 s of speech

A segment deliberately runs across a speaker change when the two turns are less than
`pause_split` apart, because that is what Deepgram did: it is how a question ends up glued to
the start of the answer, and it is the case the interim path exists to handle.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional

import yaml

# Measured on interview7; see the module docstring.
WORDS_PER_SEC = 3.10
WORD_DURATION = 0.24
INTERIM_EVERY = 1.00
INTERIM_LAG = 0.335
# The last interim of a segment stops this far short of the final's last word. Measured on
# interview7: 148 of 199 finals had their last word already in an interim, and the tail the last
# interim missed was 0.32 s at p50. Without this the tail miss would be whatever the segment
# length happens to leave over, which understates how often the interim path catches a question.
INTERIM_TAIL_GAP = 0.32
FINAL_LAG = 1.620          # forced mid-speech: n=214, p50 1620 ms
# When the speaker actually stops, Deepgram's endpointing closes the segment instead, and the
# final arrives sooner. Only 3 of 217 finals in interview6+7 were of this kind (people rarely
# pause), but a scripted interview has a pause after every question, so the distinction decides
# whether the fixture reports honest latency. Measured: 818, 933, 1289 ms -> p50 933 ms.
SPEECH_FINAL_LAG = 0.933
ENDPOINTING = 0.3          # Deepgram's endpointing=300, tuned in M0
SEGMENT_SECONDS = 3.5
PAUSE_SPLIT = 0.5          # a gap this long or longer always ends the segment
TURN_PAUSE = 0.35          # default silence between turns
UTTERANCE_END_AFTER = 1.0  # Deepgram's utterance_end_ms, tuned in M0
EPOCH = 1_800_000_000.0    # arbitrary wall clock, only differences matter

SPEAKERS = {"interviewer": 0, "customer": 0, "them": 0, "candidate": 1, "you": 1, "rep": 1}


@dataclass
class Word:
    text: str
    start: float
    end: float
    speaker: int


@dataclass
class Script:
    name: str
    turns: list[dict] = field(default_factory=list)
    preset: str = ""
    note: str = ""

    @classmethod
    def load(cls, path: str) -> "Script":
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        turns = d.get("turns") or []
        if not turns:
            raise ValueError(f"{path}: no turns")
        for i, t in enumerate(turns, 1):
            if not t.get("text"):
                raise ValueError(f"{path}: turn {i} has no text")
            who = t.get("who", "interviewer")
            if who not in SPEAKERS:
                raise ValueError(f"{path}: turn {i} has unknown speaker {who!r}; "
                                 f"use one of {', '.join(sorted(SPEAKERS))}")
        return cls(name=d.get("name") or path, turns=turns, preset=d.get("preset", ""),
                   note=d.get("note", ""))


def lay_out_words(script: Script, rate: float = WORDS_PER_SEC,
                  turn_pause: float = TURN_PAUSE) -> list[Word]:
    """Turns -> a flat word list with start/end times, at a real speaking rate."""
    out: list[Word] = []
    t = 0.5                                   # a little silence before anyone speaks
    step = 1.0 / rate
    for turn in script.turns:
        t += float(turn.get("pause", turn_pause))
        speaker = SPEAKERS[turn.get("who", "interviewer")]
        for token in str(turn["text"]).split():
            out.append(Word(token, round(t, 3), round(t + WORD_DURATION, 3), speaker))
            t += step
    return out


def split_segments(words: list[Word], segment_seconds: float = SEGMENT_SECONDS,
                   pause_split: float = PAUSE_SPLIT) -> list[list[Word]]:
    """Group words the way Deepgram grouped them: by length, and by any real pause."""
    segments: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            gap = w.start - current[-1].end
            too_long = w.end - current[0].start > segment_seconds
            if gap >= pause_split or too_long:
                segments.append(current)
                current = []
        current.append(w)
    if current:
        segments.append(current)
    return segments


def _results(words: list[Word], is_final: bool) -> dict:
    return {"type": "Results", "is_final": is_final,
            "channel": {"alternatives": [{
                "transcript": " ".join(w.text for w in words),
                "words": [{"word": w.text.lower().strip(".,?!"), "punctuated_word": w.text,
                           "start": w.start, "end": w.end, "speaker": w.speaker} for w in words]}]}}


def build_messages(segments: list[list[Word]], interim_every: float = INTERIM_EVERY,
                   interim_lag: float = INTERIM_LAG, final_lag: float = FINAL_LAG,
                   utterance_end_after: float = UTTERANCE_END_AFTER,
                   interim_tail_gap: float = INTERIM_TAIL_GAP,
                   speech_final_lag: float = SPEECH_FINAL_LAG,
                   endpointing: float = ENDPOINTING) -> list[dict]:
    """Segments -> the raw log: cumulative interims, then a final, then UtteranceEnd on a pause.

    Messages are emitted in segment order and never overtake each other. Without that clamp a
    segment closed quickly by silence could deliver its final before the previous segment's
    slower forced final, which no provider does and which would let the pipeline see a question
    before the words leading up to it.
    """
    messages: list[tuple[float, dict]] = []
    emitted_up_to = 0.0

    def emit(t: float, msg: dict) -> None:
        nonlocal emitted_up_to
        t = max(t, emitted_up_to)
        emitted_up_to = t
        messages.append((round(t, 3), msg))

    for i, seg in enumerate(segments):
        start = seg[0].start
        # cumulative interims, exactly as Deepgram sends them: each repeats the segment so far
        next_at = start + interim_every
        so_far_len: list[int] = []
        while next_at < seg[-1].end:
            so_far = [w for w in seg if w.end <= next_at]
            if so_far:
                emit(so_far[-1].end + interim_lag, _results(so_far, False))
                so_far_len.append(len(so_far))
            next_at += interim_every
        # one more interim just before the final, stopping interim_tail_gap short of the end
        tail = [w for w in seg if w.end <= seg[-1].end - interim_tail_gap]
        if tail and (not so_far_len or len(tail) > so_far_len[-1]):
            emit(tail[-1].end + interim_lag, _results(tail, False))
        pause = (segments[i + 1][0].start - seg[-1].end) if i + 1 < len(segments) else 2.0
        # a real silence closes the segment through endpointing, which is faster than being
        # forced mid-speech; the interviewer pausing after a question is exactly this case
        ended_by_silence = pause >= endpointing
        lag = speech_final_lag if ended_by_silence else final_lag
        msg = _results(seg, True)
        if ended_by_silence:
            msg["speech_final"] = True
        emit(seg[-1].end + lag, msg)
        if pause >= utterance_end_after:
            emit(seg[-1].end + utterance_end_after,
                 {"type": "UtteranceEnd", "last_word_end": seg[-1].end})
    return [{"t_recv": round(EPOCH + t, 3), "t_rel": round(t, 3), "msg": m} for t, m in messages]


def generate(script: Script, **kw) -> list[dict]:
    words = lay_out_words(script, rate=kw.get("rate", WORDS_PER_SEC),
                          turn_pause=kw.get("turn_pause", TURN_PAUSE))
    segments = split_segments(words, kw.get("segment_seconds", SEGMENT_SECONDS),
                              kw.get("pause_split", PAUSE_SPLIT))
    return build_messages(segments, kw.get("interim_every", INTERIM_EVERY),
                          kw.get("interim_lag", INTERIM_LAG), kw.get("final_lag", FINAL_LAG))


def write(messages: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for m in messages:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", help="script YAML (see docs/eval/scripts/)")
    ap.add_argument("-o", "--out", default=None, help="output raw log (default: <script>.raw.jsonl)")
    ap.add_argument("--rate", type=float, default=WORDS_PER_SEC, help="words per second")
    ap.add_argument("--final-lag", type=float, default=FINAL_LAG, help="seconds before a final arrives")
    a = ap.parse_args(argv)
    script = Script.load(a.script)
    messages = generate(script, rate=a.rate, final_lag=a.final_lag)
    out = a.out or (a.script.rsplit(".", 1)[0] + ".raw.jsonl")
    write(messages, out)
    questions = sum(1 for t in script.turns if t.get("who", "interviewer") != "candidate"
                    and str(t["text"]).rstrip().endswith("?"))
    duration = messages[-1]["t_rel"] if messages else 0
    print(f"[synth] {script.name}: {len(script.turns)} turns, ~{questions} question mark(s), "
          f"{duration:.0f} s of audio, {len(messages)} messages -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
