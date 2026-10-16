"""
Saying a role into the microphone instead of typing it (2026-09-11, maintainer's request).

The settings window starts it, the words come back as they are heard, and the user can edit the
text before the model writes the role up. It uses the microphone chosen in Settings and the same
Deepgram connection a call does, and it stops by itself after a minute so a forgotten recording
does not keep the microphone open. Nothing is stored: the text goes to the window that asked.

Events sent to that window:
    {"type": "dictation", "state": "listening"}
    {"type": "dictation", "state": "text", "text": "..."}     every change: finals plus the part still being heard
    {"type": "dictation", "state": "done", "text": "..."}     the final text
    {"type": "dictation", "state": "error", "text": "..."}
"""

from __future__ import annotations

import asyncio
import os
from typing import Callable, Optional

MAX_SECONDS = 60.0


class DictationText:
    """Finals are kept; the interim after them is replaced as it grows."""

    def __init__(self):
        self.finals: list[str] = []
        self.interim = ""

    def feed(self, kind: str, text: str) -> bool:
        """Take one STT event; True when the visible text changed."""
        text = (text or "").strip()
        if kind == "final":
            if text:
                self.finals.append(text)
            changed = bool(text) or bool(self.interim)
            self.interim = ""
            return changed
        if kind == "interim" and text != self.interim:
            self.interim = text
            return True
        return False

    @property
    def value(self) -> str:
        return " ".join(self.finals + ([self.interim] if self.interim else []))


class Dictation:
    def __init__(self, on_event: Callable[[dict], None], lang: str = "en", mic: str = "",
                 seconds: float = MAX_SECONDS):
        self.on_event = on_event
        self.lang = lang
        self.mic = mic
        self.seconds = seconds
        self.text = DictationText()
        self.running = False
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from .engine import find_input, start_capture
        from .stt import DeepgramSTT

        key = os.environ.get("DEEPGRAM_API_KEY")
        if not key:
            self.on_event({"type": "dictation", "state": "error",
                           "text": "No Deepgram key: add one under API keys to speak the role, or type it."})
            return
        self.running = True
        pa = stream = None
        try:
            import pyaudiowpatch as pyaudio
            pa = pyaudio.PyAudio()
            device = find_input(pa, self.mic)
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()
            async with DeepgramSTT(key, self.lang) as stt:
                stream = start_capture(pa, device, loop, queue)
                self.on_event({"type": "dictation", "state": "listening"})
                pump = asyncio.create_task(self._pump(queue, stt))
                reader = asyncio.create_task(self._read(stt))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.seconds)
                except asyncio.TimeoutError:
                    pass
                finally:
                    stream.stop_stream()
                    stream.close()
                    stream = None
                    pump.cancel()
                    try:
                        await stt.finish()                      # flush what was still being heard
                        await asyncio.wait_for(reader, timeout=3)
                    except Exception:
                        reader.cancel()
            self.on_event({"type": "dictation", "state": "done", "text": self.text.value})
        except Exception as e:
            self.on_event({"type": "dictation", "state": "error", "text": f"could not listen: {e}"})
        finally:
            if stream is not None:
                stream.close()
            if pa is not None:
                pa.terminate()
            self.running = False

    async def _pump(self, queue: asyncio.Queue, stt) -> None:
        while True:
            t_capture, chunk = await queue.get()
            await stt.send_audio(chunk, t_capture=t_capture)

    async def _read(self, stt) -> None:
        async for ev in stt.events():
            if self.text.feed(ev.kind, ev.text):
                self.on_event({"type": "dictation", "state": "text", "text": self.text.value})
