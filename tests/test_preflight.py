"""Checks before a call (2026-10-16): a model the provider lists but refuses must stop the call before
it starts, not on the first question. No network: the OpenAI client is replaced."""

import asyncio

import pytest

from fawa2ly import preflight
from fawa2ly import settings as s
from fawa2ly.llm import openai_compat


class FakeCompletions:
    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if kw["model"] in self.fail_for:
            raise RuntimeError("Error code: 404 - This model models/x is no longer available to new users")
        return object()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg")
    preflight._verified.clear()
    comp = FakeCompletions(fail_for={"gone-model"})

    class Client:
        chat = type("C", (), {"completions": comp})()

        async def close(self):
            pass

    real = openai_compat.OpenAICompatibleLLM.__init__

    def init(self, *a, **kw):
        kw["client"] = Client()
        real(self, *a, **kw)
    monkeypatch.setattr(openai_compat.OpenAICompatibleLLM, "__init__", init)

    async def deepgram_ok():
        return None
    monkeypatch.setattr(preflight, "check_deepgram", deepgram_ok)
    return comp


def test_a_model_that_is_gone_stops_the_call_with_words_a_user_can_act_on(fake):
    problems = asyncio.run(preflight.preflight_call({"vendor": "groq", "model": "gone-model"}))
    assert len(problems) == 1 and "gone-model" in problems[0] and "not available" in problems[0]


def test_a_working_model_passes_and_is_not_asked_again_for_a_while(fake):
    assert asyncio.run(preflight.preflight_call({"vendor": "groq", "model": "good"})) == []
    assert asyncio.run(preflight.preflight_call({"vendor": "groq", "model": "good"})) == []
    assert len(fake.calls) == 1
    assert fake.calls[0]["max_tokens"] == preflight.PROBE_TOKENS


def test_no_model_check_when_suggestions_are_off(fake):
    assert asyncio.run(preflight.preflight_call({"vendor": "groq", "model": "gone-model", "suggest": False})) == []


REAL_CHECK_DEEPGRAM = preflight.check_deepgram     # before the fixture replaces it


def test_a_missing_deepgram_key_is_reported_and_whisper_needs_none(fake, monkeypatch):
    monkeypatch.delenv("DEEPGRAM_API_KEY")
    problem = asyncio.run(REAL_CHECK_DEEPGRAM())     # no key: answered without a request
    assert problem and "Deepgram" in problem
    s_ = s.load()
    s_.stt_vendor = "whisper"
    s.save(s_)
    assert asyncio.run(preflight.preflight_call({"vendor": "groq", "model": "good"})) == []


def test_probing_many_models_reports_each_one(fake):
    out = asyncio.run(preflight.probe_models("groq", ["a", "gone-model", "b"]))
    assert [(r["model"], r["ok"]) for r in out] == [("a", True), ("gone-model", False), ("b", True)]


@pytest.mark.parametrize("raw,word", [("Error code: 401 invalid api key", "key"),
                                      ("Error code: 429 RESOURCE_EXHAUSTED", "rate limiting"),
                                      ("APIConnectionError: Connection error.", "did not answer")])
def test_errors_are_explained(raw, word):
    assert word in preflight.explain(raw)


def test_the_server_does_not_start_a_call_that_failed_its_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from fawa2ly.server import SessionServer
    built = []

    async def failing(chosen):
        return ["Groq gone-model: this model is not available on your account; pick another one"]
    srv = SessionServer(lambda c, cb: built.append(c), preflight=failing)
    with pytest.raises(RuntimeError, match="not started"):
        asyncio.run(srv.start_session({}))
    assert built == [] and srv.session is None


def test_a_fake_model_or_a_recording_starts_without_the_key_checks(monkeypatch):
    """Agent 2 R1: `--replay ... --fake-llm` (README's UI loop) was refused by the preflight for want
    of a Gemini and a Deepgram key, which that run never uses."""
    from fawa2ly import server as server_mod

    made = []

    class Spy:
        def __init__(self, *a, **kw):
            made.append(kw)

        async def run(self):
            return None

    monkeypatch.setattr(server_mod, "SessionServer", Spy)
    assert server_mod.main(["--replay", "demo.raw.jsonl", "--suggest", "--fake-llm", "--port", "0"]) == 0
    assert server_mod.main(["--idle", "--fake-llm", "--port", "0"]) == 0
    assert server_mod.main(["--idle", "--port", "0"]) == 0
    assert [kw["preflight"] is None for kw in made] == [True, True, False]
