"""The overlay's background opacity (2026-09-11, roadmap item 1).

The slider fades the panel behind the text, never the text. The engine stores the value like the
other overlay preferences, so it is clamped there: a window can send anything over the socket.
"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly import settings as user_settings
from fawa2ly.server import SessionServer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_opacity_stays_between_a_readable_floor_and_solid():
    assert user_settings.clamp_opacity(0.55) == 0.55
    assert user_settings.clamp_opacity(0) == user_settings.MIN_OPACITY
    assert user_settings.clamp_opacity("1.7") == 1.0
    for bad in ("abc", None, float("nan")):
        with pytest.raises(ValueError):
            user_settings.clamp_opacity(bad)


def test_opacity_has_a_default_and_survives_a_restart():
    assert user_settings.load().opacity == 0.9
    s = user_settings.load()
    s.opacity = 0.5
    user_settings.save(s)
    assert user_settings.load().opacity == 0.5


def send_settings(values):
    async def go():
        server = SessionServer(lambda chosen, on_event: None, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            while json.loads(await asyncio.wait_for(ws.recv(), timeout=3))["type"] != "settings":
                pass                        # what a window gets on connect ends with the saved settings
            await ws.send(json.dumps({"cmd": "settings", "values": values}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                if event["type"] in ("settings", "error"):
                    break
        task.cancel()
        return event

    return asyncio.run(go())


def test_a_slider_value_out_of_range_is_clamped_and_every_window_hears_it():
    event = send_settings({"opacity": 0.05})
    assert event["type"] == "settings"
    assert event["settings"]["opacity"] == user_settings.MIN_OPACITY
    assert user_settings.load().opacity == user_settings.MIN_OPACITY


def test_a_value_that_is_not_a_number_is_refused_and_nothing_is_saved():
    event = send_settings({"opacity": "abc", "text_size": "large"})
    assert event["type"] == "error" and "opacity" in event["text"]
    saved = user_settings.load()
    assert saved.opacity == 0.9 and saved.text_size == "medium"


# ---- local transcription from the Settings window (offline mode) ----

def test_the_whisper_model_is_one_of_the_sizes_the_window_offers():
    assert user_settings.clean_whisper_model(None) is None and user_settings.clean_whisper_model("") is None
    for size in user_settings.WHISPER_MODELS:
        assert user_settings.clean_whisper_model(size) == size
    for bad in ("large-v3", "../../etc", "Systran/faster-whisper-small", 3):
        with pytest.raises(ValueError):
            user_settings.clean_whisper_model(bad)


def test_offline_transcription_is_chosen_in_settings_and_reaches_the_next_call():
    from fawa2ly.engine import EngineConfig
    from fawa2ly.server import config_from

    event = send_settings({"stt_vendor": "whisper", "whisper_model": "base"})
    assert event["type"] == "settings"
    assert (event["settings"]["stt_vendor"], event["settings"]["whisper_model"]) == ("whisper", "base")
    assert "whisper_available" in event["settings"], "the window warns when the libraries are missing"
    cfg = config_from(EngineConfig(), {})
    assert (cfg.stt_vendor, cfg.whisper_model) == ("whisper", "base")


def test_an_unknown_model_or_engine_is_refused_and_nothing_is_saved():
    for values in ({"whisper_model": "huge"}, {"stt_vendor": "azure"}):
        event = send_settings(values)
        assert event["type"] == "error"
    saved = user_settings.load()
    assert saved.stt_vendor == "deepgram" and saved.whisper_model is None
