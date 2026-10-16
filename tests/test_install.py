"""What the installed app needs from the engine (2026-09-11).

An installed engine runs from its install folder, not the project: the presets must come from
inside it, keys a tester pastes into Settings must reach it without a restart and never travel
back to a window, each call gets its own log only when transcript saving is on, and the
speakers and microphone can be chosen by name. No real audio device, key or network is used.
"""

import asyncio
import datetime
import json
import os

import pytest
from websockets.asyncio.client import connect

from fawa2ly import engine as engine_mod
from fawa2ly import settings as user_settings
from fawa2ly.context import builder
from fawa2ly.engine import EngineConfig
from fawa2ly.server import SessionServer, config_from, session_log_path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for name in user_settings.key_names():
        monkeypatch.delenv(name, raising=False)      # restored after each test, whatever the code set


def test_the_presets_ship_with_the_engine():
    shipped = builder.bundled_path("presets")
    assert os.path.isfile(os.path.join(shipped, "hr.yaml"))
    assert os.path.isfile(os.path.join(shipped, "meeting-standup.yaml"))


def test_keys_reach_the_engine_and_a_window_only_learns_whether_they_are_set():
    assert user_settings.keys_status()["DEEPGRAM_API_KEY"] is False
    user_settings.save_keys({"DEEPGRAM_API_KEY": " dg-secret ", "GEMINI_API_KEY": "gm-secret"})
    assert os.environ["DEEPGRAM_API_KEY"] == "dg-secret", "a key works from the next call, no restart"
    settings = user_settings.load().to_json()
    assert settings["keys"]["DEEPGRAM_API_KEY"] is True and settings["keys"]["GEMINI_API_KEY"] is True
    assert "dg-secret" not in json.dumps(settings) and "gm-secret" not in json.dumps(settings)

    from dotenv import dotenv_values
    assert dotenv_values(user_settings.env_path())["DEEPGRAM_API_KEY"] == "dg-secret"
    user_settings.save_keys({"GEMINI_API_KEY": ""})                  # empty removes it
    assert "GEMINI_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in dotenv_values(user_settings.env_path())


def test_a_key_the_app_does_not_use_or_a_second_line_is_refused_and_nothing_is_written():
    with pytest.raises(ValueError, match="AWS_SECRET"):
        user_settings.save_keys({"AWS_SECRET": "x"})
    with pytest.raises(ValueError, match="single line"):
        user_settings.save_keys({"DEEPGRAM_API_KEY": "abc\nCNC_API_KEY=stolen"})
    assert not os.path.exists(user_settings.env_path())
    assert "DEEPGRAM_API_KEY" not in os.environ


def test_each_call_gets_its_own_log_in_the_sessions_folder(tmp_path):
    folder = str(tmp_path / "sessions")
    when = datetime.datetime(2026, 9, 11, 14, 5, 9)
    first = session_log_path(folder, when)
    assert first == os.path.join(folder, "2026-09-11_14-05-09.jsonl") and os.path.isdir(folder)
    open(first, "w").close()
    assert session_log_path(folder, when) == os.path.join(folder, "2026-09-11_14-05-09-2.jsonl"), \
        "two calls in the same second must not share a log"


class FakePA:
    """The parts of pyaudiowpatch.PyAudio the device choice uses, with a headset plugged in."""

    def __init__(self):
        def dev(i, name, loop, ins):
            return {"index": i, "name": name, "isLoopbackDevice": loop, "maxInputChannels": ins,
                    "hostApi": 1, "defaultSampleRate": 48000.0}
        self.devices = [dev(0, "Speakers (Realtek) [Loopback]", True, 2),
                        dev(1, "Headset (Jabra) [Loopback]", True, 2),
                        dev(2, "Microphone (Realtek)", False, 1),
                        dev(3, "Headset Microphone (Jabra)", False, 1),
                        dev(4, "Speakers (Realtek)", False, 0)]

    def get_device_info_generator_by_host_api(self, *, host_api_index=None, host_api_type=None):
        return iter(self.devices)

    def get_loopback_device_info_generator(self):
        return (d for d in self.devices if d["isLoopbackDevice"])

    def get_default_input_device_info(self):
        return self.devices[2]

    def get_host_api_info_by_type(self, host_api_type):
        return {"defaultOutputDevice": 4}

    def get_device_info_by_index(self, index):
        return self.devices[index]


def test_the_devices_chosen_in_settings_are_opened_and_a_missing_one_falls_back():
    pytest.importorskip("pyaudiowpatch")      # Windows-only audio library
    pa = FakePA()
    assert engine_mod.list_devices(pa) == {
        "speakers": ["Speakers (Realtek) [Loopback]", "Headset (Jabra) [Loopback]"],
        "mics": ["Microphone (Realtek)", "Headset Microphone (Jabra)"]}
    assert engine_mod.pick_device(pa, False, speaker="Headset (Jabra) [Loopback]")[0]["index"] == 1
    assert engine_mod.pick_device(pa, False, speaker="Unplugged [Loopback]")[0]["index"] == 0, \
        "a headset unplugged since falls back to the default speakers"
    assert engine_mod.pick_device(pa, False)[0]["index"] == 0
    assert engine_mod.find_input(pa, "Headset Microphone (Jabra)")["index"] == 3
    assert engine_mod.find_input(pa, "Unplugged")["index"] == 2
    cfg = config_from(EngineConfig(), {"speaker_device": "Headset (Jabra) [Loopback]", "mic_device": ""})
    assert (cfg.speaker_device, cfg.mic_device) == ("Headset (Jabra) [Loopback]", "")


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
            await until(lambda e: e["type"] == "settings")   # what a window gets on connect ends with them
            result = await script(ws, until)
        task.cancel()
        return result

    return asyncio.run(go())


def test_keys_from_the_settings_window_are_saved_and_the_windows_hear_only_their_status():
    async def script(ws, until):
        await ws.send(json.dumps({"cmd": "keys", "values": {"DEEPGRAM_API_KEY": "dg-secret"}}))
        saved = await until(lambda e: e["type"] in ("settings", "error"))
        await ws.send(json.dumps({"cmd": "keys", "values": {"NOPE_KEY": "x"}}))
        refused = await until(lambda e: e["type"] == "error")
        return saved, refused

    saved, refused = serve(script)
    assert saved["type"] == "settings" and saved["settings"]["keys"]["DEEPGRAM_API_KEY"] is True
    assert "dg-secret" not in json.dumps(saved)
    assert os.environ.get("DEEPGRAM_API_KEY") == "dg-secret"
    assert "NOPE_KEY" in refused["text"]


def test_the_settings_window_can_ask_which_devices_there_are(monkeypatch):
    monkeypatch.setattr("fawa2ly.server.list_devices",
                        lambda: {"speakers": ["Headset (Jabra) [Loopback]"], "mics": ["Headset Microphone (Jabra)"]})

    async def script(ws, until):
        await ws.send(json.dumps({"cmd": "devices"}))
        return await until(lambda e: e["type"] in ("devices", "error"))

    event = serve(script)
    assert event == {"type": "devices", "speakers": ["Headset (Jabra) [Loopback]"],
                     "mics": ["Headset Microphone (Jabra)"]}


# ---- what a packaged build needs beyond the presets ----
# The doctor once failed two checks anywhere but the project folder, which is every installed copy.

def test_the_doctor_does_not_fail_where_the_sample_cv_is_not_shipped(tmp_path, monkeypatch):
    """An installed engine has no docs/sample_cv.txt: it is a development fixture and the installer
    carries the engine and the presets, not a fictional person's CV. Until 2026-09-12 that was two
    FAILs in the packaged build's own health check, which a tester reads as a broken install."""
    from fawa2ly import doctor
    monkeypatch.setattr(doctor, "SAMPLE_CV", str(tmp_path / "not-shipped.txt"))

    checks = doctor.check_config() + asyncio.run(doctor.check_offline(str(tmp_path)))
    failed = [c for c in checks if c.status == doctor.FAIL]
    assert not failed, f"failing without the sample CV: {[(c.name, c.detail) for c in failed]}"
    assert next(c for c in checks if c.name == "sample CV").status == doctor.SKIP
    builder_check = next(c for c in checks if c.name.startswith("builder"))
    assert builder_check.status == doctor.OK, "it falls back to its own CV text and still checks the prompt"
