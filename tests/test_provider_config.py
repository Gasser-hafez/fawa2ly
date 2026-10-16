"""The API keys page (2026-10-16): every provider configurable, local servers found by a scan, and
OpenAI-compatible endpoints the user adds. Nothing here reaches a network."""

import asyncio

import pytest

from fawa2ly import server
from fawa2ly import settings as s
from fawa2ly.llm.openai_compat import OpenAICompatibleLLM, all_vendors, custom_key_env


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    for v in all_vendors().values():
        if v.get("key_env"):
            monkeypatch.delenv(v["key_env"], raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)


def offered():
    return {p["vendor"] for p in server.available_providers()}


def test_only_ollama_of_the_local_servers_is_offered_until_one_is_turned_on():
    assert "ollama" in offered() and "lmstudio" not in offered()
    s.save_provider("lmstudio", {"enabled": True, "model": "qwen3-8b"})
    assert "lmstudio" in offered()


def test_a_cloud_provider_needs_a_key_and_a_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert "openai" not in offered()               # no default model on purpose
    s.save_provider("openai", {"model": "some-model"})
    assert "openai" in offered()
    s.save_provider("openai", {"enabled": False})
    assert "openai" not in offered()


def test_changes_reach_the_client_and_openai_gets_its_own_parameters(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    s.save_provider("openai", {"model": "m1", "base_url": "https://proxy.example/v1/"})
    llm = OpenAICompatibleLLM.from_env("openai", client=object())
    assert (llm.base_url, llm.model) == ("https://proxy.example/v1", "m1")
    assert llm.token_param == "max_completion_tokens" and llm.temperature is None
    s.save_provider("groq", {"temperature": "0.7"})
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert OpenAICompatibleLLM.from_env("groq", client=object()).temperature == 0.7
    s.save_provider("groq", {"temperature": ""})   # empty: back to the app's default
    assert OpenAICompatibleLLM.from_env("groq", client=object()).temperature == 0.3


@pytest.mark.parametrize("values", [{"base_url": "ftp://x"}, {"base_url": "not a url"},
                                    {"temperature": "3"}, {"temperature": "hot"}, {"model": "a\nb"}])
def test_bad_values_are_refused_and_nothing_is_saved(values):
    with pytest.raises(ValueError):
        s.save_provider("groq", values)
    assert s.load().providers == {}


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError):
        s.save_provider("nope", {"model": "x"})


def test_a_custom_endpoint_is_added_used_and_removed_with_its_key():
    pid = s.add_custom_provider("My Gateway", "http://10.0.0.5:4000/v1", "llama-70b")
    assert pid == "custom-my-gateway"
    assert pid in offered()                          # its key is optional
    s.save_keys({custom_key_env(pid): "secret"})     # the key name is one the app accepts now
    llm = OpenAICompatibleLLM.from_env(pid, client=object())
    assert (llm.base_url, llm.model) == ("http://10.0.0.5:4000/v1", "llama-70b")
    assert s.add_custom_provider("My Gateway", "http://h/v1") == "custom-my-gateway-2"
    s.remove_custom_provider(pid)
    assert pid not in all_vendors() and custom_key_env(pid) not in s.key_names()
    with pytest.raises(ValueError):
        s.remove_custom_provider("openai")           # the built-in ones stay


def test_the_catalog_says_whether_a_key_is_set_and_never_the_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-very-secret")
    cat = {p["id"]: p for p in s.provider_catalog()}
    assert cat["groq"]["key_set"] and cat["groq"]["ready"] and cat["groq"]["kind"] == "cloud"
    assert cat["ollama"]["kind"] == "local" and cat["ollama"]["key_optional"]
    assert "gsk-very-secret" not in repr(cat)


def test_a_scan_turns_on_the_local_servers_that_answer_and_picks_a_model(monkeypatch):
    async def fake_check(vendor, timeout=8.0):
        if vendor == "lmstudio":
            return {"vendor": vendor, "ok": True, "ms": 3, "models": ["qwen3-8b", "phi-4"]}
        return {"vendor": vendor, "ok": False, "error": "APIConnectionError"}
    monkeypatch.setattr(server, "check_provider", fake_check)

    class WS:
        sent = []

        async def send(self, text):
            self.sent.append(text)

    srv = server.SessionServer.__new__(server.SessionServer)
    srv.publish = lambda event: None
    srv._publish_settings = lambda: None
    asyncio.run(srv.handle_scan_local(WS()))
    v = all_vendors()["lmstudio"]
    assert v["enabled"] and v["model"] == "qwen3-8b"
    assert not all_vendors()["jan"]["enabled"]
