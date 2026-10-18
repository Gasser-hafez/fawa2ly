"""The WebSocket protocol the desktop UI talks (M3 steps 2 and 4).

The engine is stubbed: none of this needs an audio device, a key or a network beyond loopback.
What is under test is the contract a UI depends on - hello, the idle state the setup screen is
drawn from, starting and stopping a session, the commands, and the promise that one wedged
client cannot hold up the call.

Every test points APPDATA at a temporary folder, so nothing here touches the settings or the
background document of whoever is running the suite.
"""

import asyncio
import base64
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly.server import PROTOCOL, SessionServer


@pytest.fixture(autouse=True)
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


class StubEngine:
    def __init__(self, on_event, chosen=None):
        self.on_event = on_event
        self.chosen = chosen or {}
        self.paused = False
        self.ran = False
        self.retried = []
        self._stop = asyncio.Event()

    async def run(self):
        self.ran = True
        self.on_event({"type": "status", "state": "listening", "log": "x.jsonl", "provider": None})
        await self._stop.wait()

    def pause(self):
        self.paused = True
        self.on_event({"type": "status", "state": "paused"})

    def resume(self):
        self.paused = False
        self.on_event({"type": "status", "state": "listening"})

    def stop(self):
        self._stop.set()

    def retry(self, n):
        self.retried.append(n)


def make_server(autostart=None, **kw) -> SessionServer:
    made = []

    def factory(chosen, on_event):
        made.append(StubEngine(on_event, chosen))
        return made[-1]

    s = SessionServer(factory, host="127.0.0.1", port=0, autostart=autostart, **kw)
    s.made = made
    return s


async def start(server: SessionServer):
    task = asyncio.create_task(server.run())
    while server.port == 0:                 # serve() resolves port 0 to a real one
        await asyncio.sleep(0)
    return task


async def recv_json(ws):
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))


async def wait_for(ws, match):
    """Read until an event matches; the replayed history arrives first."""
    while True:
        event = await recv_json(ws)
        if match(event):
            return event


def test_a_ui_gets_hello_then_status_and_can_pause_and_stop():
    async def go():
        server = make_server(autostart={})
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            hello = await recv_json(ws)
            assert (hello["type"], hello["protocol"]) == ("hello", PROTOCOL)
            assert isinstance(hello["providers"], list), "the model picker is drawn from hello"
            await wait_for(ws, lambda e: e.get("state") == "listening")

            await ws.send(json.dumps({"cmd": "pause"}))
            await wait_for(ws, lambda e: e.get("state") == "paused")
            assert server.made[0].paused is True

            await ws.send(json.dumps({"cmd": "stop"}))
            await asyncio.wait_for(task, timeout=3)
        assert server.made[0].ran is True

    asyncio.run(go())


def test_idle_carries_what_the_setup_screen_has_to_draw():
    """The UI cannot invent the preset list or remember the last choices; the engine owns both."""
    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await recv_json(ws)                                    # hello
            idle = await wait_for(ws, lambda e: e.get("state") == "idle")
            assert "hr" in idle["presets"] and "mobile" in idle["presets"]
            assert idle["settings"]["lang"] and idle["settings"]["cv_words"] == 0
        task.cancel()

    asyncio.run(go())


def test_start_creates_a_session_and_remembers_the_choices(app_dir):
    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"cmd": "start", "settings": {"preset": "hr", "lang": "en"}}))
            await wait_for(ws, lambda e: e.get("state") == "listening")
            assert server.made[0].chosen == {"preset": "hr", "lang": "en"}

            await ws.send(json.dumps({"cmd": "start", "settings": {}}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "already running" in err["text"]

            # ending a session returns the server to idle: a second call needs no restart
            await ws.send(json.dumps({"cmd": "stop"}))
            back = await wait_for(ws, lambda e: e.get("state") == "idle")
            assert back["settings"]["preset"] == "hr" and back["settings"]["lang"] == "en"
        task.cancel()

    asyncio.run(go())


def test_commands_before_a_session_are_answered_not_swallowed():
    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"cmd": "pause"}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "no session to pause" in err["text"]
        task.cancel()

    asyncio.run(go())


def test_documents_are_stored_per_kind_of_call_and_can_be_removed(app_dir):
    """The webview file picker hides the path, so the file itself arrives and is parsed here."""
    def b64(s):
        return base64.b64encode(s.encode()).decode()

    async def go():
        server = make_server()
        task = await start(server)
        text = "Sam, Flutter developer. Acme Tax internship: Laravel, MySQL."
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"cmd": "cv", "name": "cv.txt", "data": b64(text)}))
            idle = await wait_for(ws, lambda e: e.get("state") == "idle" and e["settings"]["documents"]["interview"])
            assert idle["settings"]["documents"]["interview"] == [{"name": "cv.txt", "words": len(text.split())}]
            assert idle["settings"]["cv_words"] == len(text.split())

            await ws.send(json.dumps({"cmd": "cv", "mode": "meeting", "name": "agenda.md",
                                      "data": b64("Pro plan: 20 dollars a month")}))
            both = await wait_for(ws, lambda e: e.get("state") == "idle" and e["settings"]["documents"]["meeting"])
            assert [d["name"] for d in both["settings"]["documents"]["meeting"]] == ["agenda.md"]
            assert both["settings"]["cv_words"] == len(text.split()), "the saved kind of call's words"

            await ws.send(json.dumps({"cmd": "cv", "name": "notes.docx", "data": ""}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "notes.docx" in err["text"]

            await ws.send(json.dumps({"cmd": "cv", "remove": "cv.txt"}))
            gone = await wait_for(ws, lambda e: e.get("state") == "idle"
                                  and not e["settings"]["documents"]["interview"])
            assert gone["settings"]["cv_words"] == 0
            assert gone["settings"]["documents"]["meeting"], "removing an interview document leaves the meeting ones"
        task.cancel()

    asyncio.run(go())


def test_a_client_that_connects_late_is_given_the_recent_past():
    """The overlay can be closed and reopened mid-call; it must not come back to a blank window."""
    async def go():
        server = make_server(autostart={})
        task = await start(server)
        server.publish({"type": "piece", "stream": "other", "speaker": 0, "text": "So, tell me."})
        server.publish({"type": "line", "n": 1, "text": "I worked nights for a year."})
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            got = [await recv_json(ws) for _ in range(6)]
        kinds = [g["type"] for g in got]
        assert kinds[0] == "hello"
        assert kinds[1] == "status"           # newest status first, so the client knows its state
        assert "piece" in kinds and "line" in kinds
        server.made[0].stop()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(go())


@pytest.mark.parametrize("history", [5, 200])
def test_a_window_opened_mid_call_is_given_the_saved_settings_last(history):
    """2026-09-12: a settings window opened in a long call showed every control at its default. With
    a short history the idle status that carried the settings has scrolled out; with a long one it is
    replayed, older than the file, because the choices made at Start are saved without an event."""
    async def go():
        server = make_server(autostart={"style": "keywords", "speculate": False}, history=history)
        task = await start(server)
        while (server.last_status or {}).get("state") != "listening":
            await asyncio.sleep(0)
        for i in range(10):
            server.publish({"type": "interim", "text": str(i)})
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            got = [await recv_json(ws)]
            while got[-1]["type"] != "settings":
                got.append(await recv_json(ws))
        server.made[0].stop()
        await asyncio.wait_for(task, timeout=3)
        return got

    got = asyncio.run(go())
    assert [g["type"] for g in got[:2]] == ["hello", "status"] and got[1]["state"] == "listening"
    stale = [g for g in got if g.get("state") == "idle"]
    assert bool(stale) == (history == 200), "the pre-call idle status is replayed only while it fits"
    assert all(g["settings"]["style"] == "full" for g in stale)
    last = got[-1]
    assert (last["settings"]["style"], last["settings"]["speculate"]) == ("keywords", False)
    assert "hr" in last["presets"] and isinstance(last["providers"], list)


def test_an_unknown_command_is_answered_not_ignored():
    async def go():
        server = make_server(autostart={})
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"cmd": "selfdestruct"}))
            await ws.send("not json at all")
            errors = []
            while len(errors) < 2:
                event = await recv_json(ws)
                if event["type"] == "error":
                    errors.append(event["text"])
            assert "unknown command" in errors[0] and "not JSON" in errors[1]
        server.made[0].stop()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(go())


def test_retry_carries_the_question_number_and_a_bad_one_is_answered():
    async def go():
        server = make_server(autostart={})
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await wait_for(ws, lambda e: e.get("state") == "listening")
            await ws.send(json.dumps({"cmd": "retry", "n": 3}))
            await ws.send(json.dumps({"cmd": "retry"}))          # no number at all
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "question number" in err["text"]
        assert server.made[0].retried == [3]
        server.made[0].stop()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(go())


def test_a_wedged_client_is_dropped_instead_of_holding_up_the_call():
    """Audio, STT and the model must never wait on a socket. A client that cannot be written to
    loses its messages and its place, and the session carries on."""
    class Wedged:
        async def send(self, _message):
            raise ConnectionResetError("gone")

    class Fine:
        def __init__(self):
            self.got = []

        async def send(self, message):
            self.got.append(message)

    async def go():
        server = make_server()
        wedged, fine = Wedged(), Fine()
        server.clients = {wedged, fine}
        fanout = asyncio.create_task(server._fanout())
        server.publish({"type": "line", "n": 1, "text": "still going"})
        for _ in range(10):
            await asyncio.sleep(0)
        fanout.cancel()
        assert wedged not in server.clients
        assert fine in server.clients and json.loads(fine.got[0])["text"] == "still going"

    asyncio.run(go())


def test_history_is_bounded_so_a_long_call_does_not_grow_forever():
    server = make_server(history=5)
    for i in range(50):
        server.publish({"type": "piece", "text": str(i)})
    assert len(server.history) == 5
    assert server.history[-1]["text"] == "49"


def test_a_call_writes_no_log_unless_transcript_saving_is_on(tmp_path, monkeypatch):
    """The packaged app starts the engine with --sessions. Its logs hold everything said in a call
    (including the raw speech-to-text output), so they must exist only for a user who turned
    "save a transcript" on."""
    import os

    from fawa2ly import server as server_mod
    from fawa2ly import settings as user_settings
    from fawa2ly.engine import EngineConfig

    seen = []

    class Spy:
        def __init__(self, cfg, on_event):
            seen.append(cfg)

    monkeypatch.setattr(server_mod, "Engine", Spy)
    sessions = tmp_path / "sessions"
    make = server_mod.engine_factory(EngineConfig(), str(sessions))

    make({}, lambda e: None)
    assert seen[-1].log_path == os.devnull and seen[-1].raw_path == os.devnull
    assert not sessions.exists(), "nothing is written for a call the user did not choose to keep"

    s = user_settings.load()
    s.save_transcripts = True
    user_settings.save(s)
    make({}, lambda e: None)
    assert seen[-1].log_path.startswith(str(sessions)) and seen[-1].raw() != os.devnull

    assert server_mod.engine_factory(EngineConfig(log_path="x.jsonl"), None)({}, lambda e: None) \
        and seen[-1].log_path == "x.jsonl", "without --sessions a command-line run logs where it was told"


# ---- the transport: frame size, and one bad message never costs the connection (audit F14, F22) ----

def test_a_document_over_the_old_one_mebibyte_frame_is_read_not_dropped():
    """800 KB of PDF is 1.07 MB of base64, which websockets' default max_size closed with 1009 and
    no message. Any file the app accepts has to arrive; a file past MAX_UPLOAD_BYTES is told so."""
    from fawa2ly import server as server_mod

    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            big = base64.b64encode(b"%PDF-1.4 " + b"x" * 800_000).decode()
            await ws.send(json.dumps({"cmd": "cv", "name": "brochure.pdf", "data": big}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "brochure.pdf" in err["text"], "answered by the reader, the socket was not closed"
            too_big = "A" * (server_mod.MAX_UPLOAD_BYTES * 4 // 3 + 100)
            await ws.send(json.dumps({"cmd": "cv", "name": "huge.pdf", "data": too_big}))
            err = await wait_for(ws, lambda e: e["type"] == "error" and "huge.pdf" in e["text"])
            assert "larger than" in err["text"]
            await ws.send(json.dumps({"cmd": "ping"}))
            await wait_for(ws, lambda e: e["type"] == "pong")
        task.cancel()

    asyncio.run(go())


@pytest.mark.parametrize("bad", ['[1, 2]', '"x"', '7', 'null', '{"cmd": "settings", "values": [1]}',
                                 '{"cmd": "start", "settings": [1]}', '{"cmd": "cv", "name": ["a"]}',
                                 b"\xff\xfe\x00"])
def test_a_malformed_command_is_answered_and_the_connection_stays_open(bad):
    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(bad)
            await wait_for(ws, lambda e: e["type"] == "error")
            await ws.send(json.dumps({"cmd": "ping"}))
            await wait_for(ws, lambda e: e["type"] == "pong")      # still there
        task.cancel()

    asyncio.run(go())


def test_a_handler_that_fails_costs_one_error_event_not_the_connection(monkeypatch, capsys):
    """A full disk, a locked settings.json: the window is told, engine.log gets the traceback."""
    from fawa2ly import settings as user_settings

    def boom(_s):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(user_settings, "save", boom)

    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({"cmd": "settings", "values": {"theme": "light"}}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "settings" in err["text"] and "No space left" in err["text"]
            await ws.send(json.dumps({"cmd": "ping"}))
            await wait_for(ws, lambda e: e["type"] == "pong")
        task.cancel()

    asyncio.run(go())
    assert "OSError" in capsys.readouterr().err, "the traceback goes to stderr, which is engine.log"
