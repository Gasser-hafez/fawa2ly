"""
CodexLLM: answers through the Codex CLI already installed and signed in on this machine, so a user
with a ChatGPT plan needs no API key at all (maintainer's request 2026-11-05, after seeing another
assistant pick Codex up on its own at setup).

It talks to `codex app-server`, the server the Codex IDE extension uses: JSON-RPC, one JSON object
per line on stdin and stdout (protocol read from `codex app-server generate-ts`, CLI 0.154.0). One
process lives as long as the provider; each request is an ephemeral thread of its own, so the
system prompt is ours (baseInstructions) and no answer sees another's history.

Measured 2026-11-05 on a Plus account: thread/start 0.08 s; gpt-5.6-luna at effort "low" gave a
first token in 1.8-2.4 s with ~3.5k input tokens once Codex's own tools are switched off. With
them on (the user's plugins, browser, shell...) the same question cost 11.9k tokens and 3.9 s, so
they are disabled below. code_mode_host stays on: live web search stopped working without it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from typing import AsyncIterator, Optional

from .base import SuggestionEvent, SuggestionProvider, SuggestionRequest
from .prompts import user_content

DEFAULT_MODEL = "gpt-5.6-luna"     # the fastest of the four the Plus account lists
EFFORT = "low"
IDLE_TIMEOUT_S = 60.0              # no word from Codex for this long and the answer is given up
DISABLED_FEATURES = ("apps", "plugins", "remote_plugin", "browser_use", "computer_use", "multi_agent",
                     "image_generation", "in_app_browser", "shell_tool", "unified_exec", "goals", "hooks",
                     "skill_search", "tool_suggest", "view_image", "sleep_tool", "personality",
                     "shell_snapshot", "guardian_approval", "workspace_dependencies")


def codex_home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def codex_ready() -> bool:
    """Codex is installed and has been signed in, judged from files, without starting it."""
    # ponytail: sign-in seen through auth.json only; one kept in the OS keyring reads as "not
    # signed in". Probe with account/read if that ever comes up.
    return bool(shutil.which("codex")) and os.path.isfile(os.path.join(codex_home(), "auth.json"))


class CodexLLM(SuggestionProvider):
    name = "codex"

    def __init__(self, model: Optional[str] = None, web_search: bool = False, effort: str = EFFORT,
                 command: Optional[list] = None):
        self.model = model or DEFAULT_MODEL
        self.web_search = web_search
        self.effort = effort
        self.command = command         # what starts the server; a test hands in a fake one
        self._proc = None
        self._reader = None
        self._lock = asyncio.Lock()
        self._next = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._threads: dict[str, asyncio.Queue] = {}

    # ---- the process ----
    def _send(self, msg: dict) -> None:
        if self._proc is not None:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))

    async def _call(self, method: str, params: dict) -> dict:
        self._next += 1
        i = self._next
        fut = asyncio.get_running_loop().create_future()
        self._pending[i] = fut
        self._send({"id": i, "method": method, "params": params})
        m = await asyncio.wait_for(fut, IDLE_TIMEOUT_S)
        if "error" in m:
            raise RuntimeError(f"Codex {method}: {(m['error'] or {}).get('message')}")
        return m.get("result") or {}

    async def _read(self, proc) -> None:
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if "id" in m and "method" in m:
                    # the server asking for something (an approval); this provider grants nothing
                    self._send({"id": m["id"], "error": {"code": -32601, "message": "not supported"}})
                elif "id" in m:
                    fut = self._pending.pop(m["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(m)
                else:
                    q = self._threads.get((m.get("params") or {}).get("threadId"))
                    if q is not None:
                        q.put_nowait(m)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"error": {"message": "Codex stopped"}})
            self._pending.clear()
            for q in self._threads.values():
                q.put_nowait(None)

    async def _ensure(self) -> None:
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                return
            args = self.command
            if args is None:
                exe = shutil.which("codex")
                if not exe:
                    raise RuntimeError("Codex is not installed (no codex on PATH)")
                args = [exe, "app-server", "-c", "notify=[]", "-c", "mcp_servers={}"]
                for f in DISABLED_FEATURES:
                    args += ["--disable", f]
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                limit=1 << 24, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._reader = asyncio.create_task(self._read(self._proc))
            try:
                await self._call("initialize", {"clientInfo": {"name": "fawa2ly", "title": "Fawa2ly",
                                                               "version": "1"}, "capabilities": None})
                self._send({"method": "initialized", "params": {}})
                if not (await self._call("account/read", {})).get("account"):
                    raise RuntimeError("Codex is installed but not signed in: run `codex login`")
            except BaseException:
                await self.close()
                raise

    # ---- requests ----
    async def list_models(self) -> list[str]:
        await self._ensure()
        return [m["id"] for m in (await self._call("model/list", {})).get("data") or [] if m.get("id")]

    async def suggest(self, req: SuggestionRequest) -> AsyncIterator[SuggestionEvent]:
        content = (req.user if req.user is not None
                   else user_content(req.question, req.history, lead=req.lead))
        async for ev in self.ask(req.system, content):
            yield ev

    async def ask(self, system: str, user: str, web_search: Optional[bool] = None,
                  schema: Optional[dict] = None) -> AsyncIterator[SuggestionEvent]:
        """One turn on a fresh thread: first_token / token* / done, or error. Never raises.
        max_tokens has no counterpart in app-server; the system prompt keeps answers short."""
        model = self.model
        tid = None
        search = self.web_search if web_search is None else web_search
        try:
            await self._ensure()
            r = await self._call("thread/start", {
                "ephemeral": True, "model": self.model, "baseInstructions": system,
                "sandbox": "read-only", "approvalPolicy": "never", "cwd": tempfile.gettempdir(),
                "config": {"web_search": "live" if search else "disabled"}})
            tid = r["thread"]["id"]
            model = r.get("model") or model
            q = self._threads[tid] = asyncio.Queue()
            params = {"threadId": tid, "effort": self.effort,
                      "input": [{"type": "text", "text": user, "text_elements": []}]}
            if schema is not None:
                params["outputSchema"] = schema
            await self._call("turn/start", params)
            first, usage, commentary = True, None, set()
            while True:
                m = await asyncio.wait_for(q.get(), IDLE_TIMEOUT_S)
                if m is None:
                    raise RuntimeError("Codex stopped mid-answer")
                method, p = m.get("method"), m.get("params") or {}
                item = p.get("item") or {}
                if method == "item/started" and item.get("phase") == "commentary":
                    commentary.add(item.get("id"))     # "Searching..." and the like, not the answer
                elif method == "item/agentMessage/delta" and p.get("delta") and p.get("itemId") not in commentary:
                    yield SuggestionEvent("first_token" if first else "token", time.time(), p["delta"], model=model)
                    first = False
                elif method == "thread/tokenUsage/updated":
                    total = (p.get("tokenUsage") or {}).get("total") or {}
                    usage = {"input_tokens": total.get("inputTokens"), "output_tokens": total.get("outputTokens")}
                elif method == "turn/completed":
                    turn = p.get("turn") or {}
                    if turn.get("status") == "failed":
                        raise RuntimeError((turn.get("error") or {}).get("message") or "Codex could not answer")
                    break
            yield SuggestionEvent("done", time.time(), usage=usage, model=model)
        except Exception as e:  # the provider contract: never raise, report
            yield SuggestionEvent("error", time.time(), f"{type(e).__name__}: {e}", model=model)
        finally:
            if tid is not None:
                self._threads.pop(tid, None)
                if self._proc is not None and self._proc.returncode is None:
                    self._next += 1                     # the reply is not waited for
                    self._send({"id": self._next, "method": "thread/unsubscribe", "params": {"threadId": tid}})

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
