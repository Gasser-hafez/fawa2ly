"""The model speed test, token usage, described roles and dictated text (2026-09-11, maintainer's requests).

No network, no microphone and no real app folder: providers are FakeLLM, APPDATA is a temporary
folder, and dictation is tested at the point where heard words become text.
"""

import asyncio
import datetime
import json
import os

import pytest
from websockets.asyncio.client import connect

from fawa2ly import roles, speedtest, usage
from fawa2ly.context import Preset, available
from fawa2ly.context.builder import preset_info, user_preset_dir
from fawa2ly.dictation import DictationText
from fawa2ly.llm import FakeLLM, collect
from fawa2ly.llm.base import SuggestionRequest
from fawa2ly.server import SessionServer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


# ---- usage ----

def test_usage_counts_requests_and_tokens_and_says_when_tokens_were_not_reported():
    day = datetime.date(2026, 9, 11)
    usage.record("gemini", "flash", {"input_tokens": 100, "output_tokens": 20}, day=day)
    usage.record("gemini", "flash", {"input_tokens": 50, "output_tokens": 5}, day=day)
    usage.record("groq", "haiku", None, day=datetime.date(2026, 8, 30))
    s = usage.summary(today=day)
    assert s["today"] == {"requests": 2, "input_tokens": 150, "output_tokens": 25, "unreported": 0}
    assert s["month"]["requests"] == 2
    assert s["all"]["requests"] == 3 and s["all"]["unreported"] == 1, "not reported is not zero tokens"
    assert list(s["models"]) == ["gemini/flash", "groq/haiku"] and s["since"] == "2026-08-30"
    usage.reset()
    assert usage.summary(today=day)["all"]["requests"] == 0


def test_a_metered_provider_counts_every_request_that_passes_through_it():
    metered = usage.MeteredProvider(FakeLLM(bullets=["I built a chat app"]))
    s = asyncio.run(collect(metered, SuggestionRequest("Why?", "system words here")))
    assert s.bullets == ["I built a chat app"]
    assert (metered.name, metered.model) == ("fake", "fake-1")
    today = usage.summary()["today"]
    assert today["requests"] == 1 and today["output_tokens"] > 0
    asyncio.run(collect(usage.MeteredProvider(FakeLLM(error="boom")), SuggestionRequest("Why?", "s")))
    assert usage.summary()["today"]["requests"] == 2, "a failed request is still a request"


# ---- speed test ----

def test_the_speed_test_ranks_by_first_token_and_a_failure_is_never_the_fastest():
    delays = {"slow": 0.08, "fast": 0.01}

    def make(vendor, model):
        if vendor == "broken":
            raise RuntimeError("BROKEN_API_KEY is not set")
        if vendor == "empty":
            return FakeLLM(bullets=[])
        return FakeLLM(bullets=["A line to say"], first_token_delay=delays[vendor], model=f"{vendor}-model")

    seen = []
    rows = asyncio.run(speedtest.run([{"vendor": v, "model": None} for v in ("slow", "broken", "fast", "empty")],
                                     on_result=seen.append, make=make, runs=2))
    by = {r["vendor"]: r for r in rows}
    assert [r["vendor"] for r in seen] == ["slow", "broken", "fast", "empty"], "each row as it finishes"
    assert by["fast"]["ttft_ms"] < by["slow"]["ttft_ms"]
    assert by["fast"]["runs"] == 2 and by["fast"]["model"] == "fast-model" and by["fast"]["lines"] == 1
    assert "BROKEN_API_KEY" in by["broken"]["error"]
    assert speedtest.fastest(rows) == "fast"


def test_a_model_that_does_not_answer_in_time_is_not_a_candidate():
    rows = asyncio.run(speedtest.run([{"vendor": "stuck", "model": None}],
                                     make=lambda v, m: FakeLLM(first_token_delay=5), runs=1, timeout_s=0.05))
    assert "no reply" in rows[0]["error"] and speedtest.fastest(rows) is None


# ---- described roles ----

BRIEF = json.dumps({"title": "Pharmacy technician", "role": "Pharmacy technician in a hospital pharmacy",
                    "focus": ["dispensing accuracy", "stock control", "working with pharmacists"],
                    "avoid": ["clinical advice beyond their scope", "teamwork claims with no example"]})


def test_a_described_role_is_written_up_saved_and_listed_with_its_title():
    provider = FakeLLM(bullets=[BRIEF])
    brief = asyncio.run(roles.analyze("I work in a hospital pharmacy preparing and checking prescriptions",
                                      "interview", provider))
    assert brief["name"] == "custom-pharmacy-technician" and len(brief["focus"]) == 3
    assert "hospital pharmacy" in provider.requests[0].user, "the description is the message, not a question"
    assert "custom-pharmacy-technician" in available()
    assert Preset.find("custom-pharmacy-technician").mode == "interview"
    info = preset_info()
    assert info["custom-pharmacy-technician"] == {"title": "Pharmacy technician", "custom": True,
                                                  "category": "Your roles",
                                                  "role": "Pharmacy technician in a hospital pharmacy"}
    assert info["hr"]["custom"] is False and info["hr"]["category"] == "General"

    again = asyncio.run(roles.analyze("I also prepare IV medication in the same hospital pharmacy",
                                      "interview", FakeLLM(bullets=[BRIEF])))
    assert again["name"] == "custom-pharmacy-technician-2", "a second role never overwrites the first"
    roles.delete("custom-pharmacy-technician")
    assert "custom-pharmacy-technician" not in available()
    with pytest.raises(ValueError):
        roles.delete("hr")


def test_a_reply_that_is_not_a_role_brief_or_a_description_too_short_saves_nothing():
    with pytest.raises(ValueError, match="role brief"):
        asyncio.run(roles.analyze("I work in a hospital pharmacy preparing prescriptions", "meeting",
                                  FakeLLM(bullets=["Sorry, I cannot help with that"])))
    with pytest.raises(ValueError, match="at least"):
        asyncio.run(roles.analyze("pharmacy", "interview", FakeLLM(bullets=[BRIEF])))
    folder = user_preset_dir()
    assert not os.path.isdir(folder) or not os.listdir(folder)


# ---- dictation ----

def test_dictated_text_keeps_what_was_said_and_replaces_what_is_still_being_heard():
    t = DictationText()
    assert t.feed("interim", "I work") and t.value == "I work"
    assert t.feed("interim", "I work in a") and t.value == "I work in a"
    assert t.feed("final", "I work in a pharmacy.") and t.value == "I work in a pharmacy."
    assert t.feed("interim", "every day") and t.value == "I work in a pharmacy. every day"
    assert not t.feed("utterance_end", "")


# ---- through the settings window ----

def serve(script):
    async def go():
        server = SessionServer(lambda chosen, on_event: None, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def until(match):
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if match(event):
                        return event
            result = await script(ws, until)
        task.cancel()
        return result

    return asyncio.run(go())


def test_the_speed_test_runs_from_the_settings_window_and_names_the_fastest(monkeypatch):
    monkeypatch.setattr("fawa2ly.server.available_providers",
                        lambda: [{"vendor": "a", "model": "m1", "local": False},
                                 {"vendor": "b", "model": "m2", "local": False}])
    monkeypatch.setattr("fawa2ly.speedtest._real_provider",
                        lambda v, m: FakeLLM(bullets=["A line"], first_token_delay=0.06 if v == "a" else 0.0))

    async def script(ws, until):
        await ws.send(json.dumps({"cmd": "speedtest"}))
        return await until(lambda e: e["type"] == "speedtest" and e["state"] == "finished")

    done = serve(script)
    assert done["fastest"] == "b" and [r["vendor"] for r in done["results"]] == ["a", "b"]


def test_a_role_described_in_the_settings_window_joins_the_role_list(monkeypatch):
    monkeypatch.setattr("fawa2ly.roles.make_provider", lambda vendor, model: FakeLLM(bullets=[BRIEF]))

    async def script(ws, until):
        await ws.send(json.dumps({"cmd": "role", "mode": "meeting",
                                  "description": "I sell hospital pharmacy software to procurement teams"}))
        role = await until(lambda e: e["type"] in ("role", "error"))
        listed = await until(lambda e: e["type"] == "settings")
        await ws.send(json.dumps({"cmd": "usage"}))
        used = await until(lambda e: e["type"] == "usage")
        return role, listed, used

    role, listed, used = serve(script)
    assert role["type"] == "role" and role["name"] == "custom-pharmacy-technician"
    assert listed["preset_modes"]["custom-pharmacy-technician"] == "meeting"
    assert listed["preset_info"]["custom-pharmacy-technician"]["custom"] is True
    assert set(used) >= {"today", "month", "all", "models"}
