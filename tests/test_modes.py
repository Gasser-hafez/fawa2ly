"""Interview or meeting as the call's first choice (roadmap item 4).

The mode picks the prompt and which roles fit. The interview prompt is v3.0, so its relevance
ratings still stand. Nothing here reaches a network or the real app folder.
"""

import asyncio
import json
import os

import pytest
from websockets.asyncio.client import connect

from fawa2ly import settings as user_settings
from fawa2ly.context import ContextBuilder, Preset, available_modes
from fawa2ly.context.builder import NO_BACKGROUND, NO_DOCUMENT
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.llm.prompts import ASKED_LEAD, MODES, STYLES, system_for
from fawa2ly.server import SessionServer, config_from


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_the_meeting_prompt_is_about_the_meeting_and_never_a_cv_or_a_customer():
    """Meeting mode (roadmap item 18, 2026-09-12): its own prompt, no-material line and roles."""
    for style in STYLES:
        text = system_for(style, "meeting")
        assert "meeting material" in text and "Never invent a status" in text
        for word in ("interview", "CV", "candidate", "customer", "product information"):
            assert word not in text, f"{style}: {word!r} belongs to another mode"
    b = ContextBuilder(mode="meeting")
    assert b.system().endswith(NO_DOCUMENT["meeting"]) and b.build("Where are we on the release?").lead == ASKED_LEAD
    assert {m for p, m in available_modes().items() if p.startswith("meeting-")} == {"meeting"}


def test_the_interview_prompt_is_what_it_was_before_there_were_modes():
    assert MODES == ("interview", "meeting")
    for style in STYLES:
        assert system_for(style) == system_for(style, "interview")


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        system_for("full", "support")
    with pytest.raises(ValueError):
        ContextBuilder(mode="support")


def test_a_role_brings_its_own_mode_and_a_mismatch_is_refused():
    meeting = Preset.find("meeting-standup")
    assert ContextBuilder(preset=meeting).mode == "meeting", "a command line that names only a preset"
    assert ContextBuilder().mode == "interview"
    assert ContextBuilder(preset=Preset.find("hr"), mode="interview").mode == "interview"
    with pytest.raises(ValueError, match="meeting"):
        ContextBuilder(preset=meeting, mode="interview")


def test_the_setup_screen_can_tell_which_roles_fit_which_call():
    modes = available_modes()
    assert modes["meeting-standup"] == "meeting"
    assert {modes[p] for p in ("hr", "mobile", "swe")} == {"interview"}


def test_the_mode_is_a_setting_and_an_unknown_one_is_refused():
    assert user_settings.Settings().mode == "interview"
    assert user_settings.clean_mode("meeting") == "meeting"
    for bad in ("support", "", None):
        with pytest.raises(ValueError):
            user_settings.clean_mode(bad)
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"mode": "support"}, f)
    assert user_settings.load().mode == "interview"


def test_the_mode_chosen_for_a_call_reaches_the_engine_and_a_wrong_role_stops_it():
    cfg = config_from(EngineConfig(), {"mode": "meeting", "preset": "meeting-standup"})
    assert (cfg.mode, cfg.preset) == ("meeting", "meeting-standup")
    ok = Engine(EngineConfig(suggest=True, fake_llm=True, mode="meeting", preset="meeting-standup"),
                on_event=lambda _e: None)
    assert ok._build_suggester(None).builder.mode == "meeting"
    wrong = Engine(EngineConfig(suggest=True, fake_llm=True, mode="interview", preset="meeting-standup"),
                   on_event=lambda _e: None)
    with pytest.raises(ValueError, match="meeting-standup"):
        wrong._build_suggester(None)


class StubEngine:
    def __init__(self, on_event):
        self.on_event = on_event
        self._stop = asyncio.Event()

    async def run(self):
        self.on_event({"type": "status", "state": "listening"})
        await self._stop.wait()

    def stop(self):
        self._stop.set()


def serve(script):
    async def go():
        made = []
        server = SessionServer(lambda chosen, on_event: made.append(StubEngine(on_event)) or made[-1],
                               host="127.0.0.1", port=0)
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def until(match):
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                    if match(event):
                        return event
            result = await script(ws, until, made)
        task.cancel()
        return result

    return asyncio.run(go())


def test_idle_says_which_mode_each_role_is_for():
    async def script(ws, until, made):
        return await until(lambda e: e.get("state") == "idle")

    idle = serve(script)
    assert idle["preset_modes"]["meeting-standup"] == "meeting"
    assert idle["settings"]["mode"] == "interview"


def test_the_mode_is_remembered_from_a_call_and_can_be_changed_in_settings():
    async def script(ws, until, made):
        await ws.send(json.dumps({"cmd": "start", "settings": {"mode": "meeting", "preset": "meeting-standup"}}))
        await until(lambda e: e.get("state") == "listening")
        made[0].stop()
        back = await until(lambda e: e.get("state") == "idle" and "preset_modes" in e and e["settings"]["mode"] == "meeting")
        await ws.send(json.dumps({"cmd": "settings", "values": {"mode": "support"}}))
        refused = await until(lambda e: e["type"] == "error")
        await ws.send(json.dumps({"cmd": "settings", "values": {"mode": "interview"}}))
        changed = await until(lambda e: e["type"] == "settings")
        return back, refused, changed

    back, refused, changed = serve(script)
    assert back["settings"]["preset"] == "meeting-standup"
    assert "mode" in refused["text"]
    assert changed["settings"]["mode"] == "interview" and changed["preset_modes"]["hr"] == "interview"
    assert changed["settings"]["preset"] == "", "a meeting role would stop the next interview from starting"


# ---- negotiation help (roadmap item 25, 2026-09-13) ----

def test_negotiation_help_is_added_only_when_it_is_turned_on_and_only_for_this_mode():
    from fawa2ly.llm.prompts import NEGOTIATION_RULE

    for mode in MODES:
        off = ContextBuilder(mode=mode)
        assert NEGOTIATION_RULE[mode] not in off.system(), \
            f"{mode}: every other call sends the prompt it sent before"

        on = ContextBuilder(mode=mode)
        on.negotiation = True
        text = on.system()
        assert NEGOTIATION_RULE[mode] in text
        for other in MODES:
            if other != mode:
                assert NEGOTIATION_RULE[other] not in text, f"{mode} took {other}'s rule"


def test_the_negotiation_rule_forbids_the_two_things_that_would_make_it_dangerous():
    """A model inventing "the usual range is X" during a live pay talk invents the one number the
    user is about to say out loud, and the obvious negotiating advice - imply a competing offer - is
    advice to lie."""
    from fawa2ly.llm.prompts import NEGOTIATION_RULE

    for mode, rule in NEGOTIATION_RULE.items():
        assert "Never invent a market figure" in rule, mode
        assert "only a number that is in the documents or that someone said" in rule, mode
        assert "an offer they do not have" in rule, mode
        assert "They decide what to accept or refuse" in rule, mode


def test_it_composes_with_the_role_rather_than_replacing_it():
    """It is a switch, not a role: choosing a "negotiation" preset would cost the user the role that
    says what they do, in the one conversation where what they do is the whole argument."""
    from fawa2ly.context import Preset
    from fawa2ly.llm.prompts import NEGOTIATION_RULE

    b = ContextBuilder(preset=Preset.find("mobile"), cv_text="Flutter developer, two years.")
    b.negotiation = True
    text = b.system()
    assert NEGOTIATION_RULE["interview"] in text
    assert "Flutter developer, two years." in text and Preset.find("mobile").role in text


def test_the_toggle_reaches_a_call_from_the_saved_settings():
    """It is saved like live feedback, and read when the call is built."""
    import asyncio
    import tempfile

    from fawa2ly import settings as user_settings
    from fawa2ly.engine import Engine, EngineConfig
    from fawa2ly.llm.prompts import NEGOTIATION_RULE
    from fawa2ly.server import config_from

    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tempfile.mkdtemp()
    try:
        assert config_from(EngineConfig(), {}).negotiation is False, "off unless turned on"
        s = user_settings.load()
        s.negotiation = True
        user_settings.save(s)
        cfg = config_from(EngineConfig(), {"mode": "meeting"})
        assert cfg.negotiation is True

        engine = Engine(EngineConfig(suggest=True, fake_llm=True, mode="meeting", negotiation=True),
                        on_event=lambda e: None)
        engine.suggester = engine._build_suggester(None)
        assert NEGOTIATION_RULE["meeting"] in engine.suggester.builder.system()
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old
