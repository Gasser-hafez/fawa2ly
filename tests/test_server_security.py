"""Who may talk to the engine (security fix, 2026-10-16).

WebSockets are not subject to CORS, so a hostile web page in the user's browser could connect to
127.0.0.1 and read the live transcript, saved calls and documents. The server now accepts only the
app's own origins and loopback Host names, and will not listen on any other address. Each test
connects the way a browser page would: with an Origin header it cannot change.
"""

import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from fawa2ly import server as server_mod
from fawa2ly.server import ALLOWED_ORIGINS, SessionServer


@pytest.fixture(autouse=True)
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def make_server(**kw) -> SessionServer:
    return SessionServer(lambda chosen, on_event: None, host="127.0.0.1", port=0, **kw)


async def start(server):
    task = asyncio.create_task(server.run())
    while server.port == 0:
        await asyncio.sleep(0)
    return task


async def hello(url, **kw):
    async with connect(url, **kw) as ws:
        return json.loads(await asyncio.wait_for(ws.recv(), 3))


def run(coro_fn, **kw):
    async def go():
        server = make_server(**kw)
        task = await start(server)
        try:
            return await coro_fn(server)
        finally:
            task.cancel()
    return asyncio.run(go())


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null",
                                    "http://tauri.localhost.evil.example", "file://"])
def test_a_foreign_web_page_is_refused_before_it_can_send_anything(origin):
    async def go(server):
        with pytest.raises(InvalidStatus) as refused:
            await hello(f"ws://127.0.0.1:{server.port}", origin=origin)
        assert refused.value.response.status_code == 403
    run(go)


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_the_apps_own_pages_are_accepted(origin):
    async def go(server):
        got = await hello(f"ws://127.0.0.1:{server.port}", origin=origin)
        assert got["type"] == "hello"
    run(go)


def test_a_client_that_is_not_a_browser_sends_no_origin_and_is_accepted():
    async def go(server):
        assert (await hello(f"ws://127.0.0.1:{server.port}"))["type"] == "hello"
    run(go)


def test_an_extra_origin_must_be_asked_for():
    async def go(server):
        got = await hello(f"ws://127.0.0.1:{server.port}", origin="http://localhost:8080")
        assert got["type"] == "hello"
    run(go, allow_origins=("http://localhost:8080",))

    async def refused(server):
        with pytest.raises(InvalidStatus):
            await hello(f"ws://127.0.0.1:{server.port}", origin="http://localhost:8080")
    run(refused)


def test_a_rebound_hostname_is_refused_even_with_a_matching_origin():
    # DNS rebinding: evil.example resolves to 127.0.0.1, so the page's Origin and Host both say
    # evil.example. The Origin allowlist already refuses it; the Host check is the second lock.
    async def go(server):
        port = server.port
        def loopback():                                      # the socket goes to loopback whatever the URL says
            return socket.create_connection(("127.0.0.1", port))
        with pytest.raises(InvalidStatus) as refused:        # Host: evil.example:port
            await hello(f"ws://evil.example:{port}", sock=loopback())
        assert refused.value.response.status_code == 403
        assert (await hello(f"ws://localhost:{port}", sock=loopback()))["type"] == "hello"
    run(go)


def test_only_loopback_can_be_bound():
    for host in ("0.0.0.0", "192.168.1.5", "::", ""):
        with pytest.raises(ValueError, match="no authentication"):
            SessionServer(lambda c, e: None, host=host)
    assert server_mod.main(["--host", "0.0.0.0", "--idle"]) == 1


def test_the_installed_engine_does_not_accept_the_dev_server_pages(monkeypatch):
    """The :5173 origins are for running from source. In the frozen engine a page served on that
    port is somebody else's (audit F6); the app's own window and --allow-origin still work."""
    monkeypatch.setattr(server_mod.sys, "frozen", True, raising=False)

    async def go(server):
        with pytest.raises(InvalidStatus) as refused:
            await hello(f"ws://127.0.0.1:{server.port}", origin="http://localhost:5173")
        assert refused.value.response.status_code == 403
        assert (await hello(f"ws://127.0.0.1:{server.port}", origin="http://tauri.localhost"))["type"] == "hello"
        assert (await hello(f"ws://127.0.0.1:{server.port}", origin="http://localhost:8080"))["type"] == "hello"
    run(go, allow_origins=("http://localhost:8080",))
