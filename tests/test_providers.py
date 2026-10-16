"""Switching the model, before or during a call (M3, 2026-09-10).

rehearsal6: the pipeline closed turns in 1.8 s at p50 while the free Gemini tier took 43 s to a
first token. When the provider is the problem, changing it has to be possible mid-interview.
Keys are faked here; no test reads the real .env or reaches a network.
"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.llm import FakeLLM
from fawa2ly.server import SessionServer, available_providers


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")


def test_only_providers_with_a_key_or_a_local_server_are_offered():
    names = {p["vendor"] for p in available_providers()}
    assert "groq" in names
    assert "gemini" not in names and "cerebras" not in names


def test_switching_mid_call_sends_the_next_question_to_the_new_model():
    events = []
    e = Engine(EngineConfig(), on_event=events.append)
    e.suggester = Suggester(FakeLLM(), ContextBuilder())
    e.provider = e.suggester.provider

    async def go():
        label = await e.set_provider("groq", "openai/gpt-oss-120b")
        await e._close_providers()
        return label

    label = asyncio.run(go())
    assert label == "groq/openai/gpt-oss-120b"
    assert (e.suggester.provider.name, e.suggester.provider.model) == ("groq", "openai/gpt-oss-120b")
    assert events[-1]["type"] == "status" and events[-1]["provider"] == label


def test_switching_to_a_provider_without_a_key_fails_and_keeps_the_old_one():
    e = Engine(EngineConfig(), on_event=lambda _e: None)
    old = FakeLLM()
    e.suggester = Suggester(old, ContextBuilder())
    with pytest.raises(RuntimeError):
        asyncio.run(e.set_provider("gemini"))
    assert e.suggester.provider is old


class StubEngine:
    def __init__(self, on_event):
        self.on_event = on_event
        self.switched = []
        self._stop = asyncio.Event()

    async def run(self):
        self.on_event({"type": "status", "state": "listening", "provider": "fake/fake-1"})
        await self._stop.wait()

    def pause(self):
        pass

    def resume(self):
        pass

    def stop(self):
        self._stop.set()

    def retry(self, n):
        pass

    async def set_provider(self, vendor, model=None):
        if vendor == "nope":
            raise RuntimeError("NOPE_API_KEY is not set")
        self.switched.append((vendor, model))
        return f"{vendor}/{model}"


def test_the_ui_can_switch_the_model_during_a_call_and_is_told_when_it_cannot():
    async def go():
        made = []
        server = SessionServer(lambda chosen, on_event: made.append(StubEngine(on_event)) or made[-1],
                               host="127.0.0.1", port=0, autostart={})
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            assert any(p["vendor"] == "groq" for p in hello["providers"])
            await ws.send(json.dumps({"cmd": "provider", "vendor": "groq", "model": "openai/gpt-oss-20b"}))
            await ws.send(json.dumps({"cmd": "provider", "vendor": "nope"}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if event["type"] == "error":
                    assert "could not switch" in event["text"]
                    break
        assert made[0].switched == [("groq", "openai/gpt-oss-20b")]
        made[0].stop()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(go())
