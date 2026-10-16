"""
The engine, served to a UI over a WebSocket.

    python -m fawa2ly.server --suggest --preset mobile --cv cv.txt --log call1.jsonl
    python -m fawa2ly.server --replay interview7.raw.jsonl --suggest --fake-llm   # no audio, no key
    python -m fawa2ly.server --idle                     # wait for the UI to choose and start
    # ws://127.0.0.1:8765

This is the same `Engine` with a socket in front of it instead of a console. Every engine event
goes out as one JSON message, and the UI sends back commands.

    server -> UI   {"type": "hello", "protocol": 2}      first message on connect
                   then the newest status, the recent history, and the saved settings
                   every engine event:
                   status | log | interim | piece | question | line | utterance | assist
    UI -> server   {"cmd": "start", "settings": {...}}   begin a session with these choices
                   {"cmd": "pause"} | {"cmd": "resume"} | {"cmd": "stop"} | {"cmd": "ping"}
                   {"cmd": "retry", "n": 3}              ask question 3 again
                   {"cmd": "provider", "vendor": "gemini", "model": null}   switch models mid-call
                   {"cmd": "more", "n": 3}               two or three more lines for question 3
                   {"cmd": "action", "kind": "recap"}    say | recap | ask opens a card; shorter needs "n"
                   {"cmd": "jobfit", "description": "..."}  compare a job description with the CV
                   {"cmd": "settings", "values": {...}}  save choices; every window gets "settings"
                   {"cmd": "cv", "name": "cv.pdf", "data": "<base64>"} | {"cmd": "cv"} to forget

Between sessions the server sits in `idle` and says what the UI needs to draw the setup screen:
the presets on disk, the last session's choices, and whether a background document is stored.
A session that ends returns it to `idle`, so a second call does not need the process restarted.

Three decisions worth stating, because all three are easy to get wrong later:

**A late or reconnecting client gets the recent past.** The overlay can be closed and reopened
mid-call, and a UI that connects to a running session must not show an empty window until the
next question. The server keeps the last `HISTORY` events and replays them on connect, with the
most recent `status` first so the client knows what state it is in, and the saved settings last,
because a long call scrolls the idle status that carried them out of the history.

**A slow client never slows the call.** Sending is per-client and failures drop that client;
audio, STT and the model run on their own tasks and never await a socket.

**Loopback only, and only for our own pages.** Nothing off this machine can reach 127.0.0.1, but
a web page open in the user's browser can: WebSockets are not subject to CORS, so without a check
any site could connect here, read the live transcript, saved calls and documents, and change
settings or keys (reproduced 2026-10-16 with `Origin: https://evil.example`). Two checks close it,
both made before the handshake completes:

  - **Origin.** A browser always sends the page's origin and a page cannot change it. Only the
    app's own origins (`ALLOWED_ORIGINS`: the Tauri webview on each platform and the local dev
    server) are accepted. Clients that are not browsers send no Origin and are accepted: a web
    page cannot omit it, and a process on this machine can already read the app folder.
  - **Host.** The Host header must name loopback, which stops DNS rebinding (a hostile name that
    resolves to 127.0.0.1 would otherwise carry its own, matching Origin).

Binding to any other interface is refused outright, since there is still no authentication.
The `cv` command writes a file the user chose, into the app folder, which is safe for the same
reasons. The installed engine (a frozen build) accepts only the app's own origins: the :5173 dev
pages are for running from source, and a page served on that port on a user's machine is not ours.

**One bad message never costs the connection.** Whatever is wrong with a command - not JSON, not an
object, a field of the wrong type, a handler that raises (a locked settings.json, a full disk) - the
sender is answered with an `error` event and the socket stays up. An escaping exception used to
close it with 1011, and the overlay then said "not listening" in the middle of a call. A document
up to `MAX_UPLOAD_BYTES` fits one frame; the default 1 MiB frame closed the socket on an ordinary
PDF CV with no message at all.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import traceback
from http import HTTPStatus
from typing import Callable, Optional

import websockets
from websockets.asyncio.server import serve

from . import jobfit, review, roles, speedtest, usage
from . import settings as user_settings
from .console import safe_console
from .context import available, available_modes, extract_cv
from .context.builder import preset_info
from .dictation import Dictation
from .engine import Engine, EngineConfig, list_devices
from .limits import MAX_UPLOAD_BYTES
from .preflight import preflight_call

try:
    from dotenv import load_dotenv
    load_dotenv(".env")                          # the project folder, when run from source
    # Keys pasted into Settings. load_dotenv never overrides a variable
    # that is already set, so when both exist the project's .env above wins during development.
    load_dotenv(user_settings.env_path())
except ImportError:
    pass

PROTOCOL = 2
HOST = "127.0.0.1"
PORT = 8765
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# The pages that may talk to the engine: the Tauri webview (http://tauri.localhost on Windows and
# Android, tauri:// on macOS and Linux, https:// when the app is configured for it) and the plain
# static server the README uses for UI work. Add more with --allow-origin, one flag per origin.
APP_ORIGINS = ("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost")
DEV_ORIGINS = ("http://127.0.0.1:5173", "http://localhost:5173")
ALLOWED_ORIGINS = APP_ORIGINS + DEV_ORIGINS
HISTORY = 200        # events replayed to a client that connects (or reconnects) mid-session


def default_origins() -> tuple:
    """The dev server's origins only when running from source: the installed engine (PyInstaller
    sets sys.frozen) serves the app's own windows, and --allow-origin still adds any other."""
    return APP_ORIGINS if getattr(sys, "frozen", False) else ALLOWED_ORIGINS


def _object(value, what: str) -> dict:
    """A field that has to be a JSON object (absent counts as empty). ValueError otherwise, which
    handle_command answers as an `error` event."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    return value


def _read_document(name: str, data: str) -> str:
    """The text of an uploaded file (base64 in `data`). Plain function so it runs in a thread."""
    return extract_cv(name, base64.b64decode(data))


def available_providers() -> list[dict]:
    """Providers this machine can use right now: a key in the environment, or a local server.

    Offering one without a key would only produce an error mid-interview. Key values are never
    read out here, only whether the variable is set."""
    from .llm.openai_compat import all_vendors
    return [{"vendor": name, "label": v["label"], "local": bool(v.get("local")), "model": v["model"]}
            for name, v in all_vendors().items() if user_settings.provider_ready(v)]


async def check_provider(vendor: str, timeout: float = 8.0) -> dict:
    """Reach a provider with the key and address it has now and list its models: proves the key, the
    address and that something is listening, without spending a request on an answer."""
    from .llm.openai_compat import OpenAICompatibleLLM
    t0 = time.monotonic()
    llm = None
    try:
        llm = OpenAICompatibleLLM.from_env(vendor, require_model=False)
        models = await asyncio.wait_for(llm.list_models(), timeout)
        return {"vendor": vendor, "ok": True, "ms": round((time.monotonic() - t0) * 1000), "models": models}
    except Exception as e:
        text = "no answer in time" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
        return {"vendor": vendor, "ok": False, "error": text[:300]}
    finally:
        if llm is not None:
            try:
                await llm.close()
            except Exception:
                pass


def config_from(base: EngineConfig, chosen: dict) -> EngineConfig:
    """The CLI's configuration, overridden by what the setup screen chose."""
    cfg = EngineConfig(**dict(base.__dict__))
    for key in ("lang", "vendor", "mic", "user_mic", "suggest", "speculate", "style", "mode",
                "speaker_device", "mic_device"):
        if chosen.get(key) not in (None, ""):
            setattr(cfg, key, chosen[key])
    if "preset" in chosen:                 # "" is a real choice here: no role preset
        cfg.preset = chosen["preset"] or None
    if chosen.get("model"):
        cfg.llm_model = chosen["model"]
    # The documents stored for this kind of call take the place of a --cv given on the command line.
    stored = user_settings.load_documents(cfg.mode or "interview")
    if stored:
        cfg.documents, cfg.cv_text, cfg.cv = stored, "", None
    saved = user_settings.load()
    # A transcript is kept only when the user turned saving on (roadmap item 6, non-goals).
    if saved.save_transcripts:
        cfg.transcript_dir = user_settings.transcripts_dir()
    cfg.feedback = saved.live_feedback            # roadmap item 9, off unless turned on
    cfg.negotiation = saved.negotiation           # roadmap item 25, off unless turned on
    # roadmap item 26: deepgram (default) or whisper (offline). Precedence is CLI flag > saved
    # setting > default: a flag left at its default cannot be told from one not given, so the saved
    # setting fills in only while the flag is at the default (it used to overwrite the flag, and
    # `--stt-vendor whisper` silently did nothing, audit F23).
    if base.stt_vendor == EngineConfig.stt_vendor:
        cfg.stt_vendor = saved.stt_vendor
    if base.whisper_model is None:
        cfg.whisper_model = saved.whisper_model
    return cfg


class SessionServer:
    """Serves one engine at a time. `make_engine` takes the chosen settings and the event
    callback and returns the engine, so a test can drive the protocol without an audio device."""

    def __init__(self, make_engine: Callable[[dict, Callable[[dict], None]], object],
                 host: str = HOST, port: int = PORT, history: int = HISTORY,
                 autostart: Optional[dict] = None, allow_origins: tuple = (),
                 preflight: Optional[Callable] = None):
        if host not in LOOPBACK_HOSTS:
            raise ValueError(f"refusing to listen on {host}: the engine has no authentication, "
                             "so it only serves this machine (127.0.0.1)")
        self.origins = default_origins() + tuple(allow_origins)
        self.host = host
        self.port = port
        self.history_size = history
        self.make_engine = make_engine
        self.preflight = preflight            # async (chosen) -> problems; None skips it (tests)
        self._checking = False
        self.autostart = autostart
        self.clients: set = set()
        self.history: list[dict] = []
        self.last_status: Optional[dict] = None
        self.engine = None
        self.session: Optional[asyncio.Task] = None
        self._outbox: asyncio.Queue = asyncio.Queue()

    # ---- outbound ----
    def publish(self, event: dict) -> None:
        """Engine callback. Never blocks: the queue is unbounded and drained by _fanout()."""
        if event.get("type") == "status" and event.get("state") in ("idle", "listening", "paused", "stopped"):
            self.last_status = event
        self.history.append(event)
        del self.history[: -self.history_size]
        self._outbox.put_nowait(event)

    def idle(self) -> None:
        """Say what the setup screen needs: the presets on disk and the last session's choices."""
        self.publish({"type": "status", "state": "idle", "presets": available(), "preset_modes": available_modes(),
                      "preset_info": preset_info(),
                      "providers": available_providers(),
                      "settings": user_settings.load().to_json()})

    async def _fanout(self) -> None:
        while True:
            event = await self._outbox.get()
            if not self.clients:
                continue
            message = json.dumps(event, ensure_ascii=False)
            for ws in list(self.clients):
                try:
                    await ws.send(message)
                except Exception:       # a dead or wedged client is not the call's problem
                    self.clients.discard(ws)

    # ---- session lifecycle ----
    @property
    def running(self) -> bool:
        return self.session is not None and not self.session.done()

    def _settings_event(self) -> dict:
        return {"type": "settings", "settings": user_settings.load().to_json(),
                "providers": available_providers(), "presets": available(),
                "preset_modes": available_modes(), "preset_info": preset_info()}

    def _publish_settings(self) -> None:
        self.publish(self._settings_event())

    async def handle_speedtest(self, ws) -> None:
        """Time every model with a key (2026-09-11). Between calls only: its requests would slow the
        answers of a running call, and the result would measure that instead of the model."""
        if self.running:
            await ws.send(json.dumps({"type": "error", "text": "a call is running: test the models between calls"}))
            return
        task = getattr(self, "_speed_task", None)
        if task is not None and not task.done():
            await ws.send(json.dumps({"type": "error", "text": "a speed test is already running"}))
            return
        providers = available_providers()
        if not providers:
            await ws.send(json.dumps({"type": "error", "text": "no model has a key yet: add one under API keys"}))
            return
        self.publish({"type": "speedtest", "state": "started", "vendors": [p["vendor"] for p in providers]})

        async def go():
            try:
                results = await speedtest.run(
                    providers, make=lambda vendor, model: speedtest._real_provider(vendor, model),
                    on_result=lambda row: self.publish({"type": "speedtest", "state": "result", "result": row}))
                self.publish({"type": "speedtest", "state": "finished", "results": results,
                              "fastest": speedtest.fastest(results)})
            except Exception as e:                 # whatever happens, the window stops waiting
                self.publish({"type": "speedtest", "state": "finished", "results": [], "fastest": None,
                              "error": str(e)})

        self._speed_task = asyncio.create_task(go())

    async def handle_role(self, ws, msg: dict) -> None:
        """A role described in words, written up by the saved model and added to the role list."""
        s = user_settings.load()
        try:
            mode = user_settings.clean_mode(msg.get("mode") or s.mode)
            provider = roles.make_provider(s.vendor, s.model)
        except Exception as e:
            await ws.send(json.dumps({"type": "error", "text": f"cannot analyze the role: {e}"}))
            return
        try:
            brief = await roles.analyze(msg.get("description") or "", mode, provider)
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        finally:
            await provider.close()
        await ws.send(json.dumps({"type": "role", "mode": mode, **brief}, ensure_ascii=False))
        self._publish_settings()

    async def handle_calls(self, ws) -> None:
        """The saved calls, newest first (roadmap item 7)."""
        await ws.send(json.dumps({"type": "calls", "calls": review.list_calls()}, ensure_ascii=False))

    async def handle_jobfit(self, ws, msg: dict) -> None:
        """Job description gap analysis (roadmap item 23): with a `description`, the saved model
        compares it with the CV and it becomes an interview document; with `clear`, both go; with
        neither, the last comparison comes back, which is what the window asks for when it opens.

        Between calls only. It is a long request against the same model the call uses, and during a
        call it would sit in front of the question the user is waiting on."""
        if msg.get("clear"):
            jobfit.clear()
            await ws.send(json.dumps({"type": "jobfit", "jobfit": None}))
            # Not just the window: this removes an interview document, and clearing is allowed
            # during a call (only the comparison itself is refused then). Without this a call
            # running when it was cleared would keep answering from a job description the user
            # had just taken away.
            self._documents_changed("interview")
            return
        description = msg.get("description")
        if not description:
            await ws.send(json.dumps({"type": "jobfit", "jobfit": jobfit.load()}, ensure_ascii=False))
            return
        if self.running:
            await ws.send(json.dumps({"type": "error", "text": "a call is running: compare the job "
                                                               "description with your CV between calls"}))
            return
        try:
            jobfit.check(description)      # a line of text, or no CV: refused before a model is asked for
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False))
            return
        s = user_settings.load()
        try:
            provider = roles.make_provider(s.vendor, s.model)
        except Exception as e:
            await ws.send(json.dumps({"type": "error", "text": f"cannot reach the model: {e}"}))
            return
        try:
            result = await jobfit.analyze(description, provider, lang=s.lang)
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False))
            return
        finally:
            await provider.close()
        await ws.send(json.dumps({"type": "jobfit", "jobfit": result}, ensure_ascii=False))
        self._documents_changed("interview")        # the job description is now a document

    async def handle_call(self, ws, msg: dict) -> None:
        """One saved call with its review and notes; with "review" or "notes" (item 19), the saved model
        writes that first (or again)."""
        name = msg.get("file")
        try:
            call = review.load_call(name)
        except ValueError as e:
            await ws.send(json.dumps({"type": "call_error", "file": name, "text": str(e)}))
            return
        asked = [run for key, run in (("review", review.review_call), ("notes", review.notes_call)) if msg.get(key)]
        if asked:
            s = user_settings.load()
            try:
                provider = roles.make_provider(s.vendor, s.model)
            except Exception as e:
                await ws.send(json.dumps({"type": "call_error", "file": name, "text": f"cannot reach the model: {e}"}))
                return
            try:
                for run in asked:
                    await run(name, provider, again=bool(msg.get("again")))
                call = review.load_call(name)
            except ValueError as e:
                await ws.send(json.dumps({"type": "call_error", "file": name, "text": str(e)}, ensure_ascii=False))
                return
            finally:
                await provider.close()
        await ws.send(json.dumps({"type": "call", **call}, ensure_ascii=False))
        if asked:
            await self.handle_calls(ws)            # the list says which calls have a review

    async def handle_role_delete(self, ws, name) -> None:
        try:
            roles.delete(str(name or ""))
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        s = user_settings.load()
        if s.preset == name:                       # the saved choice cannot point at a deleted file
            s.preset = ""
            user_settings.save(s)
        self._publish_settings()

    async def handle_dictate(self, ws, action) -> None:
        """Say the role into the microphone instead of typing it. The text goes back to the window
        that asked, as it is heard, and nowhere else; nothing is stored."""
        current = getattr(self, "_dictation", None)
        if action == "stop":
            if current is not None:
                current.stop()
            return
        if self.running:
            await ws.send(json.dumps({"type": "error", "text": "the microphone belongs to the call right now: type the role instead, or end the call first"}))
            return
        if current is not None and current.running:
            await ws.send(json.dumps({"type": "error", "text": "already listening"}))
            return
        s = user_settings.load()
        if s.stt_vendor == "whisper":
            # Speaking a role uses Deepgram; a user who set listening to this computer chose that
            # nothing they say leaves it (README "Privacy"), so the microphone is not streamed out
            # behind that choice (audit F10).
            await ws.send(json.dumps({"type": "dictation", "state": "error",
                                      "text": "Speaking the role uses Deepgram, and Listening is set to "
                                              "this computer: type the role instead."}))
            return

        def forward(event: dict) -> None:
            asyncio.ensure_future(ws.send(json.dumps(event, ensure_ascii=False)))

        self._dictation = Dictation(forward, lang=s.lang if s.lang != "multi" else "en", mic=s.mic_device)
        asyncio.create_task(self._dictation.run())

    async def handle_usage(self, ws, reset: bool = False) -> None:
        if reset:
            usage.reset()
        await ws.send(json.dumps({"type": "usage", **usage.summary()}))

    async def handle_keys(self, ws, values) -> None:
        """Keys pasted into the settings window (M3 step 5): saved on this machine and set for the
        next call. Every window is then told which keys are set, never a value, which goes no
        further than this method."""
        try:
            user_settings.save_keys(values)
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        self._publish_settings()

    async def handle_provider_config(self, ws, cmd: str, msg: dict) -> None:
        """The API keys page (2026-10-16): change a provider, add an OpenAI-compatible endpoint (and
        its key, if it has one), or remove one that was added. Every window is then told."""
        try:
            if cmd == "provider_save":
                user_settings.save_provider(str(msg.get("vendor") or ""), msg.get("values") or {})
            elif cmd == "provider_add":
                v = msg.get("values") or {}
                pid = user_settings.add_custom_provider(v.get("label"), v.get("base_url"), v.get("model"))
                if (v.get("key") or "").strip():
                    from .llm.openai_compat import custom_key_env
                    user_settings.save_keys({custom_key_env(pid): v["key"]})
            else:
                user_settings.remove_custom_provider(str(msg.get("vendor") or ""))
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        self._publish_settings()

    async def handle_scan_local(self, ws) -> None:
        """Look for model servers on this computer (Ollama, LM Studio, llama.cpp, vLLM, Jan) at the
        addresses set for them. One that answers is turned on, so it is offered in the model list."""
        from .llm.openai_compat import all_vendors
        local = [vid for vid, v in all_vendors().items() if v.get("local")]
        results = await asyncio.gather(*(check_provider(vid, timeout=2.0) for vid in local))
        for r in results:
            if r["ok"]:
                values = {"enabled": True}
                if not all_vendors()[r["vendor"]].get("model") and r["models"]:
                    values["model"] = r["models"][0]
                user_settings.save_provider(r["vendor"], values)
        await ws.send(json.dumps({"type": "scan_local", "results": results}))
        self._publish_settings()

    async def handle_devices(self, ws) -> None:
        """The speakers and microphones there are, for the settings window. Listing opens PortAudio,
        which takes a moment, so it runs off the event loop; only the window that asked is answered."""
        try:
            found = await asyncio.to_thread(list_devices)
        except Exception as e:
            await ws.send(json.dumps({"type": "error", "text": f"could not list audio devices: {e}"}))
            return
        await ws.send(json.dumps({"type": "devices", "speakers": found["speakers"], "mics": found["mics"]},
                                 ensure_ascii=False))

    async def start_session(self, chosen: dict) -> None:
        if self.running:
            return
        s = user_settings.load()
        for key in ("preset", "lang", "vendor", "model", "mic", "user_mic", "suggest", "speculate",
                    "style", "mode", "speaker_device", "mic_device"):
            if key in chosen:
                setattr(s, key, chosen[key])
        user_settings.save(s)                     # the next call opens on these choices
        if self.preflight is not None:
            # A model that fails is found here, not on the interviewer's first question.
            if self._checking:
                return                            # a second click while the first is checked
            self._checking = True
            self.publish({"type": "notice", "text": "Checking the model and the listening service"})
            try:
                problems = await self.preflight(chosen)
            finally:
                self._checking = False
            if problems:
                raise RuntimeError("The call was not started. " + " ".join(f"{p}." for p in problems))
        self.engine = self.make_engine(chosen, self.publish)
        self.session = asyncio.create_task(self._run_session())

    async def _run_session(self) -> None:
        try:
            await self.engine.run()
        except Exception as e:                    # a bad device or a missing key must not kill the server
            self.publish({"type": "status", "state": "stopped", "error": str(e)})
        finally:
            self.engine = None
            self.idle()                           # ready for the next call, no restart needed

    # ---- inbound ----
    async def handle_command(self, ws, raw: str) -> None:
        """One message from a window: check its shape, run it, and whatever goes wrong answer the
        sender with an `error` event instead of letting the exception close the connection."""
        try:
            msg = json.loads(raw)
        except ValueError:                           # bad JSON, or bytes that are not UTF-8
            await ws.send(json.dumps({"type": "error", "text": "not JSON"}))
            return
        if not isinstance(msg, dict):
            await ws.send(json.dumps({"type": "error", "text": 'a command is a JSON object, like {"cmd": "ping"}'}))
            return
        try:
            await self._dispatch(ws, msg)
        except websockets.ConnectionClosed:
            raise                                    # the sender left; nobody to tell
        except ValueError as e:                      # bad input: the message is meant for the user
            await ws.send(json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False))
        except Exception as e:                       # a bug or a full disk: engine.log gets the traceback
            traceback.print_exc()
            await ws.send(json.dumps({"type": "error", "text": f"{msg.get('cmd')}: {type(e).__name__}: {e}"},
                                     ensure_ascii=False))

    async def _dispatch(self, ws, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "start":
            if self.running:
                await ws.send(json.dumps({"type": "error", "text": "a session is already running"}))
                return
            try:
                await self.start_session(_object(msg.get("settings"), "start.settings"))
            except Exception as e:                # a missing preset, say: report it and stay idle
                await ws.send(json.dumps({"type": "error", "text": str(e)}))
                self.idle()
        elif cmd == "provider":
            if self.engine is None or not hasattr(self.engine, "set_provider"):
                await ws.send(json.dumps({"type": "error", "text": "no session to switch"}))
                return
            vendor, model = msg.get("vendor") or "", msg.get("model") or None
            try:
                await self.engine.set_provider(vendor, model)
            except Exception as e:                # no key, unknown vendor: say so, keep the old one
                await ws.send(json.dumps({"type": "error", "text": f"could not switch: {e}"}))
                return
            s = user_settings.load()
            s.vendor, s.model = vendor, model
            user_settings.save(s)                 # the next call starts on the model that worked
        elif cmd == "more":
            if self.engine is None or not hasattr(self.engine, "more"):
                await ws.send(json.dumps({"type": "error", "text": "no session to ask more of"}))
                return
            try:
                self.engine.more(int(msg.get("n")))
            except (TypeError, ValueError):
                await ws.send(json.dumps({"type": "error", "text": "more needs a question number"}))
        elif cmd == "action":
            if self.engine is None or not hasattr(self.engine, "action"):
                await ws.send(json.dumps({"type": "error", "text": "no session for that"}))
                return
            try:
                n = msg.get("n")
                self.engine.action(str(msg.get("kind") or ""), None if n is None else int(n))
            except (TypeError, ValueError):
                await ws.send(json.dumps({"type": "error", "text": "action needs a question number"}))
        elif cmd == "settings":
            await self.handle_settings(ws, _object(msg.get("values"), "settings.values"))
        elif cmd == "cv":
            await self.handle_cv(ws, msg)
        elif cmd == "keys":
            await self.handle_keys(ws, msg.get("values"))
        elif cmd in ("provider_save", "provider_add", "provider_remove"):
            await self.handle_provider_config(ws, cmd, msg)
        elif cmd == "provider_check":
            await ws.send(json.dumps({"type": "provider_check", **await check_provider(str(msg.get("vendor") or ""))}))
        elif cmd == "scan_local":
            await self.handle_scan_local(ws)
        elif cmd == "probe_models":
            from .preflight import probe_models
            vendor = str(msg.get("vendor") or "")
            models = [str(m) for m in (msg.get("models") or []) if isinstance(m, str)]
            for r in await probe_models(vendor, models):
                await ws.send(json.dumps({"type": "probe", **r}))
            await ws.send(json.dumps({"type": "probe_done", "vendor": vendor}))
        elif cmd == "devices":
            await self.handle_devices(ws)
        elif cmd == "speedtest":
            await self.handle_speedtest(ws)
        elif cmd == "role":
            await self.handle_role(ws, msg)
        elif cmd == "role_delete":
            await self.handle_role_delete(ws, msg.get("name"))
        elif cmd == "dictate":
            await self.handle_dictate(ws, msg.get("action"))
        elif cmd in ("usage", "usage_reset"):
            await self.handle_usage(ws, reset=cmd == "usage_reset")
        elif cmd == "jobfit":
            await self.handle_jobfit(ws, msg)
        elif cmd == "calls":
            await self.handle_calls(ws)
        elif cmd == "call":
            await self.handle_call(ws, msg)
        elif cmd == "transcripts_delete":
            user_settings.delete_transcripts()
            self._publish_settings()
        elif cmd == "ping":
            await ws.send(json.dumps({"type": "pong"}))
        elif cmd in ("pause", "resume", "stop", "retry"):
            if self.engine is None:
                await ws.send(json.dumps({"type": "error", "text": f"no session to {cmd}"}))
                return
            if cmd == "retry":
                try:
                    self.engine.retry(int(msg.get("n")))
                except (TypeError, ValueError):
                    await ws.send(json.dumps({"type": "error", "text": "retry needs a question number"}))
            else:
                getattr(self.engine, cmd)()
        else:
            await ws.send(json.dumps({"type": "error", "text": f"unknown command: {cmd!r}"}))

    async def handle_settings(self, ws, values: dict) -> None:
        """Save choices from the settings window, apply what can change mid-call, tell every window.

        The overlay's own preferences (animation, text size) are stored here too, so the settings
        window and the overlay - two windows on two sockets - agree without talking to each other."""
        s = user_settings.load()
        changed = {k: v for k, v in values.items() if k in user_settings.EDITABLE}
        try:
            if "opacity" in changed:
                changed["opacity"] = user_settings.clamp_opacity(changed["opacity"])
            if "hotkeys" in changed:
                changed["hotkeys"] = user_settings.clean_hotkeys(changed["hotkeys"], s.hotkeys)
            if "theme" in changed:
                changed["theme"] = user_settings.clean_theme(changed["theme"])
            if "ui_lang" in changed:
                changed["ui_lang"] = user_settings.clean_ui_lang(changed["ui_lang"])
            if "save_transcripts" in changed:
                changed["save_transcripts"] = bool(changed["save_transcripts"])
            if "pace_meter" in changed:
                changed["pace_meter"] = bool(changed["pace_meter"])
            if "live_feedback" in changed:
                changed["live_feedback"] = bool(changed["live_feedback"])
            if "negotiation" in changed:
                changed["negotiation"] = bool(changed["negotiation"])
            if "stt_vendor" in changed and changed["stt_vendor"] not in ("deepgram", "whisper"):
                raise ValueError(f"unknown stt_vendor: {changed['stt_vendor']!r}")
            if "whisper_model" in changed:
                changed["whisper_model"] = user_settings.clean_whisper_model(changed["whisper_model"])
            if "mode" in changed:
                changed["mode"] = user_settings.clean_mode(changed["mode"])
                # A role saved for the other kind of call would stop the next call from starting
                # (ContextBuilder refuses the pair), so changing the mode lets go of it.
                role = changed.get("preset", s.preset)
                if role and available_modes().get(role, "interview") != changed["mode"]:
                    changed["preset"] = ""
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        if "style" in changed and self.engine is not None and hasattr(self.engine, "set_style"):
            try:
                self.engine.set_style(changed["style"])
            except Exception as e:
                await ws.send(json.dumps({"type": "error", "text": f"could not change the answer style: {e}"}))
                return
        for k, v in changed.items():
            setattr(s, k, v)
        user_settings.save(s)
        self._publish_settings()

    def _documents_changed(self, mode: str) -> None:
        """A document was added or removed. Between calls that redraws the setup screen. During a
        call of the same kind the running engine gets them, so the next question uses them; during
        any call the windows get a settings event instead of idle, which would throw the overlay
        back to the setup screen in the middle of the interview."""
        if self.running and self.engine is not None:
            if getattr(self.engine, "mode", None) == mode and hasattr(self.engine, "set_documents"):
                self.engine.set_documents(user_settings.load_documents(mode))
            self._publish_settings()
        else:
            self.idle()

    async def handle_cv(self, ws, msg: dict) -> None:
        """Documents for one kind of call (roadmap item 5): add one (`name` and base64 `data`),
        remove one (`remove`), or remove them all (neither). The kind is `mode`, else the saved one."""
        try:
            mode = user_settings.clean_mode(msg.get("mode") or user_settings.load().mode)
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        name = msg.get("name")
        if not name:
            user_settings.remove_document(mode, msg.get("remove") or None)
            self._documents_changed(mode)
            return
        data = msg.get("data") or ""
        if not isinstance(name, str) or not isinstance(data, str):
            raise ValueError("cv needs a file name and base64 data")
        if len(data) * 3 // 4 > MAX_UPLOAD_BYTES:
            await ws.send(json.dumps({"type": "error", "text": f"{name} is larger than "
                                      f"{MAX_UPLOAD_BYTES // 2**20} MB: use a smaller file"}, ensure_ascii=False))
            return
        try:
            # A worker thread: reading a PDF or DOCX takes seconds, and the loop that carries the
            # call's audio and captions must not wait for it.
            text = await asyncio.to_thread(_read_document, name, data)
        except Exception as e:
            await ws.send(json.dumps({"type": "error", "text": f"could not read {name}: {e}"}))
            return
        try:
            user_settings.add_document(mode, name, text)      # refuses no text, or over the budget
        except ValueError as e:
            await ws.send(json.dumps({"type": "error", "text": str(e)}))
            return
        self._documents_changed(mode)

    async def handler(self, ws) -> None:
        self.clients.add(ws)
        try:
            await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL,
                                      "providers": available_providers()}))
            if self.last_status is not None:
                await ws.send(json.dumps(self.last_status, ensure_ascii=False))
            for event in list(self.history):
                await ws.send(json.dumps(event, ensure_ascii=False))
            # Then the settings as saved now (2026-09-12). A window otherwise gets them only from the
            # idle status or a settings event, and HISTORY events into a call both have scrolled
            # out: a settings window opened then showed every control at its default. Last, because
            # the history can be older than the file (the choices made at Start are saved without
            # an event). A settings event, not idle, which would send the overlay to the setup screen.
            await ws.send(json.dumps(self._settings_event(), ensure_ascii=False))
            async for raw in ws:
                await self.handle_command(ws, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self.clients.discard(ws)

    # ---- who may connect ----
    def _check_host(self, connection, request):
        """Refuse a request whose Host is not loopback (DNS rebinding). Returning a response
        ends the handshake; None lets it continue to the Origin check and the handler."""
        host = (request.headers.get("Host") or "").strip().lower()
        if host.startswith("["):                       # [::1]:8765
            name = host[1:].split("]", 1)[0]
        else:
            name = host.rsplit(":", 1)[0] if ":" in host else host
        if name not in LOOPBACK_HOSTS:
            return connection.respond(HTTPStatus.FORBIDDEN, "host not allowed\n")
        return None

    # ---- lifecycle ----
    async def run(self) -> None:
        fanout = asyncio.create_task(self._fanout())
        # origins: None allows a client that sends no Origin (not a browser), anything else must
        # be listed. websockets answers a listed-origin mismatch with 403 before the handler runs.
        # max_size: a document goes over as one base64 message (4/3 of the file), so a frame has to
        # hold the largest upload the app accepts; websockets' default of 1 MiB closed the socket on
        # an ordinary PDF CV.
        async with serve(self.handler, self.host, self.port, origins=[None, *self.origins],
                         process_request=self._check_host, max_size=2 * MAX_UPLOAD_BYTES) as server:
            self.port = server.sockets[0].getsockname()[1]     # resolves port 0 for tests
            print(f"[server] ws://{self.host}:{self.port}  (protocol {PROTOCOL})", flush=True)
            self.idle()
            try:
                if self.autostart is not None:
                    await self.start_session(self.autostart)
                    await self.session
                else:
                    await asyncio.Future()          # serve until the process is stopped
            finally:
                fanout.cancel()
                if self.running:
                    self.engine.stop()
                for ws in list(self.clients):
                    await ws.close()


def session_log_path(folder: str, now: "datetime.datetime | None" = None) -> str:
    """A fresh log for one call (M3 step 5): <folder>/<date>_<time>.jsonl, never a file an earlier
    call wrote. Only used while transcript saving is on."""
    import datetime
    os.makedirs(folder, exist_ok=True)
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(folder, f"{stamp}.jsonl")
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stamp}-{n}.jsonl")
        n += 1
    return path


def engine_factory(base: EngineConfig, sessions: Optional[str]):
    """The function the server calls to build an engine for one call."""
    def make_engine(chosen, on_event):
        cfg = config_from(base, chosen)
        if sessions is not None:
            # A call's logs hold everything said in it, so they are kept only when the user turned
            # on "save a transcript"; otherwise nothing is written (os.devnull).
            if user_settings.load().save_transcripts:
                cfg.log_path, cfg.raw_path = session_log_path(sessions), None
            else:
                cfg.log_path, cfg.raw_path = os.devnull, os.devnull
        return Engine(cfg, on_event)
    return make_engine


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", nargs="?", const="", default=None, metavar="FOLDER",
                    help="log each call to its own FOLDER/<date>_<time>.jsonl (default folder: "
                         "%%APPDATA%%/Fawa2ly/sessions), but only while transcript saving is on; the installed app passes this")
    ap.add_argument("--host", default=HOST, help="loopback only: there is no authentication, so any other address is refused")
    ap.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                    help="also accept pages from this origin (for example http://localhost:8080); repeatable")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--idle", action="store_true",
                    help="do not start a session; wait for the UI's setup screen to start one")
    ap.add_argument("--mic", action="store_true")
    ap.add_argument("--user-mic", action="store_true")
    ap.add_argument("--lang", default="multi")
    ap.add_argument("--log", default="session.jsonl")
    ap.add_argument("--raw", default=None)
    ap.add_argument("--seconds", type=float, default=0)
    ap.add_argument("--suggest", action="store_true")
    ap.add_argument("--vendor", default="gemini")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--stt-vendor", default="deepgram", choices=("deepgram", "whisper"),
                    help="whisper: transcribe locally (roadmap item 26), no Deepgram key needed")
    ap.add_argument("--whisper-model", default=None,
                    help="faster-whisper size with --stt-vendor whisper (default: small)")
    ap.add_argument("--preset", default=None)
    ap.add_argument("--cv", default=None)
    ap.add_argument("--fake-llm", action="store_true")
    ap.add_argument("--fake-delay", type=float, default=0.0,
                    help="with --fake-llm, seconds before the first line (to see the waiting state)")
    ap.add_argument("--replay", default=None,
                    help="replay a recorded <session>.raw.jsonl instead of listening (UI work, demos)")
    ap.add_argument("--no-speculate", action="store_true",
                    help="do not ask the model before a turn closes (saves requests on rate-limited tiers)")
    a = ap.parse_args(argv)
    safe_console()
    base = EngineConfig(lang=a.lang, log_path=a.log, raw_path=a.raw, mic=a.mic, user_mic=a.user_mic,
                        seconds=a.seconds, suggest=a.suggest, vendor=a.vendor, llm_model=a.llm_model,
                        stt_vendor=a.stt_vendor, whisper_model=a.whisper_model,
                        preset=a.preset, cv=a.cv, fake_llm=a.fake_llm, fake_delay=a.fake_delay,
                        replay=a.replay, speculate=not a.no_speculate)
    sessions = None if a.sessions is None else (a.sessions or user_settings.sessions_dir())

    make_engine = engine_factory(base, sessions)

    try:
        server = SessionServer(make_engine, host=a.host, port=a.port, autostart=None if a.idle else {},
                               allow_origins=tuple(a.allow_origin), preflight=preflight_call)
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
