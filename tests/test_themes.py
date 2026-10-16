"""Overlay themes (2026-09-11, design canvas 2a and 12).

A theme lives in three places: its name in fawa2ly/settings.py, its block in styles.css and its
option in settings.html. Written out separately they drift, so this checks all three agree.
"""

import asyncio
import json
import os
import re

import pytest
from websockets.asyncio.client import connect

from fawa2ly import settings as user_settings
from fawa2ly.server import SessionServer

APP = os.path.join(os.path.dirname(__file__), os.pardir, "app", "src")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def read(name):
    with open(os.path.join(APP, name), encoding="utf-8") as f:
        return f.read()


def test_every_theme_has_a_style_block_and_an_option_and_nothing_else_does():
    default, *others = user_settings.THEMES
    assert default == "dark" == user_settings.Settings().theme
    styled = set(re.findall(r':root\[data-theme="([a-z]+)"\]\s*\{', read("styles.css")))
    assert styled == set(others), "the default is the plain :root; every other theme has a block"
    picker = re.search(r'<select id="theme">(.*?)</select>', read("settings.html"), re.S).group(1)
    assert re.findall(r'<option value="([a-z]+)"', picker) == list(user_settings.THEMES)


def test_an_unknown_theme_is_refused():
    assert user_settings.clean_theme("paper") == "paper"
    for bad in ("sepia", "", None, ["dark"]):
        with pytest.raises(ValueError):
            user_settings.clean_theme(bad)


def test_a_stored_theme_that_no_longer_exists_opens_on_the_default():
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"theme": "sepia"}, f)
    assert user_settings.load().theme == "dark"


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


def test_choosing_a_theme_saves_it_and_every_window_hears_it():
    event = send_settings({"theme": "terminal"})
    assert event["type"] == "settings" and event["settings"]["theme"] == "terminal"
    assert user_settings.load().theme == "terminal"


def test_an_unknown_theme_from_a_window_changes_nothing():
    event = send_settings({"theme": "sepia", "text_size": "large"})
    assert event["type"] == "error" and "theme" in event["text"]
    saved = user_settings.load()
    assert saved.theme == "dark" and saved.text_size == "medium"
