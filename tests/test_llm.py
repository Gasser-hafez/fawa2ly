import asyncio
from types import SimpleNamespace

import pytest

from fawa2ly.llm import FakeLLM, OpenAICompatibleLLM, collect, parse_bullets
from fawa2ly.llm.base import SuggestionRequest
from fawa2ly.llm.prompts import SYSTEM_V1, user_content

REQ = SuggestionRequest(question="Why did you leave your last job?", system=SYSTEM_V1,
                        history=[("other", "Tell me about yourself."), ("user", "I am a teacher.")])


def test_parse_bullets_accepts_common_markers_and_plain_lines():
    assert parse_bullets("- a\n* b\n• c\n1. d\n2) e\n\nplain\n") == ["a", "b", "c", "d", "e", "plain"]
    assert parse_bullets("") == []


def test_user_content_includes_history_then_question():
    text = user_content(REQ.question, REQ.history)
    assert text.startswith("Recent conversation:\nother: Tell me about yourself.\nuser: I am a teacher.")
    assert text.endswith("They were just asked:\nWhy did you leave your last job?")
    assert user_content("Q?", []) == "They were just asked:\nQ?"


def test_fake_llm_streams_first_token_then_tokens_then_done():
    fake = FakeLLM(bullets=["night shift", "want day work"])
    events = asyncio.run(_collect_events(fake))
    kinds = [e.kind for e in events]
    assert kinds[0] == "first_token" and kinds[-1] == "done" and set(kinds[1:-1]) == {"token"}
    assert "".join(e.text for e in events[:-1]) == "- night shift\n- want day work"
    assert events[-1].usage["output_tokens"] == len(events) - 1
    assert fake.requests == [REQ]


async def _collect_events(provider):
    return [e async for e in provider.suggest(REQ)]


def test_collect_builds_suggestion_with_timings_and_bullets():
    fake = FakeLLM(bullets=["a b", "c"], first_token_delay=0.02)
    s = asyncio.run(collect(fake, REQ))
    assert s.bullets == ["a b", "c"] and s.text == "- a b\n- c"
    assert s.error is None and s.provider == "fake" and s.model == "fake-1"
    assert s.ttft_ms is not None and s.ttft_ms >= 15
    assert s.total_ms >= s.ttft_ms
    assert s.to_json()["ttft_ms"] == s.ttft_ms


def test_collect_reports_error_without_raising():
    s = asyncio.run(collect(FakeLLM(error="RateLimitError: slow down"), REQ))
    assert s.error == "RateLimitError: slow down" and s.bullets == [] and s.ttft_ms is None


class FakeStream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


def chunk(content, model="llama-x", usage=None, empty=False, finish_reason=None):
    choices = [] if empty else [SimpleNamespace(delta=SimpleNamespace(content=content),
                                                finish_reason=finish_reason)]
    return SimpleNamespace(choices=choices, model=model, usage=usage)


class FakeClient:
    def __init__(self, chunks=None, raise_exc=None):
        self.calls = []
        outer = self

        class Completions:
            async def create(self, **kw):
                outer.calls.append(kw)
                if raise_exc:
                    raise raise_exc
                return FakeStream(chunks)

        self.chat = SimpleNamespace(completions=Completions())
        self.models = SimpleNamespace(list=self._list)
        self.closed = False

    async def _list(self):
        return SimpleNamespace(data=[SimpleNamespace(id="llama-b"), SimpleNamespace(id="llama-a")])

    async def close(self):
        self.closed = True


def test_openai_compatible_maps_chunks_to_events_and_usage():
    client = FakeClient(chunks=[
        chunk(None),                                   # role-only first chunk, no content
        chunk("- night "), chunk("shift\n"), chunk("- wants days"),
        chunk(None, usage=SimpleNamespace(prompt_tokens=80, completion_tokens=9), empty=True),
    ])
    prov = OpenAICompatibleLLM("https://x/v1", "k", "llama-x", name="testvendor", client=client)
    s = asyncio.run(collect(prov, REQ))
    assert s.bullets == ["night shift", "wants days"]
    assert s.usage == {"input_tokens": 80, "output_tokens": 9}
    assert s.model == "llama-x" and s.provider == "testvendor" and s.error is None
    call = client.calls[0]
    from fawa2ly.llm.base import SuggestionRequest as _R
    assert call["model"] == "llama-x" and call["stream"] is True
    assert call["max_tokens"] == _R("q", "s").max_tokens      # the default, wherever it is set
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_V1}
    assert call["messages"][1]["role"] == "user" and REQ.question in call["messages"][1]["content"]


def test_openai_compatible_turns_exceptions_into_error_events_and_lists_models():
    prov = OpenAICompatibleLLM("https://x/v1", "k", "m", client=FakeClient(raise_exc=ConnectionError("down")))
    s = asyncio.run(collect(prov, REQ))
    assert s.error == "ConnectionError: down" and s.text == ""
    prov2 = OpenAICompatibleLLM("https://x/v1", "k", "m", client=FakeClient())
    assert asyncio.run(prov2.list_models()) == ["llama-a", "llama-b"]
    asyncio.run(prov2.close())
    assert prov2._client.closed is True


def test_from_env_requires_key_and_reads_model_override(monkeypatch):
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        OpenAICompatibleLLM.from_env("cerebras")
    with pytest.raises(ValueError):
        OpenAICompatibleLLM.from_env("nope")
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    monkeypatch.setenv("CEREBRAS_MODEL", "llama-custom")
    prov = OpenAICompatibleLLM.from_env("cerebras")
    assert (prov.name, prov.model, prov.base_url) == ("cerebras", "llama-custom", "https://api.cerebras.ai/v1")
    assert OpenAICompatibleLLM.from_env("cerebras", model="explicit").model == "explicit"
    assert OpenAICompatibleLLM.from_env("ollama").model == "llama3.2"


def test_vendor_extra_fields_go_through_extra_body(monkeypatch):
    client = FakeClient(chunks=[chunk("- a")])
    prov = OpenAICompatibleLLM("https://x/v1", "k", "m", client=client, extra={"reasoning_effort": "minimal"})
    asyncio.run(collect(prov, REQ))
    assert client.calls[0]["extra_body"] == {"reasoning_effort": "minimal"}
    prov2 = OpenAICompatibleLLM("https://x/v1", "k", "m", client=FakeClient(chunks=[chunk("- a")]))
    asyncio.run(collect(prov2, REQ))
    assert "extra_body" not in prov2._client.calls[0]
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert OpenAICompatibleLLM.from_env("gemini").extra["reasoning_effort"] == "low"
    assert OpenAICompatibleLLM.from_env("gemini", extra={}).extra == {}


class FakeClientWithNonStream(FakeClient):
    """Streams nothing, answers non-streamed (some gateways do)."""

    def __init__(self, text="- a\n- b", usage=None):
        super().__init__(chunks=[])
        outer = self

        class Completions:
            async def create(self, **kw):
                outer.calls.append(kw)
                if kw.get("stream"):
                    return FakeStream([])
                return SimpleNamespace(model="routed-model", usage=usage,
                                       choices=[SimpleNamespace(message=SimpleNamespace(content=text),
                                                                finish_reason="stop")])

        self.chat = SimpleNamespace(completions=Completions())


def test_empty_stream_falls_back_to_non_streaming_once():
    client = FakeClientWithNonStream(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7))
    prov = OpenAICompatibleLLM("http://x/v1", "k", "auto/fast", client=client,
                               extra={"reasoning_effort": "low", "stream_options": {"include_usage": True}})
    s = asyncio.run(collect(prov, REQ))
    assert s.bullets == ["a", "b"] and s.error is None
    assert s.model == "routed-model" and s.usage == {"input_tokens": 50, "output_tokens": 7}
    assert s.ttft_ms is not None and s.ttft_ms <= s.total_ms
    assert [c["stream"] for c in client.calls] == [True, False]
    assert client.calls[1]["extra_body"] == {"reasoning_effort": "low"}   # stream_options dropped


def test_empty_stream_and_empty_non_stream_gives_empty_suggestion():
    prov = OpenAICompatibleLLM("http://x/v1", "k", "m", client=FakeClientWithNonStream(text=""))
    s = asyncio.run(collect(prov, REQ))
    assert s.bullets == [] and s.error is None and s.ttft_ms is None


class ClosableStream(FakeStream):
    def __init__(self, chunks, raise_after=None):
        super().__init__(chunks)
        self.closed = False
        self.raise_after = raise_after

    async def _gen(self):
        for i, c in enumerate(self._chunks):
            if self.raise_after is not None and i >= self.raise_after:
                raise ConnectionError("dropped")
            yield c
        if self.raise_after is not None:
            raise ConnectionError("dropped")

    async def close(self):
        self.closed = True


def test_stream_closed_only_when_not_drained():
    """Closing an exhausted stream makes httpcore2 print "generator didn't stop after athrow()"."""
    made = []

    class Client(FakeClient):
        def __init__(self, chunks, raise_after=None):
            super().__init__(chunks=chunks)
            outer = self

            class Completions:
                async def create(self, **kw):
                    outer.calls.append(kw)
                    st = ClosableStream(chunks, raise_after=raise_after)
                    made.append(st)
                    return st

            self.chat = SimpleNamespace(completions=Completions())

    # drained normally -> never closed
    prov = OpenAICompatibleLLM("http://x/v1", "k", "m", client=Client([chunk("- a")]))
    assert asyncio.run(collect(prov, REQ)).bullets == ["a"]
    assert made and not any(st.closed for st in made)

    # broken mid-stream -> closed, and the error is reported not raised
    made.clear()
    prov = OpenAICompatibleLLM("http://x/v1", "k", "m", client=Client([chunk("- a")], raise_after=1))
    s = asyncio.run(collect(prov, REQ))
    assert s.error.startswith("ConnectionError")
    assert made and all(st.closed for st in made)


def test_mute_asyncgen_close_noise_filters_only_that_message():
    from fawa2ly.llm import mute_asyncgen_close_noise
    seen = []

    async def main():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(lambda lp, ctx: seen.append(ctx["message"]))
        mute_asyncgen_close_noise()
        loop.call_exception_handler({"message": "an error occurred during closing of asynchronous generator"})
        loop.call_exception_handler({"message": "Task exception was never retrieved"})

    asyncio.run(main())
    assert seen == ["Task exception was never retrieved"]


def test_finish_reason_is_reported_and_length_means_truncated():
    # the finish_reason arrives on a final chunk that carries no new text
    client = FakeClient(chunks=[chunk("- a b"), chunk("", finish_reason="length")])
    prov = OpenAICompatibleLLM("http://x/v1", "k", "m", client=client)
    s = asyncio.run(collect(prov, REQ))
    assert s.finish_reason == "length" and s.truncated is True
    assert s.to_json()["truncated"] is True

    s2 = asyncio.run(collect(FakeLLM(bullets=["a"]), REQ))
    assert s2.finish_reason == "stop" and s2.truncated is False

    s3 = asyncio.run(collect(FakeLLM(bullets=["a"], finish_reason="length"), REQ))
    assert s3.truncated is True


def test_missing_finish_reason_field_does_not_break_the_answer():
    """A gateway that omits finish_reason must still yield bullets."""
    class Bare(FakeClient):
        def __init__(self):
            super().__init__(chunks=[])
            outer = self

            class Completions:
                async def create(self, **kw):
                    outer.calls.append(kw)
                    return FakeStream([SimpleNamespace(
                        choices=[SimpleNamespace(delta=SimpleNamespace(content="- a b"))],
                        model="m", usage=None)])

            self.chat = SimpleNamespace(completions=Completions())

    s = asyncio.run(collect(OpenAICompatibleLLM("http://x/v1", "k", "m", client=Bare()), REQ))
    assert s.bullets == ["a b"] and s.finish_reason is None and s.truncated is False


def test_prompt_forbids_asking_the_user_anything_back():
    """Mid-call nobody can answer the model, so a clarifying question is a dead overlay."""
    from fawa2ly.limits import MAX_BULLET_WORDS
    assert "Never ask a question back" in SYSTEM_V1
    assert "ignore the background silently" in SYSTEM_V1
    assert f"At most {MAX_BULLET_WORDS} words per line" in SYSTEM_V1


def test_prompt_forbids_a_menu_and_forbids_inventing_a_background():
    """The two defects the first rated relevance run found (prompt v1.4).

    A bullet that lists alternatives makes the speaker read and choose mid-sentence, which is
    exactly what the overlay exists to remove; and a question the CV cannot support was answered
    with an invented workplace conflict.
    """
    assert "One point per line" in SYSTEM_V1
    assert "Never offer alternatives to choose between" in SYSTEM_V1
    assert "never invent an employer, a job, a school, a date" in SYSTEM_V1
    assert "fill in the detail it leaves out" in SYSTEM_V1


def test_prompt_asks_for_an_answer_to_the_question_not_a_summary_of_the_cv():
    """The first live rehearsal (prompt v2.1). Asked about Flutter experience, v2.0 opened with
    where the speaker studies: every claim true, none of it an answer. Grounding was one-sided,
    saying where facts may come from and never that the choosing and framing are the model's own
    work, so quoting the CV was the safe move."""
    assert "The first line answers it head on" in SYSTEM_V1
    assert "not a summary of the background" in SYSTEM_V1
    assert "Inside something the background does name" in SYSTEM_V1


def test_prompt_asks_for_spoken_sentences_not_keywords():
    """Prompt v2.0: what is on screen has to be sayable, or the assist is not an assist."""
    assert "complete, natural spoken sentence they could say word for word" in SYSTEM_V1
    assert "Never a label followed by a list" in SYSTEM_V1
    assert "The rest run on from it" in SYSTEM_V1


def test_parse_bullets_drops_a_dangling_marker_from_a_truncated_reply():
    """A reply cut off by max_tokens ends in a bare '-', which reached the overlay as a bullet."""
    assert parse_bullets("- real bullet\n-") == ["real bullet"]
    assert parse_bullets("- real bullet\n*\n...\n   \n") == ["real bullet"]
    assert parse_bullets("- 1.4s to 180ms") == ["1.4s to 180ms"]      # digits are content


def test_the_working_default_vendor_is_the_one_that_actually_answers():
    """Every command-line tool defaults to the same vendor, so one setting changes them together."""
    import fawa2ly.bench, fawa2ly.relevance, fawa2ly.replay, fawa2ly.suggest
    for module in (fawa2ly.suggest, fawa2ly.replay, fawa2ly.bench, fawa2ly.relevance):
        src = open(module.__file__, encoding="utf-8").read()
        assert 'default="gemini"' in src, module.__name__


def test_prompt_keeps_the_cv_out_of_general_questions_and_does_not_repeat_stories():
    """rehearsal4 (prompt v2.3): the same chat app answered "how do you keep your skills sharp",
    "how do you learn from bugs" and "why Flutter", and its offline queue came up in 5 of 11
    answers. A general question gets a general answer, and a story is told once."""
    assert "Match the kind of question" in SYSTEM_V1
    assert "Most general questions need none" in SYSTEM_V1
    assert "Do not retell a story" in SYSTEM_V1

