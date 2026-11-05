"""Web search during a call (roadmap item 10, 2026-09-12).

A search adds seconds, so it never comes before an answer's lines: the model may end its answer with
"SEARCH: <query>", which is never shown, and the search runs once the answer is recorded; the card's
Search button does the same for any answer. No test here reaches a network: Tavily is answered by
httpx's mock transport, following the request and error shapes in its docs.
"""

import asyncio
import json

import httpx
import pytest

from fawa2ly import settings as user_settings
from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder, filter_bullets
from fawa2ly.context.guardrail import search_query
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.limits import SEARCH_MARK, SEARCH_RESULT_WORDS
from fawa2ly.llm import FakeLLM
from fawa2ly.llm.prompts import NO_YEAR, SEARCH_RULE, query_request, search_request
from fawa2ly.pipeline import Utterance
from fawa2ly.search import FakeSearch, SearchResult, TavilySearch, tavily
from fawa2ly.server import SessionServer, config_from
from fawa2ly.summary import CallRecord


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


class Scripted(FakeLLM):
    """One reply per request, in order."""

    def __init__(self, *replies, **kw):
        super().__init__(**kw)
        self.replies = list(replies)

    async def suggest(self, req):
        self.bullets = self.replies.pop(0) if self.replies else []
        async for ev in super().suggest(req):
            yield ev


def utt(n=1, text="What do you know about Acme?"):
    return Utterance(n=n, speaker=0, trigger="question_mark", text=text, segments=1,
                     t_first_final=0.0, t_last_final=0.0, t_flush=0.0, gap_ms=0,
                     speech_to_text_ms=None, source="turn", question_end_to_fire_ms=1500,
                     stream="other")


def make(llm, searcher, **kw):
    timeline, results, events, tasks = [], [], [], []

    def on_result(a):
        results.append(a)
        timeline.append(("assist", a.web))

    def on_search(n, info):
        events.append((n, info))
        timeline.append(("search", info["state"]))

    s = Suggester(llm, ContextBuilder(cv_text="Gasser, Flutter developer"),
                  on_line=lambda n, text: timeline.append(("line", text)), on_result=on_result,
                  on_search=on_search, searcher=searcher,
                  spawn=lambda coro: tasks.append(asyncio.ensure_future(coro)), **kw)
    return s, timeline, results, events, tasks


async def settle(tasks):
    while tasks:
        pending = list(tasks)
        tasks.clear()
        await asyncio.gather(*pending)


def test_a_search_line_is_never_shown_and_takes_no_line_s_place():
    kept, dropped = filter_bullets(["I would start with what they shipped this year",
                                    "SEARCH: acme q3 results", '**Search:** "acme layoffs 2026"', "SEARCH:"])
    assert kept == ["I would start with what they shipped this year"]
    assert [d.reason for d in dropped] == ["search", "search", "search"]
    assert search_query("SEARCH: acme q3 results") == "acme q3 results"
    assert search_query('**Search:** "acme layoffs 2026"') == "acme layoffs 2026"
    assert search_query("SEARCH:") is None
    assert search_query("Research: I led the study") is None
    assert SEARCH_MARK in SEARCH_RULE


def test_the_prompt_offers_search_only_on_a_call_that_has_it():
    b = ContextBuilder(cv_text="Gasser, Flutter developer")
    rated = b.system()
    assert SEARCH_MARK not in rated
    b.web_search = True
    assert b.system() == rated + "\n\n" + SEARCH_RULE, "last, and nothing else changes"


def test_the_rule_keeps_recent_claims_out_of_the_first_lines_and_years_out_of_queries():
    # the first live run (2026-09-12): a launch claimed before the search ran, and "2023 2024" in a query
    assert "state no such fact" in SEARCH_RULE and "not even as something seen or heard" in SEARCH_RULE
    assert "whatever language the conversation is in" in SEARCH_RULE
    assert NO_YEAR in SEARCH_RULE and NO_YEAR in query_request("Why us?", ["We like your product"])


def test_an_answer_that_asks_for_a_search_gets_one_after_its_lines_are_on_screen():
    llm = Scripted(["Acme is growing fast, which is why I applied", "I have shipped apps for teams that grow",
                    "SEARCH: acme latest results"],
                   ["Acme said revenue grew 12 percent last quarter", "It also opened two new offices"])
    search = FakeSearch()
    s, timeline, results, events, tasks = make(llm, search)

    async def go():
        await s.handle(utt())
        await settle(tasks)

    asyncio.run(go())
    assert search.queries == ["acme latest results"]
    assert not [t for kind, t in timeline if kind == "line" and "SEARCH" in str(t)]
    assert timeline.index(("search", "searching")) > timeline.index(("assist", False)), "never before the answer"
    first, added = results
    assert first.bullets == ["Acme is growing fast, which is why I applied", "I have shipped apps for teams that grow"]
    assert first.search_query == "acme latest results" and not first.web
    assert added.web and added.more and added.search_query == "acme latest results"
    assert added.bullets == ["Acme said revenue grew 12 percent last quarter", "It also opened two new offices"]
    assert [x["site"] for x in added.sources] == ["example.com", "example.org"]
    assert [info["state"] for _, info in events] == ["searching", "found"]
    asked = llm.requests[-1].question
    assert "From a web search just now" in asked and "revenue grew 12 percent" in asked
    assert "Acme is growing fast" in asked, "the lines on screen are not to be repeated"


def test_no_search_for_a_question_the_call_has_moved_past():
    llm = Scripted(["Acme builds apps for shops", "I like their focus", "SEARCH: first question"],
                   ["Their rivals are bigger", "Acme is faster to ship", "SEARCH: second question"],
                   ["Added from what the search found"], first_token_delay=0.02)
    search = FakeSearch()
    s, _, _, _, tasks = make(llm, search)

    async def go():
        await asyncio.gather(s.handle(utt(1)), s.handle(utt(2, "And what about their rivals?")))
        await settle(tasks)

    asyncio.run(go())
    assert search.queries == ["second question"]


def test_the_search_button_has_the_model_write_the_query_when_the_answer_asked_for_none():
    llm = Scripted(["I would bring up their newest app", "It shows I follow what they ship"],
                   ["SEARCH: acme new app launch"],
                   ["Their new app went on sale this month for small teams"])
    search = FakeSearch()
    s, _, _, events, tasks = make(llm, search)

    async def go():
        first = await s.handle(utt())
        await settle(tasks)
        assert not search.queries, "nothing asked for, nothing searched"
        return await s.search(first.n)

    added = asyncio.run(go())
    assert search.queries == ["acme new app launch"]
    assert "Reply with one line only" in llm.requests[1].question
    assert added.bullets == ["Their new app went on sale this month for small teams"]
    assert [(info["state"], info["query"]) for _, info in events] == [
        ("searching", ""), ("searching", "acme new app launch"), ("found", "acme new app launch")]


def test_each_question_is_searched_once_and_a_call_has_a_limit():
    llm = Scripted(["Line one to say here", "Line two to say here"], ["Added from the web here"],
                   ["Line three to say here", "Line four to say here"])
    search = FakeSearch()
    s, _, _, events, _ = make(llm, search, search_limit=1)

    async def go():
        a = await s.handle(utt(1))
        added = await s.search(a.n, "acme")
        again = await s.search(a.n, "acme again")
        b = await s.handle(utt(2, "Why do you want to work here?"))
        return added, again, await s.search(b.n, "anything")

    added, again, refused = asyncio.run(go())
    assert added is not None and again is None and refused is None
    assert search.queries == ["acme"]
    assert events[-1] == (2, {"state": "failed", "query": "", "error": "this call's 1 web searches are used"})


def test_a_failed_or_empty_search_says_why_and_costs_no_model_request():
    for searcher, reason in ((FakeSearch(error="the search took too long"), "the search took too long"),
                             (FakeSearch(results=[]), "the search found nothing")):
        llm = Scripted(["Line one to say here", "Line two to say here"])
        s, _, results, events, _ = make(llm, searcher)

        async def go():
            a = await s.handle(utt())
            return await s.search(a.n, "acme")

        assert asyncio.run(go()) is None
        assert len(llm.requests) == 1 and len(results) == 1
        assert events[-1][1]["state"] == "failed" and events[-1][1]["error"] == reason


def test_a_search_overtaken_by_retry_adds_nothing_to_the_new_answer():
    llm = Scripted(["Line one to say here", "Line two to say here"],
                   ["Retried line one here", "Retried line two here"], ["Should never be shown"])
    s, timeline, _, events, _ = make(llm, FakeSearch(delay=0.05))

    async def go():
        a = await s.handle(utt())
        pending = asyncio.ensure_future(s.search(a.n, "acme"))
        await asyncio.sleep(0)
        await s.retry(a.n)
        return await pending

    assert asyncio.run(go()) is None
    assert len(llm.requests) == 2 and ("line", "Should never be shown") not in timeline
    assert s.shown_lines[1] == ["Retried line one here", "Retried line two here"]
    assert "found" not in [info["state"] for _, info in events]
    assert 1 not in s.searched, "the retried answer may be searched"


def test_results_are_cut_short_and_a_sales_call_keeps_its_own_product_facts():
    long_page = SearchResult(title="Rival pricing", url="https://rival.com/pricing", content=" ".join(["word"] * 200))
    sales = search_request("Is Rival cheaper?", ["We start at 19 dollars a user"], [long_page], mode="sales")
    interview = search_request("What do you know about Rival?", ["They sell to shops"], [long_page])
    assert "1. Rival pricing (rival.com)\n" + " ".join(["word"] * SEARCH_RESULT_WORDS) + " ..." in sales
    assert "only from the product information" in sales and "product information" not in interview


def tavily_answering(handler):
    return TavilySearch("tvly-test-key", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def search_with(t, query):
    async def go():
        try:
            return await t.search(query)
        finally:
            await t.close()

    return asyncio.run(go())


def test_tavily_is_asked_as_its_docs_say_and_its_results_are_read():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["Authorization"], body=json.loads(request.content))
        return httpx.Response(200, json={"query": "acme latest results", "response_time": 0.8, "results": [
            {"title": "Acme results", "url": "https://www.acme.com/q3", "content": " Revenue grew\n12 percent. ",
             "score": 0.9},
            {"title": "Nothing in it", "url": "https://blank.example", "content": ""}]})

    reply = search_with(tavily_answering(handler), "  acme   latest results ")
    assert seen["url"] == tavily.ENDPOINT and seen["auth"] == "Bearer tvly-test-key"
    assert seen["body"] == {"query": "acme latest results", "search_depth": "fast", "max_results": 5,
                            "topic": "general", "include_answer": False}
    assert reply.error is None
    assert [(r.title, r.site, r.content) for r in reply.results] == [("Acme results", "acme.com", "Revenue grew 12 percent.")]


@pytest.mark.parametrize("status, expected", [(401, "refused the key"), (429, "too many searches"),
                                              (432, "used its plan's searches"), (500, "Tavily answered 500")])
def test_tavily_errors_say_what_happened_and_never_carry_the_key(status, expected):
    reply = search_with(tavily_answering(
        lambda request: httpx.Response(status, json={"detail": {"error": "Invalid API key: tvly-test-key"}})), "acme")
    assert expected in reply.error and "tvly-test-key" not in reply.error and not reply.results


def test_tavily_out_of_reach_or_slow_is_a_reply_not_an_exception():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    assert search_with(tavily_answering(slow), "acme").error == "the search took too long"
    assert search_with(tavily_answering(down), "acme").error == "could not reach Tavily (ConnectError)"
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        TavilySearch.from_env()


def test_a_call_gets_search_only_when_it_is_turned_on_and_has_a_key(monkeypatch):
    events = []
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    off = Engine(EngineConfig(suggest=True, vendor="gemini"), events.append)._build_suggester(None)
    assert off.searcher is None and not off.builder.web_search
    no_key = Engine(EngineConfig(suggest=True, vendor="gemini", web_search=True), events.append)._build_suggester(None)
    assert no_key.searcher is None and not no_key.builder.web_search
    assert any("web search is off for this call" in e.get("text", "") for e in events if e["type"] == "log")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key")
    on = Engine(EngineConfig(suggest=True, vendor="gemini", web_search=True), events.append)._build_suggester(None)
    assert isinstance(on.searcher, TavilySearch) and on.builder.web_search
    fake = Engine(EngineConfig(suggest=True, fake_llm=True, web_search=True), events.append)._build_suggester(None)
    assert isinstance(fake.searcher, FakeSearch)


def test_search_is_a_setting_off_by_default_and_its_key_is_kept_like_the_others():
    assert "web_search" in user_settings.EDITABLE and user_settings.load().web_search is False
    assert "TAVILY_API_KEY" in user_settings.key_names()
    assert user_settings.load().to_json()["keys"]["TAVILY_API_KEY"] is False
    assert config_from(EngineConfig(), {}).web_search is False
    s = user_settings.load()
    s.web_search = True
    user_settings.save(s)
    assert config_from(EngineConfig(), {}).web_search is True


def test_the_search_command_reaches_the_running_call():
    class Call:
        def __init__(self):
            self.asked = []

        def search(self, n):
            self.asked.append(n)

    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(json.loads(message))

    ws, call = Socket(), Call()

    async def go():
        server = SessionServer(lambda chosen, publish: call)
        await server.handle_command(ws, json.dumps({"cmd": "search", "n": 3}))
        server.engine = call
        await server.handle_command(ws, json.dumps({"cmd": "search", "n": 3}))
        await server.handle_command(ws, json.dumps({"cmd": "search"}))

    asyncio.run(go())
    assert call.asked == [3]
    assert [m["text"] for m in ws.sent] == ["no session to search for", "search needs a question number"]


def test_the_call_summary_keeps_each_search_with_its_question():
    r = CallRecord(clock=lambda: 100.0)
    r.observe({"type": "status", "state": "listening"})
    r.observe({"type": "question", "n": 1, "text": "What do you know about Acme?"})
    r.observe({"type": "search", "n": 1, "state": "searching", "query": ""})
    r.observe({"type": "search", "n": 1, "state": "found", "query": "acme results",
               "sources": [{"site": "acme.com", "title": "Acme results", "url": "https://acme.com/q3"}]})
    r.observe({"type": "question", "n": 2, "text": "Why us?"})
    r.observe({"type": "search", "n": 2, "state": "failed", "query": "", "error": "this call's 10 web searches are used"})
    s = r.summary()
    assert s["searches"] == 1, "a search refused before it was sent is not counted"
    assert s["questions"][0]["search"] == {"query": "acme results", "error": None, "sites": ["acme.com"]}
    assert s["questions"][1]["search"]["error"] == "this call's 10 web searches are used"
