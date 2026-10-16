"""
STT provider interface.

Every provider turns its own wire format into STTEvent objects. Nothing
downstream (pipeline, detector, telemetry) ever sees provider JSON, so swapping
Deepgram for another vendor is one new file in this package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional


@dataclass
class Word:
    text: str                      # punctuated form when the provider gives one
    start: float                   # seconds on the audio clock (0 = first audio byte sent)
    end: float
    speaker: Optional[int] = None  # diarization label, None if the provider gave none


@dataclass
class STTEvent:
    kind: str                      # "interim" | "final" | "utterance_end" | "error"
    t_recv: float                  # wall clock (time.time()) when the message arrived
    t_rel: Optional[float]         # seconds since first audio byte sent, same clock as Word.start/end
    text: str = ""
    words: list[Word] = field(default_factory=list)
    raw: Optional[dict] = None     # provider message, for debugging only
    endpointed: bool = False       # the provider heard the speaker stop (Deepgram: speech_final).
                                   # A final without it was forced by a length limit and can cut a
                                   # sentence, and a question, in half.

    @property
    def audio_end(self) -> Optional[float]:
        """Audio-clock time when the last word of this event ended."""
        return self.words[-1].end if self.words else None


class STTProvider(ABC):
    @abstractmethod
    async def send_audio(self, chunk: bytes, t_capture: Optional[float] = None) -> None:
        """Send 16 kHz mono int16 PCM. t_capture: wall clock of the chunk's first sample."""

    @abstractmethod
    def events(self) -> AsyncIterator[STTEvent]:
        """Async iterator over normalized events, in arrival order."""

    async def close(self) -> None:
        return None
