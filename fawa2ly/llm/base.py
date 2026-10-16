"""
SuggestionProvider interface.

A provider turns a SuggestionRequest into a stream of SuggestionEvents, each
wall-clock stamped, so time-to-first-token (TTFT) is measured the same way for
every vendor. collect() folds the stream into one Suggestion record for the
session log and telemetry. Nothing downstream sees vendor SDK objects.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import AsyncIterator, Callable, Optional

from ..limits import DEFAULT_MAX_TOKENS


@dataclass
class SuggestionRequest:
    question: str
    system: str                                             # built by the context builder (M2 step 2)
    history: list[tuple[str, str]] = field(default_factory=list)   # (speaker, text), oldest first
    # Thinking models spend reasoning tokens against this budget and do not report them in
    # usage, so the visible answer can be a fraction of it, and the failure is quiet: the answer
    # simply arrives short. Measured on gemini-3.5-flash-lite with the mobile preset and the real
    # CV (a 1051-token system prompt): at 400 an answer stopped after 13 visible tokens; at 800,
    # 14 of 30 eval answers still truncated and averaged 1.3 bullets against 4.0 for the rest,
    # one of them rendering as a bare "-". A ceiling costs nothing unless it is reached, so it is
    # set well clear of the reasoning these models actually do. Truncation surfaces as
    # Suggestion.truncated, and fawa2ly.relevance counts it.
    max_tokens: int = DEFAULT_MAX_TOKENS
    # The user message as given, for a request that is not a question from a call (the role write-up,
    # 2026-09-11). None means the usual "Recent conversation / They were just asked" message.
    user: Optional[str] = None
    # The line that introduces what was said ("They were just asked:"); None means the usual one.
    lead: Optional[str] = None


@dataclass
class SuggestionEvent:
    kind: str                     # "first_token" | "token" | "done" | "error"
    t: float                      # wall clock (time.time()) when the event was produced
    text: str = ""                # token text; full text on "done" (optional); message on "error"
    usage: Optional[dict] = None  # {"input_tokens", "output_tokens"} on "done" when reported
    model: str = ""
    finish_reason: Optional[str] = None   # provider's stop reason on "done"; "length" = cut off


@dataclass
class Suggestion:
    question: str
    text: str
    bullets: list[str]
    model: str
    provider: str
    t_start: float
    t_first_token: Optional[float]
    t_done: float
    usage: Optional[dict] = None
    error: Optional[str] = None
    finish_reason: Optional[str] = None

    @property
    def truncated(self) -> bool:
        """The model hit max_tokens. The last bullet is probably cut mid-phrase."""
        return self.finish_reason == "length"

    @property
    def ttft_ms(self) -> Optional[int]:
        return None if self.t_first_token is None else round((self.t_first_token - self.t_start) * 1000)

    @property
    def total_ms(self) -> int:
        return round((self.t_done - self.t_start) * 1000)

    def to_json(self) -> dict:
        d = asdict(self)
        d["ttft_ms"] = self.ttft_ms
        d["total_ms"] = self.total_ms
        d["truncated"] = self.truncated
        return d


NEWLINE = chr(10)

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*(.+?)\s*$")


def parse_bullets(text: str) -> list[str]:
    """'- a\n- b' -> ['a', 'b']. Lines without a bullet marker count as bullets too, so a
    model that ignores the format still yields something.

    Blank lines are dropped, and so are lines that are nothing but a marker or punctuation: a
    reply cut off by max_tokens ends in a dangling "-", which reached the overlay as a bullet
    reading "-" (2026-09-09).
    """
    out = []
    for line in text.splitlines():
        m = _BULLET.match(line)
        item = (m.group(1) if m else line).strip()
        if item and any(ch.isalnum() for ch in item):
            out.append(item)
    return out


class SuggestionProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def suggest(self, req: SuggestionRequest) -> AsyncIterator[SuggestionEvent]:
        """Yield first_token / token* / done, or error. Must never raise."""

    async def close(self) -> None:
        return None


async def collect(provider: SuggestionProvider, req: SuggestionRequest,
                  on_line: Optional[Callable[[str], None]] = None) -> Suggestion:
    """Fold the stream into one Suggestion.

    `on_line` is called with each bullet as soon as the line it sits on is complete, which is what
    an overlay needs: waiting for the whole reply means showing nothing for as long as the
    provider takes, and providers have measured 5-130 s. The parsing
    is the same `parse_bullets` the finished record uses, so a line cannot appear on screen in one
    shape and in the log in another.
    """
    t_start = time.time()
    parts: list[str] = []
    t_first = None
    t_done = t_start
    usage = None
    error = None
    finish = None
    model = provider.model
    lines_emitted = 0

    def emit_ready(final: bool = False) -> None:
        """Hand over every line that is finished (all but the last, which may still be growing)."""
        nonlocal lines_emitted
        if on_line is None:
            return
        rows = "".join(parts).split(NEWLINE)
        done_rows = rows if final else rows[:-1]
        while lines_emitted < len(done_rows):
            parsed = parse_bullets(done_rows[lines_emitted])
            lines_emitted += 1
            if parsed:
                on_line(parsed[0])

    async for ev in provider.suggest(req):
        if ev.model:
            model = ev.model
        if ev.kind == "first_token":
            t_first = ev.t
            parts.append(ev.text)
        elif ev.kind == "token":
            parts.append(ev.text)
        if ev.kind in ("first_token", "token"):
            emit_ready()
        elif ev.kind == "done":
            t_done = ev.t
            usage = ev.usage
            finish = ev.finish_reason
            if ev.text and not parts:
                parts.append(ev.text)
        elif ev.kind == "error":
            t_done = ev.t
            error = ev.text
    emit_ready(final=True)
    text = "".join(parts).strip()
    return Suggestion(question=req.question, text=text, bullets=parse_bullets(text), model=model,
                      provider=provider.name, t_start=t_start, t_first_token=t_first, t_done=t_done,
                      usage=usage, error=error, finish_reason=finish)
