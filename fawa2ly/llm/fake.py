"""
FakeLLM: canned bullets streamed word by word with configurable delays.
Lets the pipeline, telemetry, replay and tests run with no key and no network.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import AsyncIterator, Optional

from ..limits import STYLE_MARK
from .base import SuggestionEvent, SuggestionProvider, SuggestionRequest

# Spoken sentences, because that is what the product shows now (prompt v2.0, 2026-09-10). The
# previous canned bullets were keyword-era ("biggest strength: attention to detail") and the
# guardrail's label_prefix rule dropped two of the three, so every offline run and every UI
# screenshot came out with a single line. A fixture that the real filter rejects is worse than
# no fixture: it makes healthy code look broken.
DEFAULT_BULLETS = [
    "I moved to nights last year and it stopped working for me",
    "In my last job I was made a trainer after six months",
    "The shift pattern was the reason, not the work itself",
    "I would tie that back to what this role needs day to day",
]

# What the fake answers with on the Dynamic profile (roadmap item 27), one shape per question in
# turn, so the overlay can be worked on against all three without a key: each shape renders
# differently (the arrows, the badge, the line lengths) and a fixture that only ever picks one
# would leave two of them never seen until a real call.
SHAPE_BULLETS = {
    "full": DEFAULT_BULLETS,
    "keywords": ["Nights: stopped working for me", "Trainer after six months", "Shift pattern, not the work"],
    "direction": ["Open with the shift pattern, not the job", "Name the trainer promotion as proof",
                  "Land on what this role needs daily"],
}
SHAPE_ORDER = ("full", "keywords", "direction")


class FakeLLM(SuggestionProvider):
    name = "fake"

    def __init__(self, bullets: Optional[list[str]] = None, first_token_delay: float = 0.0,
                 token_delay: float = 0.0, model: str = "fake-1", error: Optional[str] = None,
                 finish_reason: str = "stop",
                 shape: Optional[str] = None):
        self.given_bullets = bullets
        self.bullets = DEFAULT_BULLETS if bullets is None else bullets
        self.first_token_delay = first_token_delay
        self.token_delay = token_delay
        self.model = model
        self.error = error
        self.finish_reason = finish_reason
        # Dynamic (roadmap item 27): when the prompt asks the model to name the shape it chose, the
        # fake names one and answers in it, taking the next shape each time it is asked. Set
        # `shape` to pin it to one. `bullets` given explicitly still wins: a test says what it means.
        self.shape = shape
        self.shapes_answered: list[str] = []
        self.requests: list[SuggestionRequest] = []   # what the pipeline asked, for assertions

    async def suggest(self, req: SuggestionRequest) -> AsyncIterator[SuggestionEvent]:
        self.requests.append(req)
        if self.error is not None:
            yield SuggestionEvent("error", time.time(), self.error, model=self.model)
            return
        bullets = self.bullets
        if self.given_bullets is None and STYLE_MARK in req.system:
            shape = self.shape or SHAPE_ORDER[len(self.shapes_answered) % len(SHAPE_ORDER)]
            self.shapes_answered.append(shape)
            bullets = [f"{STYLE_MARK} {shape}", *SHAPE_BULLETS[shape]]
        text = "\n".join(f"- {b}" for b in bullets)
        tokens = re.findall(r"\S+\s*", text)
        if self.first_token_delay:
            await asyncio.sleep(self.first_token_delay)
        for i, tok in enumerate(tokens):
            if i and self.token_delay:
                await asyncio.sleep(self.token_delay)
            yield SuggestionEvent("first_token" if i == 0 else "token", time.time(), tok, model=self.model)
        yield SuggestionEvent("done", time.time(), text,
                              usage={"input_tokens": len(req.system.split()) + len(req.question.split()),
                                     "output_tokens": len(tokens)},
                              model=self.model, finish_reason=self.finish_reason)
