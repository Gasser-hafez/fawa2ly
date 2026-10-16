"""A background loaded or removed during a call (2026-09-11).

rehearsal7 ran a whole call with no CV and nothing on screen said so. The moment to notice is
mid-interview, so loading one then must reach the next question - and must not throw the
overlay back to the setup screen, which is what an idle status from the server would do.
"""

import asyncio
import base64
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.server import SessionServer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_a_background_loaded_mid_call_reaches_the_next_question_and_clears_the_warning():
    events = []
    e = Engine(EngineConfig(suggest=True, fake_llm=True), on_event=events.append)
    e.suggester = e._build_suggester(None)
    assert e.cv_words == 0
    e.set_background("Flutter developer, Firebase, Stripe")
    assert e.cv_words == 4 and "Firebase" in e.suggester.builder.system()
    assert events[-1]["type"] == "status" and events[-1]["cv_words"] == 4
    e.set_background("")
    assert e.cv_words == 0 and "Firebase" not in e.suggester.builder.system()


class StubEngine:
    def __init__(self, on_event):
        self.on_event = on_event
        self.backgrounds = []
        self.mode = "interview"
        self._stop = asyncio.Event()

    async def run(self):
        self.on_event({"type": "status", "state": "listening", "cv_words": 0})
        await self._stop.wait()

    def stop(self):
        self._stop.set()

    def set_documents(self, documents):
        self.backgrounds.append(documents)


def test_loading_a_cv_during_a_call_updates_the_call_instead_of_resetting_the_window():
    async def go():
        made = []
        server = SessionServer(lambda chosen, on_event: made.append(StubEngine(on_event)) or made[-1],
                               host="127.0.0.1", port=0, autostart={})
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        seen = []
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def until_settings():
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                    seen.append((event["type"], event.get("state")))
                    if event["type"] == "settings":
                        return

            await until_settings()                  # what a window gets on connect ends with the saved settings
            await ws.send(json.dumps({"cmd": "cv", "name": "cv.txt",
                                      "data": base64.b64encode(b"Flutter developer").decode()}))
            await until_settings()
        made[0].stop()
        await asyncio.wait_for(task, timeout=3)
        return made[0], seen

    engine, seen = asyncio.run(go())
    assert engine.backgrounds == [[("cv.txt", "Flutter developer")]]
    # On connect the server replays the call's history, which begins with the idle status from
    # before the call started; only what follows the last "listening" is live.
    last_listening = len(seen) - 1 - seen[::-1].index(("status", "listening"))
    after_call_started = seen[last_listening:]
    assert ("status", "idle") not in after_call_started, "an idle status mid-call resets the overlay"
