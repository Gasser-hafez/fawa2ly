"""
FakeSTT: replays a raw Deepgram log written by DeepgramSTT(raw_log=...).

No network, no credits. Used by tests, by `python -m fawa2ly.replay`, and by
the detector precision/recall eval (M1 DoD). Events carry the original t_recv
and t_rel, so latency numbers computed on a replay equal the live ones.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Optional

from .base import STTEvent, STTProvider
from .deepgram import parse_message


class FakeSTT(STTProvider):
    def __init__(self, path, realtime: bool = False):
        """realtime=True sleeps between messages to reproduce the original pacing."""
        self.path = path
        self.realtime = realtime

    async def send_audio(self, chunk: bytes, t_capture: Optional[float] = None) -> None:
        return None  # audio is irrelevant; the log already holds the provider's answers

    async def events(self) -> AsyncIterator[STTEvent]:
        prev_t = None
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                t_recv = rec["t_recv"]
                if self.realtime and prev_t is not None:
                    await asyncio.sleep(max(0.0, t_recv - prev_t))
                prev_t = t_recv
                ev = parse_message(rec["msg"], t_recv, rec.get("t_rel"))
                if ev is not None:
                    yield ev
