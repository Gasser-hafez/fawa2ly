"""
Question -> suggestion.

`Suggester` watches the utterances the pipeline emits. Every utterance becomes conversation
history; the ones the detector fired on become a request to the suggestion provider, and the
answer is written to `<session>.assist.jsonl` as an `Assist` record.

**The product metric** is `question_end_to_first_token_ms`: from the moment the speaker finished
asking to the first line on screen (section 0, target p50 <= 1.2 s). It is deliberately the
sum of two intervals measured on different clocks:

    question_end_to_fire_ms   audio clock, from the end of the question to the detector firing
  + ttft_ms                   wall clock, from sending the request to the first token

Measuring it as one wall-clock span would be wrong in replay, where the transcript's timestamps
are historical but the model call happens now. Summing two independently valid intervals gives
the same number live and replayed, which is what makes offline tuning meaningful.

Only the other party's speech is answered. The user's own microphone (`stream="user"`) is
recorded as history and never triggers a suggestion.

"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional, TextIO

from .limits import DEFAULT_MAX_TOKENS, RECAP_WORDS
from .pipeline import normalize_question
from .llm.prompts import ACTION_LEAD, ACTIONS, DYNAMIC, more_request, shorter_request
from .context import ContextBuilder, filter_bullets
from .context.guardrail import style_choice
from .llm.base import Suggestion, SuggestionProvider, collect
from .pipeline import Utterance

# Triggers that mean "the detector decided this was a question", as opposed to a flush caused by
# silence or by the other speaker starting.
QUESTION_TRIGGERS = ("question_mark", "request_opener", "wh_word", "yes_no")


@dataclass
class Assist:
    n: int
    question: str
    bullets: list[str]
    dropped: list[dict] = field(default_factory=list)
    trigger: str = ""
    source: str = ""
    stream: str = "other"
    speaker: Optional[int] = None
    t_fire: float = 0.0                 # session clock of the utterance that triggered this
    t_request: float = 0.0              # wall clock when the request was sent
    t_first_token: Optional[float] = None
    t_done: float = 0.0
    model: str = ""
    provider: str = ""
    usage: Optional[dict] = None
    error: Optional[str] = None
    truncated: bool = False
    ttft_ms: Optional[int] = None
    question_end_to_fire_ms: Optional[int] = None
    question_end_to_first_token_ms: Optional[int] = None
    style: str = ""                     # the shape this answer took: full | keywords | direction
    chose_style: bool = False           # on Dynamic, whether the model said which (roadmap item 27)
    more: bool = False                  # lines added by the More button to an answer already shown

    def to_json(self) -> dict:
        return asdict(self)


def is_question(u: Utterance) -> bool:
    return u.trigger in QUESTION_TRIGGERS and u.stream == "other"


def speaker_label(u: Utterance) -> str:
    return "you" if u.stream == "user" else "them"


class Suggester:
    """Feed it every utterance; it answers the questions.

    on_result is called with each finished Assist, for the UI or the console.
    """

    def __init__(self, provider: SuggestionProvider, builder: Optional[ContextBuilder] = None,
                 log: Optional[TextIO] = None,
                 on_result: Optional[Callable[[Assist], None]] = None,
                 on_line: Optional[Callable[[int, str], None]] = None,
                 on_question: Optional[Callable[[int, str], None]] = None,
                 on_style: Optional[Callable[[int, str], None]] = None,
                 max_history: int = 6, max_tokens: int = DEFAULT_MAX_TOKENS):
        self.provider = provider
        self.builder = builder or ContextBuilder()
        self.log = log
        self.on_result = on_result
        self.on_line = on_line
        self.on_question = on_question
        self.on_style = on_style
        self.max_history = max_history
        self.max_tokens = max_tokens
        self.history: list[tuple[str, str]] = []
        self.said: list[tuple[str, str]] = []    # the call so far, for a recap; bounded by RECAP_WORDS
        self.said_words = 0
        self.n = 0
        self.asked: dict[int, tuple] = {}    # n -> (utterance, question, history), for retry
        self.by_utterance: dict[int, int] = {}   # Utterance.n -> question n, to route revisions
        self.generation: dict[int, int] = {}     # question n -> latest request; older ones go quiet
        self.spec: dict[str, dict] = {}          # turn mode: early asks, keyed by normalized text
        self.shown_lines: dict[int, list[str]] = {}   # question n -> lines on screen, for More
        self.action_cards: dict[int, str] = {}    # question n -> the on-demand action that opened it (item 17)
        # On the Dynamic profile the model picks a shape per answer (roadmap item 27) and says which
        # on its first line. Kept per question, because everything after that first line is judged by
        # that shape's rules: the lines as they stream, the finished record, and More's lines later,
        # which have to keep the shape of the card they are added to.
        self.answer_style: dict[int, str] = {}

    @property
    def dynamic(self) -> bool:
        """Whether the model chooses the shape of each answer (roadmap item 27)."""
        return self.builder.style == DYNAMIC

    def _style_for(self, n: Optional[int]) -> str:
        """The shape to judge this question's lines by: the one the model chose, else the profile.
        On Dynamic before the marker arrives that is "dynamic" itself, which the guardrail treats as
        the most permissive of the three rather than emptying an answer it cannot place."""
        chosen = self.answer_style.get(n) if n is not None else None
        return chosen or self.builder.style

    def _took_style(self, n: Optional[int], text: str) -> bool:
        """True when this line was the model naming the shape, so it is recorded and never shown.

        The first one wins. A model that names a second shape further down its reply would otherwise
        re-place an answer whose earlier lines were already filtered by the first shape and already
        on screen, and the card would change under the reader: the arrows appearing partway down, and
        a line kept under one word limit re-judged by another. A later marker is still swallowed, it
        just decides nothing. A retry clears the shape, so the model is free to place it again there.
        """
        if not self.dynamic or style_choice(text) is None:
            return False
        chosen = style_choice(text)
        if n is not None and n not in self.answer_style:
            self.answer_style[n] = chosen
            if self.on_style is not None:
                self.on_style(n, chosen)
        return True

    def observe(self, u: Utterance) -> None:
        """Record an utterance as conversation history, question or not."""
        text = u.text.strip()
        if text:
            self.history.append((speaker_label(u), text))
            del self.history[: -self.max_history * 2]   # keep a little slack, build() trims
            # A recap is of the call, not of the last few turns (roadmap item 17), so the turns are
            # kept for it too - bounded by words, because a turn is anything from "yes" to a
            # paragraph, and an hour of talk in the prompt is neither affordable nor readable.
            self.said.append((speaker_label(u), text))
            self.said_words += len(text.split())
            while self.said_words > RECAP_WORDS and len(self.said) > 1:
                self.said_words -= len(self.said.pop(0)[1].split())

    async def handle(self, u: Utterance) -> Optional[Assist]:
        """History first, then suggest if this was a question. None when it was not."""
        if u.speculative:
            return await self._speculate(u)
        if u.revises is not None and u.revises in self.by_utterance:
            return await self._revise(u)
        question = u.text.strip()
        was_question = is_question(u) and bool(question)
        history = list(self.history) if was_question else []
        self.observe(u)
        if not was_question:
            return None
        self.n += 1
        if self.on_question is not None:
            # Announced before the model is even asked, so a UI can put the question on screen
            # with the whole wait in front of it rather than after it. It also carries the only
            # numbering that matters downstream: Utterance.n counts every utterance, this counts
            # questions, and the `line` and `assist` events use this one.
            self.on_question(self.n, question)
        self.asked[self.n] = (u, question, history)
        self.by_utterance[u.n] = self.n
        hit = self.spec.pop(normalize_question(question), None)
        self.spec.clear()          # the other early asks were for fragments of this same turn
        if hit is not None:
            return self._show_speculation(hit, u, question, self.n)
        return await self._ask(self.n, u, question, history)

    async def _speculate(self, u: Utterance) -> Optional[Assist]:
        """Ask while the turn is still open, and show nothing yet.

        Turn mode waits for a second of silence before it answers, which is what finally made
        the questions whole, and it costs about that second. A question sentence seen before
        the close is asked here in the background; when the turn closes on exactly that text,
        its lines are already on the way and appear at once. When the turn turns out longer,
        this answer is simply never shown. rehearsal4: 8 of 10 turns closed on the text an early
        ask had already used.
        """
        if not is_question(u) or not u.text.strip():
            return None
        key = normalize_question(u.text)
        if key in self.spec:
            return None
        entry: dict = {"accepted": [], "shown": None, "gen": None, "turn": None,
                       "suggestion": None, "t_request": time.time(), "style": None}
        self.spec[key] = entry
        req = self.builder.build(u.text.strip(), list(self.history), max_tokens=self.max_tokens)

        def capture(text: str) -> None:
            if self.dynamic and style_choice(text) is not None:
                # asked early, so there is no question number to announce it under yet;
                # _show_speculation does that if this answer is the one that gets shown
                entry["style"] = entry["style"] or style_choice(text)      # the first one wins
                return
            kept, _ = filter_bullets(entry["accepted"] + [text], style=entry["style"] or self.builder.style)
            if len(kept) <= len(entry["accepted"]):
                return
            entry["accepted"].append(kept[-1])
            n = entry["shown"]
            if n is not None and self.on_line is not None and self.generation.get(n) == entry["gen"]:
                self.on_line(n, kept[-1])

        s: Suggestion = await collect(self.provider, req, on_line=capture)
        entry["suggestion"] = s
        n = entry["shown"]
        if n is not None and self.generation.get(n) == entry["gen"]:
            # the turn closed on this text while it was still streaming: the record is due now
            turn_u, question, t_shown = entry["turn"]
            return self._record(turn_u, question, s, entry["t_request"], n, t_shown=t_shown)
        return None

    def _show_speculation(self, entry: dict, u: Utterance, question: str, n: int) -> Optional[Assist]:
        gen = self.generation[n] = self.generation.get(n, 0) + 1
        t_shown = time.time()
        entry.update(shown=n, gen=gen, turn=(u, question, t_shown))
        if entry["style"]:                       # the shape it was written in, before its lines
            self.answer_style[n] = entry["style"]
            if self.on_style is not None:
                self.on_style(n, entry["style"])
        if self.on_line is not None:
            for line in entry["accepted"]:
                self.on_line(n, line)
        if entry["suggestion"] is not None:
            return self._record(u, question, entry["suggestion"], entry["t_request"], n, t_shown=t_shown)
        return None      # still streaming: _speculate finishes the record when it completes

    async def _revise(self, u: Utterance) -> Optional[Assist]:
        """The question grew after it fired (Pipeline._check_revision): ask again, same number.

        The first request may still be streaming. It is not cancelled, because a provider that
        has already started charges for it either way; it is made stale instead, so its lines and
        its record never reach the overlay after the corrected question has replaced it.
        """
        n = self.by_utterance[u.revises]
        self.by_utterance[u.n] = n
        _, old_question, history = self.asked[n]
        question = u.text.strip()
        for i in range(len(self.history) - 1, -1, -1):     # keep the fuller text as context
            if self.history[i][1] == old_question:
                self.history[i] = (self.history[i][0], question)
                break
        self.asked[n] = (u, question, history)
        if self.on_question is not None:
            self.on_question(n, question)
        return await self._ask(n, u, question, history)

    async def retry(self, n: int) -> Optional[Assist]:
        """Ask the same question again, under the same number.

        A failed answer is exactly the moment someone wants another try, and the model's failures
        are not deterministic: a timeout or an empty reply usually succeeds on the second attempt.
        The question keeps its number, so the overlay replaces the failed card in place instead of
        opening a second one for the same question.
        """
        saved = self.asked.get(n)
        if saved is None:
            return None
        u, question, history = saved
        if self.on_question is not None:
            self.on_question(n, question)
        return await self._ask(n, u, question, history)

    async def action(self, kind: str, n: Optional[int] = None) -> Optional[Assist]:
        """The overlay's on-demand buttons (roadmap item 17). "say", "recap" and "ask" open a card of
        their own from the conversation so far, numbered like a question so Retry, More and the card
        list work on it; "shorter" redoes card `n` from the lines on it. None for anything else."""
        if kind == "shorter":
            saved, shown = self.asked.get(n), self.shown_lines.get(n)
            if saved is None or not shown:
                return None
            u, question, history = saved
            if self.on_question is not None:
                self.on_question(n, question)
            return await self._ask(n, u, question, history, ask=shorter_request(question, shown))
        if kind not in ACTIONS:
            return None
        self.n += 1
        self.action_cards[self.n] = kind
        # A recap is given the call so far; the others are about where the conversation is now.
        title = ACTIONS[kind][0]
        history = list(self.said) if kind == "recap" else list(self.history)
        u = Utterance(n=0, speaker=None, trigger="action", text=kind, segments=0, t_first_final=0.0,
                      t_last_final=0.0, t_flush=0.0, gap_ms=0, speech_to_text_ms=None, source="action")
        if self.on_question is not None:
            self.on_question(self.n, title)
        self.asked[self.n] = (u, title, history)
        return await self._ask(self.n, u, title, history)

    async def _ask(self, n: int, u: Utterance, question: str,
                   history: list[tuple[str, str]], ask: Optional[str] = None) -> Assist:
        gen = self.generation[n] = self.generation.get(n, 0) + 1
        # asked again: the card starts over, and so does the shape it was given, which a retry is
        # free to place differently (roadmap item 27)
        self.answer_style.pop(n, None)
        action = u.trigger == "action" and not ask
        req = self.builder.build(
            question, history, max_tokens=self.max_tokens,
            # a recap carries the call, not the last few turns
            turns=len(history) if action and u.text == "recap" else None)
        if ask:
            req.question = ask
        elif u.trigger == "action":                 # an action card, first asked or retried
            req.question, req.lead = ACTIONS[u.text][1], ACTION_LEAD
        t_request = time.time()
        s: Suggestion = await collect(self.provider, req, on_line=self._live_line(n, gen))
        if self.generation.get(n) != gen:
            # superseded by a revision or a retry while it was streaming: log it, show nothing
            return self._record(u, question, s, t_request, n, announce=False)
        return self._record(u, question, s, t_request, n)

    async def more(self, n: int) -> Optional[Assist]:
        """Two or three more lines for a question already answered: the overlay's More button.

        The model is shown the lines already on screen and asked to go deeper, and anything that
        repeats one of them is filtered by the same guardrail that decided the first answer. The
        new lines stream onto the same question; the record is marked `more` so the overlay adds
        to the card instead of replacing it.
        """
        saved, shown = self.asked.get(n), self.shown_lines.get(n)
        if saved is None or not shown:
            return None
        u, question, history = saved
        gen = self.generation[n] = self.generation.get(n, 0) + 1
        req = self.builder.build(more_request(question, shown), history, max_tokens=self.max_tokens)
        t_request = time.time()
        accepted = list(shown)
        s: Suggestion = await collect(self.provider, req, on_line=self._adding_line(n, gen, accepted))
        if self.generation.get(n) != gen:
            return None
        self.shown_lines[n] = accepted
        a = self._record(u, question, s, t_request, n, announce=False,
                         bullets=accepted[len(shown):], more=True)
        if self.on_result is not None:
            self.on_result(a)
        return a

    def _adding_line(self, n: int, gen: int, accepted: list[str]) -> Callable[[str], None]:
        """Lines added to an answer already on screen (More), filtered with it."""
        extra = len(accepted) + 3

        def line(text: str) -> None:
            if self.generation.get(n) != gen:
                return
            if self.dynamic and style_choice(text) is not None:
                return          # More keeps the card's shape; a marker on top of it is ignored
            kept, _ = filter_bullets(accepted + [text], max_bullets=extra, style=self._style_for(n))
            if len(kept) > len(accepted):
                accepted.append(kept[-1])
                if self.on_line is not None:
                    self.on_line(n, kept[-1])

        return line

    def _live_line(self, n: int, gen: int = 0) -> Optional[Callable[[str], None]]:
        """Pass each finished line on as it arrives, already filtered.

        The overlay cannot wait for the whole reply, but it also must not show a line the
        guardrail is about to drop. Rather than a second copy of the rules, each candidate is run
        through `filter_bullets` together with the ones already shown: if the set grows, the line
        is new and allowed, and duplicates, over-long lines and the sixth line are refused by the
        same code that decides the final record.
        """
        if self.on_line is None:
            return None
        shown: list[str] = []

        def line(text: str) -> None:
            if gen and self.generation.get(n) != gen:
                return          # a newer request for this question owns the overlay now
            if self._took_style(n, text):
                return          # the first line named the shape; it is not a line to say
            kept, _ = filter_bullets(shown + [text], style=self._style_for(n))
            if len(kept) > len(shown):
                shown.append(kept[-1])
                self.on_line(n, kept[-1])

        return line

    def _record(self, u: Utterance, question: str, s: Suggestion, t_request: float,
                n: int, announce: bool = True, t_shown: Optional[float] = None,
                bullets: Optional[list[str]] = None, **marks) -> Assist:
        if self.dynamic and n not in self.answer_style:
            # The streaming path normally reads the marker; this covers the runs with no on_line
            # (the console, replay, an eval) and a model that put it somewhere other than first.
            # Only while the card has no shape yet: More adds to a card that
            # already has one, and a marker in their reply must not change it underneath the lines
            # already on screen.
            for line in s.bullets:
                if self._took_style(n, line):
                    break
        style = self._style_for(n)
        kept, dropped = filter_bullets(s.bullets, style=style)
        if announce:
            self.shown_lines[n] = kept
        end_to_first = None
        if u.question_end_to_fire_ms is not None and s.ttft_ms is not None:
            end_to_first = u.question_end_to_fire_ms + s.ttft_ms
            if t_shown is not None and s.t_first_token is not None:
                # an early ask started before the turn closed, so its own time to first token
                # overstates the wait: the first line was visible at the close, or when its first
                # token arrived, whichever came later
                end_to_first = u.question_end_to_fire_ms + max(
                    0, round((s.t_first_token - t_shown) * 1000))
        a = Assist(
            n=n, question=question, bullets=kept if bullets is None else bullets,
            dropped=[{"text": d.text, "reason": d.reason} for d in dropped],
            trigger=u.trigger, source=u.source, stream=u.stream, speaker=u.speaker,
            t_fire=u.t_flush, t_request=t_request, t_first_token=s.t_first_token, t_done=s.t_done,
            style=style if style != DYNAMIC else "",       # "" = Dynamic, and the model never said
            chose_style=bool(self.dynamic and self.answer_style.get(n)),
            model=s.model, provider=s.provider, usage=s.usage, error=s.error, truncated=s.truncated,
            ttft_ms=s.ttft_ms, question_end_to_fire_ms=u.question_end_to_fire_ms,
            question_end_to_first_token_ms=end_to_first, **marks,
        )
        if self.log is not None:
            self.log.write(json.dumps(a.to_json(), ensure_ascii=False) + "\n")
            self.log.flush()
        if self.on_result is not None and announce:
            self.on_result(a)
        return a


def _dropped_summary(dropped: list[dict]) -> str:
    counts: dict[str, int] = {}
    for d in dropped:
        counts[d["reason"]] = counts.get(d["reason"], 0) + 1
    return ", ".join(f"{n} {reason}" for reason, n in sorted(counts.items()))


def format_assist(a: Assist) -> str:
    if a.error:
        return f"** [{a.n}] {a.question[:60]} -> ERROR {a.error[:90]}"
    metric = ("unknown" if a.question_end_to_first_token_ms is None
              else f"{a.question_end_to_first_token_ms} ms")
    note = f", dropped {_dropped_summary(a.dropped)}" if a.dropped else ""
    cut = "  TRUNCATED" if a.truncated else ""
    head = (f"** [{a.n}] question end -> first line {metric} "
            f"(fire {a.question_end_to_fire_ms} ms + model {a.ttft_ms} ms){note}{cut}")
    return "\n".join([head] + [f"     * {b}" for b in a.bullets])
