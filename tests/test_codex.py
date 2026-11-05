"""Codex as a provider and a searcher (2026-11-05), against a fake `codex app-server`, plus
answering only on the hotkey."""

import asyncio
import json
import os
import sys
import textwrap

from fawa2ly.assist import Suggester
from fawa2ly.llm import FakeLLM, collect
from fawa2ly.llm.base import SuggestionRequest
from fawa2ly.llm.codex import CodexLLM, codex_ready
from fawa2ly.search import TavilySearch, make_searcher
from fawa2ly.search.codex import CodexSearch
from fawa2ly.pipeline import Utterance

# Answers each request the way app-server 0.154 does. The user text picks the script: "fail" makes
# the turn fail, "search" answers with JSON results; anything else streams a commentary message
# (which must not reach the screen) and then two lines.
FAKE_SERVER = textwrap.dedent(r'''
    import json, sys
    def out(m): sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line); i = m.get("id"); method = m.get("method"); p = m.get("params") or {}
        if method == "initialize": out({"id": i, "result": {}})
        elif method == "account/read": out({"id": i, "result": {"account": {"type": "chatgpt"}}})
        elif method == "thread/start":
            assert p["ephemeral"] and p["baseInstructions"] and p["sandbox"] == "read-only"
            out({"id": i, "result": {"thread": {"id": "t1"}, "model": p["model"]}})
        elif method == "turn/start":
            text = p["input"][0]["text"]
            out({"id": i, "result": {}})
            ev = lambda method, **kw: out({"method": method, "params": {"threadId": "t1", **kw}})
            if "fail" in text:
                ev("turn/completed", turn={"status": "failed", "error": {"message": "usage limit reached"}})
                continue
            if "search" in text:
                assert p["outputSchema"]
                body = {"results": [{"title": "T", "url": "https://www.example.com/a", "content": "A fact."},
                                    {"title": "Empty", "url": "https://x.com", "content": " "}]}
                ev("item/agentMessage/delta", itemId="m", delta=json.dumps(body))
            else:
                ev("item/started", item={"type": "agentMessage", "id": "c", "phase": "commentary"})
                ev("item/agentMessage/delta", itemId="c", delta="Thinking about it")
                ev("item/agentMessage/delta", itemId="m", delta="- first line\n- sec")
                ev("item/agentMessage/delta", itemId="m", delta="ond line")
            ev("thread/tokenUsage/updated", tokenUsage={"total": {"inputTokens": 100, "outputTokens": 9}})
            ev("turn/completed", turn={"status": "completed", "error": None})
        elif i is not None: out({"id": i, "result": {}})
''')


def fake_codex(tmp_path) -> CodexLLM:
    script = tmp_path / "fake_app_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return CodexLLM(command=[sys.executable, str(script)])


def test_codex_streams_the_answer_skips_commentary_and_reports_failures(tmp_path):
    async def run():
        llm = fake_codex(tmp_path)
        ok = await collect(llm, SuggestionRequest("Tell me about yourself", "system prompt"))
        failed = await collect(llm, SuggestionRequest("fail please", "system prompt"))
        searched = await CodexSearch(llm).search("search  something ")
        await llm.close()
        return ok, failed, searched
    ok, failed, searched = asyncio.run(run())
    assert ok.error is None and ok.bullets == ["first line", "second line"]
    assert ok.provider == "codex" and ok.usage == {"input_tokens": 100, "output_tokens": 9}
    assert "usage limit reached" in failed.error
    assert searched.error is None and [(r.site, r.content) for r in searched.results] == [("example.com", "A fact.")]


def test_codex_is_found_only_when_installed_and_signed_in_and_search_falls_back_to_it(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)   # an earlier test may have loaded .env
    monkeypatch.setattr("shutil.which", lambda name: "codex.exe")
    assert not codex_ready()                               # installed, never signed in
    (home / "auth.json").write_text("{}")
    assert codex_ready()
    assert isinstance(make_searcher(), CodexSearch)        # no Tavily key: Codex searches
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert isinstance(make_searcher(), TavilySearch)       # a key is faster, so it wins


def test_hotkey_only_answers_nothing_heard_but_the_say_action_uses_it():
    llm = FakeLLM()
    s = Suggester(llm)
    s.hotkey_only = True
    u = Utterance(n=1, speaker=0, trigger="question_mark", text="Why do you want this job?", segments=1,
                  t_first_final=0.0, t_last_final=0.0, t_flush=0.0, gap_ms=0, speech_to_text_ms=None)
    assert asyncio.run(s.handle(u)) is None and s.n == 0
    assert s.history and "Why do you want this job?" in s.history[-1][1]
    card = asyncio.run(s.action("say"))
    assert card is not None and s.n == 1


def test_turning_hotkey_only_off_during_a_call_answers_the_next_question_at_once(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fawa2ly.server import SessionServer
    monkeypatch.setenv("APPDATA", str(tmp_path))
    server = SessionServer(lambda chosen, emit: None, host="127.0.0.1", port=0)
    suggester = Suggester(FakeLLM())
    suggester.hotkey_only = True
    server.engine = SimpleNamespace(suggester=suggester)

    class Ws:
        async def send(self, text):
            raise AssertionError(text)
    asyncio.run(server.handle_settings(Ws(), {"hotkey_only": False}))
    assert suggester.hotkey_only is False
