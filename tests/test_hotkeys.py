"""Global hotkeys the user can change (2026-09-11, roadmap item 3).

The window registers them and reports what the OS refused; the engine checks everything that can
be checked without the OS, before anything is saved: the shape of a combination, one used twice,
and a show/hide toggle that must always exist.
"""

import asyncio
import json
import os

import pytest
from websockets.asyncio.client import connect

from fawa2ly import settings as user_settings
from fawa2ly.server import SessionServer

DEFAULTS = user_settings.DEFAULT_HOTKEYS


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_every_action_has_a_default_and_a_fresh_install_gets_them():
    assert set(DEFAULTS) == set(user_settings.HOTKEY_ACTIONS)
    assert user_settings.load().hotkeys == DEFAULTS
    assert user_settings.clean_hotkeys({}, DEFAULTS) == DEFAULTS, "the defaults must pass their own checks"


def test_a_change_names_only_what_changed_and_keeps_the_rest():
    merged = user_settings.clean_hotkeys({"more": " Ctrl + Alt + N ", "retry": None, "nonsense": "x"},
                                         DEFAULTS)
    assert merged["more"] == "Ctrl+Alt+N"
    assert merged["retry"] == "", "None clears an action"
    assert "nonsense" not in merged
    assert merged["toggle"] == DEFAULTS["toggle"]


@pytest.mark.parametrize("combo", ["M", "Ctrl+Alt", "Ctrl++M", "Ctrl+Hyper+M", "+M"])
def test_a_key_needs_a_modifier_and_a_modifier_needs_a_key(combo):
    with pytest.raises(ValueError):
        user_settings.clean_hotkeys({"more": combo}, DEFAULTS)


def test_something_that_is_not_text_is_refused():
    for bad in (5, ["Ctrl", "M"], "Ctrl+Alt+" + "M" * 40):
        with pytest.raises(ValueError):
            user_settings.clean_hotkeys({"more": bad}, DEFAULTS)
    with pytest.raises(ValueError):
        user_settings.clean_hotkeys("Ctrl+Alt+M", DEFAULTS)


def test_the_toggle_cannot_be_cleared_because_it_is_the_way_back_to_a_hidden_window():
    with pytest.raises(ValueError, match="toggle"):
        user_settings.clean_hotkeys({"toggle": ""}, DEFAULTS)


def test_one_combination_for_two_actions_is_refused_whatever_it_is_called():
    with pytest.raises(ValueError, match="both"):
        user_settings.clean_hotkeys({"more": "Ctrl+Alt+R"}, DEFAULTS)            # retry has it
    with pytest.raises(ValueError, match="both"):
        user_settings.clean_hotkeys({"more": "CmdOrCtrl+Shift+KeyP"}, DEFAULTS)  # the toggle
    with pytest.raises(ValueError, match="both"):
        user_settings.clean_hotkeys({"next": "Alt+Control+ArrowLeft"}, DEFAULTS)  # previous
    # Moving a combination is fine when the old owner lets go in the same change.
    moved = user_settings.clean_hotkeys({"more": "Ctrl+Alt+R", "retry": ""}, DEFAULTS)
    assert moved["more"] == "Ctrl+Alt+R" and moved["retry"] == ""


def test_a_file_from_before_an_action_existed_gets_its_default(tmp_path):
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"hotkeys": {"toggle": "Ctrl+Shift+O"}}, f)
    loaded = user_settings.load().hotkeys
    assert loaded["toggle"] == "Ctrl+Shift+O"
    assert loaded["more"] == DEFAULTS["more"]


def test_a_file_edited_into_nonsense_falls_back_to_the_defaults():
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"hotkeys": {"toggle": "", "more": "M"}}, f)
    assert user_settings.load().hotkeys == DEFAULTS


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


def test_the_settings_window_changes_one_hotkey_and_every_window_hears_the_whole_set():
    event = send_settings({"hotkeys": {"more": "Ctrl+Alt+N"}})
    assert event["type"] == "settings"
    assert event["settings"]["hotkeys"] == {**DEFAULTS, "more": "Ctrl+Alt+N"}
    assert user_settings.load().hotkeys["more"] == "Ctrl+Alt+N"


def test_a_refused_hotkey_says_why_and_nothing_in_the_same_change_is_saved():
    event = send_settings({"hotkeys": {"more": "Ctrl+Alt+R"}, "text_size": "large"})
    assert event["type"] == "error" and "retry" in event["text"]
    saved = user_settings.load()
    assert saved.hotkeys == DEFAULTS and saved.text_size == "medium"
