"""
Pipeline: STTEvent stream -> Utterance events.

Pure and synchronous: feed() takes one event and returns zero or more finished
utterances. All timestamps come from the event, never from the wall clock, so
a replayed session (FakeSTT) yields the same numbers as the live one.

An utterance is flushed by one of four triggers:
  1. a complete sentence in an INTERIM result is a question/request -> fire now
     (source="interim"). Decision (M1, 2026-09-08): in continuous speech Deepgram
     finalizes segments 1-2.5 s after the words were spoken, so waiting for the
     final cost 2-3 s per question on interview6. Interims show the question with
     its "?" ~400 ms after it was said.
     Amended 2026-09-10 after the first live rehearsal: a sentence that does not
     end in "?" is held for one event first. "Tell me about yourself." is a whole
     sentence and half a question when the rest is "and your experience with
     Flutter", and the half that decides the answer is usually the second one.
     If nothing follows it, it fires (about a second later); if the speaker
     carried on, the longer text is what gets judged. See _resolve_hold.
  2. the detector says a piece of a FINAL segment was a question/request
  3. the diarized speaker changed ("speaker_change")
  4. the provider reported silence ("silence", from UtteranceEnd)

De-duplication uses the audio clock, not text: after an early fire every word
that STARTED before the fired question's end is dropped from later interims and
from the final, through the end of its sentence (see _unconsumed). Word start
times are stable between interim and final even when the wording changes
("Well, what hours" -> "What hours").
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from .stt.base import STTEvent, Word
from .turn.detector import classify, sentences

_TERMINAL = ".?!"
_FIRED_TOLERANCE_S = 0.5   # a candidate ending within this of an earlier fire is the same question
CONTINUATION_WINDOW_S = 4.0   # see Pipeline._is_continuation
_SAME_START_S = 0.25          # word starts are stable between interims to within ~0.1 s
_GROWTH_CAP_WORDS = 60        # stop following a question that has run on this long
REOPEN_S = 2.5               # turn mode: a turn starting this soon after a question turn ended may
                              # be the same question resumed (interview7 has 1.04 s and 1.78 s pauses
                              # mid-sentence, longer than the 1 s of silence that closes a turn)
_REOPEN_MIN_WORDS = 3         # "Mm-hmm." inside that window is a backchannel, not the rest of a question
_REOPEN_MAX_WORDS = 20        # a resumption is a phrase; a turn this long after a question is an answer
_MAX_SPECULATIONS = 2         # early asks per turn: each is a model call that may be thrown away
_TURN_PAUSE_S = 0.75          # a pause this long after the fired point ends the sentence. Measured:
                              # 0.57 s inside a real continuation ("Get X, what parts of the app"),
                              # 0.88 s where interview7's candidate began answering ("the job | I think")
_GREW_WORDS = 3               # new words a fired question must gain to be re-asked: "for me?" ->
                              # "for me, Sam?" adds a name, not a question (interview6)


def normalize_question(text: str) -> str:
    """Case, punctuation and spacing removed: how a speculative ask is matched to its turn."""
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in text).split())


def _unfinished_tail(text: str) -> str:
    """Whatever follows the last sentence end: the start of a sentence still being spoken."""
    cut = max(text.rfind(c) for c in _TERMINAL)
    return text[cut + 1:].strip()


@dataclass
class Utterance:
    n: int
    speaker: Optional[int]
    trigger: str
    text: str
    segments: int
    t_first_final: float
    t_last_final: float
    t_flush: float
    gap_ms: int                          # t_flush - t_last_final: how long we sat on the last segment
    speech_to_text_ms: Optional[int]     # t_rel at flush - audio end of the last word we flushed
    proc_ms: float = 0.0                 # pipeline CPU time for the feed() that produced this
    source: str = "final"                # "interim" if fired early from an interim result
    question_end_to_fire_ms: Optional[int] = None   # t_rel at flush - audio end of the question sentence
    stream: str = "other"                # "other" = loopback (the other party), "user" = your mic
    revises: Optional[int] = None        # n of an earlier fired question this one turned out to extend
    speculative: bool = False            # turn mode: an early ask while the turn is still open; not shown
    # Turn mode: the audio clock of the turn's first and last word, so a closed turn can be matched
    # to the labeled pieces it holds (fawa2ly.eval --turns, 2026-09-12). None in sentence mode.
    audio_start: Optional[float] = None
    audio_end: Optional[float] = None

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Sentence:
    text: str
    start: float
    end: float
    complete: bool        # ends with . ? or !


def sentences_from_words(words: list[Word]) -> list[Sentence]:
    out, cur = [], []
    for w in words:
        cur.append(w)
        if w.text and w.text[-1] in _TERMINAL:
            out.append(Sentence(" ".join(x.text for x in cur), cur[0].start, cur[-1].end, True))
            cur = []
    if cur:
        out.append(Sentence(" ".join(x.text for x in cur), cur[0].start, cur[-1].end, False))
    return out


def split_words_by_speaker(words: list[Word]) -> list[tuple[Optional[int], list[Word]]]:
    """One (speaker, words) run per speaker change."""
    runs: list[tuple[Optional[int], list[Word]]] = []
    for w in words:
        if runs and runs[-1][0] == w.speaker:
            runs[-1][1].append(w)
        else:
            runs.append((w.speaker, [w]))
    return runs


def split_by_speaker(words: list[Word], fallback_text: str) -> list[tuple[Optional[int], str, Optional[float]]]:
    """(speaker, text, audio_end) runs, one per speaker change. One run if there are no words."""
    if not words:
        return [(None, fallback_text, None)]
    return [(spk, " ".join(w.text for w in ws), ws[-1].end) for spk, ws in split_words_by_speaker(words)]


class Pipeline:
    def __init__(self, classify: Callable[[str], Optional[str]] = classify,
                 on_piece: Optional[Callable[[Optional[int], str], None]] = None,
                 interim_detection: bool = True, stream: str = "other",
                 settle_interims: bool = True, turns: bool = False, speculate: bool = True,
                 reopen: bool = False):
        self.classify = classify
        self.on_piece = on_piece      # called for every final per-speaker piece, before detection (UI use)
        self.interim_detection = interim_detection
        self.settle_interims = settle_interims   # see _hold_or_fire
        self.turns = turns                       # answer whole turns closed by silence; see _feed_turn
        self.speculate = speculate               # turn mode: ask early, show at the close
        # Off by default, because that is what it measured: on interview7, a single-stream video
        # where the candidate answers on the same audio, all 24 reopens glued the answer onto the
        # question ("Where do you see yourself in five years...? Hopefully I'll be able to develop
        # a market..."), and no rehearsal question needed one. Kept, capped and tested for the day
        # a recording shows an interviewer genuinely resuming after a pause.
        self.reopen = reopen
        self.last_turn: Optional[dict] = None    # the last question turn, in case it resumes
        self.hold: Optional[dict] = None         # a question sentence waiting to see if it grows
        self.last_q: Optional[dict] = None       # the last interim fire, to catch it growing
        self.rev_words: list[Word] = []          # final words that may be the rest of it
        self.rev_final: set = set()
        self.stream = stream          # stamped on every Utterance; one Pipeline per audio stream
        self.n = 0
        self.cur_spk: Optional[int] = None
        self.fired_until: float = float("-inf")   # audio clock: everything before this was already emitted
        self.fired_ends: list[float] = []
        self.last_fire_end: Optional[float] = None   # audio end of the last question that fired
        self._reset()

    def _reset(self):
        self.turn_start: Optional[float] = None   # audio clock of the first word of the open turn
        self.spec_keys: set = set()               # speculative asks already made for the open turn
        self.buf: list[str] = []
        self.first_t: Optional[float] = None
        self.last_final_t: Optional[float] = None
        self.audio_end: Optional[float] = None   # audio clock of the last word in buf

    # ---- public -----------------------------------------------------------------------------

    def feed(self, ev: STTEvent) -> list[Utterance]:
        t0 = time.perf_counter()
        out: list[Utterance] = []
        if self.turns:
            self._feed_turn(ev, out)
        elif ev.kind == "interim":
            if self.interim_detection and ev.words:
                self._feed_interim(ev, out)
        elif ev.kind == "final":
            self._feed_final(ev, out)
        elif ev.kind == "utterance_end":
            self.hold = None
            self._track_growth(ev, out, closing=True)
            # The speaker stopped. Anything held back because a forced final might have cut it
            # is as complete as it will ever be, so judge it now or it is answered as "silence"
            # and never reaches the model.
            why = None
            if self.buf:
                for sent in sentences(" ".join(self.buf)):
                    r = self.classify(sent)
                    if r is not None:
                        why = r
            self._flush(why or "silence", ev, out,
                        q_end=self.audio_end if why else None)
        proc_ms = (time.perf_counter() - t0) * 1000
        for u in out:
            u.proc_ms = proc_ms
        return out

    # ---- internals --------------------------------------------------------------------------

    def _unconsumed(self, words: list[Word]) -> list[Word]:
        """Drop words already emitted by an early fire.

        Word START times are stable between interims and the final (within ~0.1 s), but the END
        of the last word of an interim is provisional: it is cut at the audio position Deepgram
        had reached (interview6: "job?" ends at 41.77 in the interim, 42.01 in the final). So a
        word is old if it started before the fired question's provisional end. The drop then
        extends to the end of that sentence, so a revision such as "for me?" -> "for me, Sam?"
        does not leave a "me, Sam?" fragment behind that would fire again.
        """
        out, dropping = [], False
        for w in words:
            if w.start < self.fired_until:
                dropping = True
            if dropping:
                if w.text and w.text[-1] in _TERMINAL:
                    dropping = False
                continue
            out.append(w)
        return out

    def _already_fired(self, end: float) -> bool:
        return any(abs(end - e) < _FIRED_TOLERANCE_S for e in self.fired_ends)

    def _mark_fired(self, end: float) -> None:
        self.fired_until = max(self.fired_until, end)
        self.fired_ends.append(end)
        del self.fired_ends[:-50]

    def _is_continuation(self, s: Sentence) -> bool:
        """A piece that starts with a lowercase letter shortly after a fire is the tail of the
        question that already fired ("do you want this particular position" -> "our customer
        service department?"), not a new one. Deepgram capitalizes sentence starts, so a
        lowercase start means the split happened mid-sentence. 2026-09-08: tails were 5 of the
        11 remaining false alarms on the eval set; measured by fires per session in replay."""
        return (self.last_fire_end is not None and s.text[:1].islower()
                and 0 <= s.start - self.last_fire_end < CONTINUATION_WINDOW_S)

    # ---- turn mode -----------------------------------------------------------------------------

    def _feed_turn(self, ev: STTEvent, out: list[Utterance]) -> None:
        """Answer the interviewer's whole turn, closed by silence, not the first sentence of it.

        Every sentence-level rule before this one answered a fragment in some recording, because
        sentence punctuation is not where a question ends. rehearsal5: Deepgram endpointed
        "Tell me about yourself.", then "Your experience with Flutter.", then "Including one
        project you're particularly proud of." - speech_final three times inside one question,
        because a synthetic voice pauses between phrases - and only UtteranceEnd (1000 ms of
        silence) arrived once, after all of it. Replaying rehearsal4 and rehearsal5 through that
        rule gave every question whole, with the turn closing 1.45-1.71 s after the last word.

        So finals only accumulate, and UtteranceEnd closes the turn: if any sentence in it is a
        question, the whole turn is the question. To win back the second that waiting costs, a
        complete question sentence seen while the turn is still open is emitted as a speculative
        utterance, which the suggester asks early and shows only if the closed turn says exactly
        that.
        """
        if ev.kind == "interim":
            # Not speculated on. Measured on the rehearsals: speculating on interims as well as
            # finals found no extra hit (rehearsal4 8 of 10 either way) and wasted three times the
            # requests (6 against 2), because interim wording differs from the final's ("GitX"
            # against "get x") and the turn closes on the finals' wording.
            return
        if ev.kind == "final":
            if ev.words:
                pieces = [(spk, " ".join(w.text for w in ws), ws)
                          for spk, ws in split_words_by_speaker(ev.words)]
            elif ev.text:
                pieces = [(None, ev.text, [])]
            else:
                return
            for spk, piece, ws in pieces:
                if self.buf and self.cur_spk is not None and spk is not None and spk != self.cur_spk:
                    self._close_turn("speaker_change", ev, out)
                self.cur_spk = spk
                if self.first_t is None:
                    self.first_t = ev.t_recv
                if self.turn_start is None and ws:
                    self.turn_start = ws[0].start
                self.last_final_t = ev.t_recv
                self.buf.append(piece)
                if ws:
                    self.audio_end = ws[-1].end
                if self.on_piece is not None:
                    self.on_piece(spk, piece)
            if self.speculate:
                self._speculate(" ".join(self.buf), ev, out)
            return
        if ev.kind == "utterance_end":
            self._close_turn("silence", ev, out)

    def _speculate(self, text: str, ev: STTEvent, out: list[Utterance]) -> None:
        if len(self.spec_keys) >= _MAX_SPECULATIONS:
            return
        upto, why, acc = None, None, []
        for sent in sentences(text):
            acc.append(sent)
            if sent.rstrip()[-1:] in _TERMINAL:
                r = self.classify(sent)
                if r is not None:
                    upto, why = " ".join(acc), r
        if upto is None:
            return
        key = normalize_question(upto)
        if key in self.spec_keys:
            return
        self.spec_keys.add(key)
        # n=0: an early ask is not a decision of the pipeline and takes no place in the log's
        # numbering; the suggester keys it by its text
        out.append(Utterance(
            n=0, speaker=self.cur_spk, trigger=why, text=upto, segments=len(self.buf),
            t_first_final=self.first_t or ev.t_recv, t_last_final=self.last_final_t or ev.t_recv,
            t_flush=ev.t_recv, gap_ms=0, speech_to_text_ms=None, source="speculative",
            stream=self.stream, speculative=True,
        ))

    def _close_turn(self, default: str, ev: STTEvent, out: list[Utterance]) -> None:
        if not self.buf:
            return
        text, start, end = " ".join(self.buf), self.turn_start, self.audio_end
        why = None
        for sent in sentences(text):
            r = self.classify(sent)
            if r is not None:
                why = r
        prev = self.last_turn
        first = sentences(text)[:1]
        opens_new = bool(first) and self.classify(first[0]) is not None
        if (self.reopen and prev is not None and start is not None
                and start - prev["end"] <= REOPEN_S and not opens_new
                and _REOPEN_MIN_WORDS <= len(text.split()) <= _REOPEN_MAX_WORDS):
            # The interviewer paused past the 1 s that closes a turn and went on with the same
            # question. Its card is replaced rather than a second one opened.
            revised = f"{prev['text']} {text}"
            self.n += 1
            q_lat = None if ev.t_rel is None or end is None else round((ev.t_rel - end) * 1000)
            out.append(Utterance(
                n=self.n, speaker=self.cur_spk, trigger=prev["why"], text=revised,
                segments=len(self.buf), t_first_final=self.first_t or ev.t_recv,
                t_last_final=self.last_final_t or ev.t_recv, t_flush=ev.t_recv,
                gap_ms=round((ev.t_recv - (self.last_final_t or ev.t_recv)) * 1000),
                speech_to_text_ms=q_lat, source="turn", question_end_to_fire_ms=q_lat,
                stream=self.stream, revises=prev["n"],
            ))
            self.last_turn = {**prev, "text": revised, "end": end}
            self._reset()
            return
        self._flush(why or default, ev, out, source="turn", q_end=end if why else None)
        self.last_turn = ({"n": out[-1].n, "text": text, "end": end, "why": why}
                          if why and out else None)

    # ---- sentence mode (the default until 2026-09-10; kept for A/B replays) -------------------

    def _feed_interim(self, ev: STTEvent, out: list[Utterance]) -> None:
        if self._check_revision(ev, out):
            return
        words = self._unconsumed(ev.words)
        if self._resolve_hold(words, ev, out):
            return
        for spk, run in split_words_by_speaker(words):
            for s in sentences_from_words(run):
                if not s.complete or self._is_continuation(s):
                    continue
                why = self.classify(s.text)
                if why is None or self._already_fired(s.end):
                    continue
                if self.settle_interims and not s.text.rstrip().endswith("?"):
                    # The sentence is complete but the speaker may not be: "Tell me about
                    # yourself." is a whole sentence and half a question when the rest is "and
                    # your experience with Flutter". Wait one event to see whether words follow
                    # it; the answer is worth more than the second it costs. A sentence that
                    # ends in "?" is not held: nothing follows a question mark that changes it.
                    self.hold = {"end": s.end, "why": why, "spk": spk}
                    return
                self._fire_interim(spk, run, s.end, why, ev, out, start=s.start)
                return   # anything after this sentence comes back in the next interim or the final

    def _fire_interim(self, spk, run, end: float, why: str, ev: STTEvent,
                      out: list[Utterance], start: Optional[float] = None) -> None:
        if self.buf and self.cur_spk is not None and spk is not None and spk != self.cur_spk:
            self._flush("speaker_change", ev, out)
        # the start of this sentence may already be sitting in the buffer as text from an earlier
        # final ("Chris, can you tell me about a challenge"), where no word of this interim covers it
        head = _unfinished_tail(" ".join(self.buf))
        self.cur_spk = spk
        if self.first_t is None:
            self.first_t = ev.t_recv
        self.last_final_t = ev.t_recv
        # by START, not by end: the end of an interim's last word is provisional and moves in
        # the next interim, which silently cut "Flutter." off the question in rehearsal3
        self.buf.append(" ".join(w.text for w in run if w.start < end))
        self.audio_end = end
        self._flush(why, ev, out, source="interim", q_end=end)
        self._mark_fired(end)
        if start is not None and out:
            words = sum(1 for w in run if start - _SAME_START_S <= w.start < end)
            self.last_q = {"n": out[-1].n, "start": start, "end": end, "spk": spk,
                           "words": words, "why": why, "head": head}
            # seeded with the fired sentence: when the head of the question was finalized before
            # the fire, no later final carries it, and interview7's revision came out as "or a
            # conflict that you've faced..." with "Chris, can you tell me about a challenge" gone
            self.rev_words = [w for w in run if start - _SAME_START_S <= w.start < end]
            self.rev_final: set = set()       # starts of words confirmed by a final

    def _check_revision(self, ev: STTEvent, out: list[Utterance]) -> bool:
        """The question that fired turned out to be longer than it looked.

        Deepgram's interim punctuation is a guess it takes back. rehearsal4 shows the interim
        "how you handled state management?" one second before "handled state management with
        provider and get x and why you chose the". Holding every question until something follows
        it would have caught that, and was measured to lose 12 of 26 questions on interview7, so
        the fast fire stays and is corrected instead: a sentence that begins at the same audio
        position as the last fired question (word starts are stable) and ends later is emitted
        as a revision of it, and the suggester re-asks under the same number. It waits until the
        longer sentence is complete, so a question that is still growing is not re-asked on
        every word.
        """
        q = self.last_q
        if q is None or not ev.words:
            return False
        for spk, run in split_words_by_speaker(ev.words):
            for sent in sentences_from_words(run):
                if abs(sent.start - q["start"]) > _SAME_START_S:
                    continue
                grown = sum(1 for w in run if sent.start - _SAME_START_S <= w.start <= sent.end)
                if grown < q["words"] + _GREW_WORDS or not sent.complete:
                    return False
                why = self.classify(sent.text)
                if why is None:
                    return False
                self._emit_revision(q, spk, why, sent.text, sent.end, ev, out)
                self.last_q = {**q, "end": sent.end, "spk": spk, "words": grown, "why": why}
                return True
        return False

    def _emit_revision(self, q: dict, spk, why: str, text: str, end: float, ev: STTEvent,
                       out: list[Utterance]) -> None:
        if q.get("head"):
            text = f"{q['head']} {text}"
        self.n += 1
        q_lat = None if ev.t_rel is None else round((ev.t_rel - end) * 1000)
        out.append(Utterance(
            n=self.n, speaker=spk, trigger=why, text=text, segments=1,
            t_first_final=ev.t_recv, t_last_final=ev.t_recv, t_flush=ev.t_recv, gap_ms=0,
            speech_to_text_ms=q_lat, source=ev.kind, question_end_to_fire_ms=q_lat,
            stream=self.stream, revises=q["n"],
        ))
        self._mark_fired(end)
        self.last_fire_end = end

    def _track_growth(self, ev: STTEvent, out: list[Utterance], closing: bool = False) -> None:
        """Follow a fired question into the segments that finish it.

        rehearsal4, one question in three forced finals: "For that chat app, can you walk me
        through how you" / "handled state management with provider and get x" / "and why you
        chose those tools." The interim had already fired on "how you handled state
        management?". The later pieces start lowercase soon after a fire, which is exactly what
        _is_continuation calls a tail and suppresses, so the half of the question that asked
        *why* was thrown away. Here the final words from where the question began are collected
        across segments until the sentence ends (or the speaker stops), and if it came out at
        least _GREW_WORDS longer than what fired, it is emitted as a revision of that question.
        """
        q = self.last_q
        if q is None:
            return
        # No speaker filter. On loopback audio the diarizer does not hold a voice steady between
        # an interim and its final: in rehearsal4 the fire was speaker 0 and every final word of
        # the same question came back speaker 1, so filtering on it collected nothing and the
        # half that asked "why" was flushed as silence. The user's own microphone runs in a
        # separate Pipeline, so continuity on the audio clock is the identity that holds.
        new = sorted((w for w in (ev.words or []) if w.start >= q["start"] - _SAME_START_S),
                     key=lambda w: w.start)
        prev_end = max((w.end for w in self.rev_words), default=q["end"])
        if new and new[0].start - prev_end > CONTINUATION_WINDOW_S:
            self.last_q, self.rev_words = None, []       # a pause that long is a new turn
            return
        if new and ev.kind == "final":
            # A final is authoritative for the stretch of audio it covers, so every earlier guess
            # inside that stretch goes. Matching word by word left an interim "job?" beside the
            # final's "job", and interview7's revision read "to the job job? I think there's a
            # risk", running straight on into the candidate's answer.
            lo, hi = new[0].start - 0.15, max(w.end for w in new) + 0.15
            self.rev_words = [w for w in self.rev_words if not (lo <= w.start <= hi)]
            self.rev_words = sorted(self.rev_words + new, key=lambda w: w.start)
            self.rev_final |= {round(w.start, 2) for w in new}
        if len(self.rev_words) > _GROWTH_CAP_WORDS:
            self.last_q, self.rev_words = None, []
            return
        # only a final's punctuation ends the sentence: the seeded interim words still carry the
        # "?" that was a guess, and cutting there would call the question finished at once
        cut = next((i for i, w in enumerate(self.rev_words)
                    if w.text[-1:] in _TERMINAL and round(w.start, 2) in self.rev_final), None)
        # A final can drop the question mark ("to the job" then "I think there's a risk"), and
        # then punctuation alone lets the growth run on into the answer. A real pause after the
        # point the question had reached when it fired ends it just as well.
        pause = next((i for i, (a, b) in enumerate(zip(self.rev_words, self.rev_words[1:]))
                      if round(a.start, 2) in self.rev_final and round(b.start, 2) in self.rev_final
                      and a.end >= q["end"] - 0.5 and b.start - a.end >= _TURN_PAUSE_S), None)
        if pause is not None and (cut is None or pause < cut):
            cut = pause
        if cut is None and not (ev.endpointed or closing):
            return                                         # the next segment may finish it
        words = self.rev_words if cut is None else self.rev_words[: cut + 1]
        self.last_q, self.rev_words = None, []
        if not words or len(words) < q["words"] + _GREW_WORDS:
            return                                         # it ended about where it fired
        text = " ".join(w.text for w in words)
        why = self.classify(text) or q["why"]
        self._emit_revision(q, q["spk"], why, text, words[-1].end, ev, out)

    def _resolve_hold(self, words: list[Word], ev: STTEvent, out: list[Utterance]) -> bool:
        """Decide what happened to a question that was complete but might have been unfinished.

        Nothing after it in this event means the speaker stopped: fire it. Anything after it
        means they carried on, so the held sentence is forgotten and this event's own detection
        rules on the longer text - which is the point, because the important half of a question
        is often the second half.
        """
        if self.hold is None:
            return False
        end = self.hold["end"]
        if any(w.start > end for w in words):
            self.hold = None
            return False
        run = [w for w in words if w.start < end]
        why, spk = self.hold["why"], self.hold["spk"]
        self.hold = None
        if not run or self._already_fired(end):
            return False
        self._fire_interim(spk, run, end, why, ev, out)
        return True

    def _feed_final(self, ev: STTEvent, out: list[Utterance]) -> None:
        self.hold = None      # the final has the whole segment; it decides, not a held guess
        self._track_growth(ev, out)
        if ev.words:
            words = self._unconsumed(ev.words)
            if not words:
                return   # the whole segment was already emitted from interims
            pieces = [(spk, " ".join(w.text for w in ws), ws[-1].end, ws)
                      for spk, ws in split_words_by_speaker(words)]
        else:
            pieces = [(None, ev.text, None, [])]
        for spk, piece, end, ws in pieces:
            if self.cur_spk is not None and spk is not None and spk != self.cur_spk:
                self._flush("speaker_change", ev, out)
            self.cur_spk = spk
            if self.first_t is None:
                self.first_t = ev.t_recv
            self.last_final_t = ev.t_recv
            self.buf.append(piece)
            if end is not None:
                self.audio_end = end
            if self.on_piece is not None:
                self.on_piece(spk, piece)
            why, q_end = self._classify_piece(piece, ws, ev.endpointed)
            if why is not None:
                self._flush(why, ev, out, source="final", q_end=q_end)

    def _classify_piece(self, piece: str, words: list[Word],
                        endpointed: bool = True) -> tuple[Optional[str], Optional[float]]:
        """Reason and audio end of the LAST matching sentence (same rule as detector.detect).

        `endpointed` is false for a final the provider forced after a stretch of continuous
        speech. Such a final can end mid-sentence - rehearsal2 was cut at "including one project
        that you're" - so an unfinished tail is left in the buffer for the next segment to
        complete rather than answered as if it were the whole question.
        """
        why, q_end = None, None
        if words:
            for s in sentences_from_words(words):
                if self._is_continuation(s) or (not s.complete and not endpointed):
                    continue
                r = self.classify(s.text)
                if r is not None and not self._already_fired(s.end):
                    why, q_end = r, s.end
        else:
            for sent in sentences(piece):
                r = self.classify(sent)
                if r is not None:
                    why = r
        if why is None and len(self.buf) > 1 and words:
            why, q_end = self._classify_across_segments(words, endpointed)
        return why, q_end

    def _classify_across_segments(self, words: list[Word],
                                  endpointed: bool = True) -> tuple[Optional[str], Optional[float]]:
        """A question the provider cut in half.

        Deepgram forces a final after a stretch of continuous speech, wherever it happens to be:
        rehearsal3 has "Sure thing. Tell me about yourself," closed as one segment and "and your
        experience with Flutter, including one project you're proud of." opened as the next.
        Neither half is a question on its own, so segment-by-segment detection sees nothing and
        the question is lost outright. Judging the unfired buffer as one text finds it. The
        buffer only ever holds what has not fired yet, since a fire empties it.
        """
        joined = " ".join(self.buf)
        end = words[-1].end
        if self._already_fired(end):
            return None, None
        complete = joined.rstrip()[-1:] in _TERMINAL
        if not complete and not endpointed:
            return None, None
        for sent in sentences(joined):
            r = self.classify(sent)
            if r is not None:
                return r, end
        return None, None

    def _flush(self, trigger: str, ev: STTEvent, out: list[Utterance],
               source: str = "final", q_end: Optional[float] = None) -> None:
        if not self.buf:
            return
        self.n += 1
        lat = None
        if ev.t_rel is not None and self.audio_end is not None:
            lat = round((ev.t_rel - self.audio_end) * 1000)
        q_lat = None
        if q_end is not None:
            self.last_fire_end = q_end
            if ev.t_rel is not None:
                q_lat = round((ev.t_rel - q_end) * 1000)
        out.append(Utterance(
            n=self.n, speaker=self.cur_spk, trigger=trigger, text=" ".join(self.buf),
            segments=len(self.buf), t_first_final=self.first_t, t_last_final=self.last_final_t,
            t_flush=ev.t_recv, gap_ms=round((ev.t_recv - self.last_final_t) * 1000),
            speech_to_text_ms=lat, source=source, question_end_to_fire_ms=q_lat, stream=self.stream,
            audio_start=self.turn_start, audio_end=self.audio_end,
        ))
        self._reset()
