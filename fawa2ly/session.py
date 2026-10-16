"""
Session loop shared by live capture and (in spirit) replay: STT events -> Pipeline
-> printed lines + utterance log. Lives here, not in live_transcribe.py, so it can
be unit-tested without an audio device.

Bug this file exists because of (2026-09-08): the live receiver printed interim
results but never passed them to the Pipeline, so the early "fire on interim"
path only ever ran in replay. interview7/8 were recorded with finals-only
firing while the replay of the same audio fired on interims. The parity check
(live log vs replay --no-interim) is what caught it.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Callable, Optional, TextIO

from .console import CLEAR
from .pipeline import Pipeline
from .replay import format_utterance
from .stt.base import STTEvent


async def consume(events: AsyncIterator[STTEvent], pipe: Pipeline, log: TextIO, tag: str,
                  show: Callable = print, suggester=None,
                  spawn: Optional[Callable] = None,
                  on_utterance: Optional[Callable] = None,
                  on_interim: Optional[Callable] = None) -> int:
    """Feed EVERY event to the pipeline (interims included), print and log utterances.

    With a suggester, each utterance is handed to it as a background task: the model call must
    never delay the next STT event, or the transcript falls behind the conversation.

    `show` writes console text; `on_utterance` and `on_interim` hand the same moments to a UI as
    objects instead of formatted lines (fawa2ly.engine). They are additions, not a second path:
    a front end that wires its own STT loop is how the interim bug happened.
    Returns the number of utterances written."""
    n = 0
    spawn = spawn or asyncio.create_task
    async for ev in events:
        if ev.kind == "error":
            show(f"[stt error {tag}] {ev.text}")
            continue
        if ev.kind == "interim" and tag == "other":
            show(f"\r   {ev.text[:110]}", end="", flush=True)
            if on_interim is not None:
                on_interim(ev.text)
        for u in pipe.feed(ev):
            if u.speculative:
                # An early ask while the turn is still open. The suggester may use it, but the
                # pipeline has not decided anything, so it is neither printed nor logged, and the
                # front end hears about the question only when the turn closes.
                if suggester is not None:
                    spawn(suggester.handle(u))
                continue
            show(CLEAR + format_utterance(u))
            log.write(json.dumps(u.to_json(), ensure_ascii=False) + "\n")
            log.flush()
            n += 1
            if on_utterance is not None:
                on_utterance(u)
            if suggester is not None:
                spawn(suggester.handle(u))
    return n
