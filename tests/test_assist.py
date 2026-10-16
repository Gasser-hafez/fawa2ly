import asyncio
import io
import json

from fawa2ly.assist import Assist, Suggester, format_assist, is_question, speaker_label
from fawa2ly.context import ContextBuilder
from fawa2ly.limits import MAX_BULLET_WORDS
from fawa2ly.llm import FakeLLM
from fawa2ly.pipeline import Utterance


def utt(n=1, text="What hours can you work?", trigger="question_mark", stream="other",
        q_end_to_fire=300, speaker=0):
    return Utterance(n=n, speaker=speaker, trigger=trigger, text=text, segments=1,
                     t_first_final=1.0, t_last_final=1.0, t_flush=2.0, gap_ms=0,
                     speech_to_text_ms=400, source="interim", stream=stream,
                     question_end_to_fire_ms=q_end_to_fire)


def test_is_question_covers_detector_triggers_and_ignores_your_own_mic():
    assert is_question(utt(trigger="question_mark"))
    assert is_question(utt(trigger="yes_no"))
    assert is_question(utt(trigger="request_opener"))
    assert not is_question(utt(trigger="silence"))
    assert not is_question(utt(trigger="speaker_change"))
    assert not is_question(utt(trigger="question_mark", stream="user"))   # you asked it, not them
    assert speaker_label(utt(stream="user")) == "you"
    assert speaker_label(utt(stream="other")) == "them"


def test_handle_answers_a_question_and_computes_the_product_metric():
    log = io.StringIO()
    seen = []
    s = Suggester(FakeLLM(bullets=["night shift", "wanted day work"], first_token_delay=0.03),
                  log=log, on_result=seen.append)
    a = asyncio.run(s.handle(utt(q_end_to_fire=300)))
    assert a is not None and a.bullets == ["night shift", "wanted day work"]
    assert a.question == "What hours can you work?" and a.n == 1
    assert a.ttft_ms is not None and a.ttft_ms >= 25
    assert a.question_end_to_first_token_ms == 300 + a.ttft_ms      # fire + model, two clocks
    assert a.trigger == "question_mark" and a.stream == "other" and a.t_fire == 2.0
    assert seen == [a]
    row = json.loads(log.getvalue())
    assert row["question_end_to_first_token_ms"] == a.question_end_to_first_token_ms
    assert row["bullets"] == a.bullets and row["error"] is None


def test_non_questions_are_history_only():
    fake = FakeLLM()
    s = Suggester(fake)
    assert asyncio.run(s.handle(utt(text="I worked there for two years.", trigger="silence"))) is None
    assert fake.requests == [] and s.n == 0
    assert s.history == [("them", "I worked there for two years.")]


def test_history_reaches_the_next_question_but_not_the_question_itself():
    fake = FakeLLM()
    s = Suggester(fake, ContextBuilder(history_turns=6))
    asyncio.run(s.handle(utt(text="Tell me about yourself.", trigger="silence")))
    asyncio.run(s.handle(utt(text="I am a teacher.", trigger="silence", stream="user")))
    asyncio.run(s.handle(utt(text="Why did you leave?")))
    req = fake.requests[0]
    assert req.history == [("them", "Tell me about yourself."), ("you", "I am a teacher.")]
    assert req.question == "Why did you leave?"
    assert s.history[-1] == ("them", "Why did you leave?")          # asked question joins history after


def test_history_is_trimmed_to_the_builder_window():
    fake = FakeLLM()
    s = Suggester(fake, ContextBuilder(history_turns=2))
    for i in range(5):
        asyncio.run(s.handle(utt(text=f"line {i}", trigger="silence")))
    asyncio.run(s.handle(utt(text="Why?")))
    assert fake.requests[0].history == [("them", "line 3"), ("them", "line 4")]


def test_guardrail_is_applied_and_recorded():
    long_bullet = " ".join(["word"] * (MAX_BULLET_WORDS + 1))
    s = Suggester(FakeLLM(bullets=["short one", long_bullet]))
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == ["short one"]
    assert a.dropped == [{"text": long_bullet, "reason": "too_long"}]


def test_provider_errors_are_recorded_not_raised():
    log = io.StringIO()
    s = Suggester(FakeLLM(error="RateLimitError: slow down"), log=log)
    a = asyncio.run(s.handle(utt()))
    assert a.error == "RateLimitError: slow down" and a.bullets == []
    assert a.question_end_to_first_token_ms is None
    assert json.loads(log.getvalue())["error"] == "RateLimitError: slow down"


def test_metric_is_none_when_the_fire_time_is_unknown():
    s = Suggester(FakeLLM(bullets=["a b"]))
    a = asyncio.run(s.handle(utt(q_end_to_fire=None)))
    assert a.ttft_ms is not None and a.question_end_to_first_token_ms is None


def test_format_assist_reads_as_one_line_plus_bullets():
    s = Suggester(FakeLLM(bullets=["night shift", " ".join(["w"] * (MAX_BULLET_WORDS + 1))]))
    a = asyncio.run(s.handle(utt()))
    text = format_assist(a)
    assert "question end -> first line" in text and "1 too_long" in text
    assert "     * night shift" in text
    err = format_assist(Assist(n=2, question="Q?", bullets=[], error="boom"))
    assert err.startswith("** [2]") and "ERROR boom" in err


def test_one_reply_budget_reaches_the_provider_from_every_path():
    """Three defaults for the same number disagreed, and the eval ran on the smallest of them.

    `SuggestionRequest` said 2000, `Suggester` passed 800 and `ContextBuilder.build` passed 400,
    so a relevance run whose truncations were recorded was made at 400 while the note explaining
    them discussed 800.
    """
    from fawa2ly.limits import DEFAULT_MAX_TOKENS
    from fawa2ly.llm.base import SuggestionRequest
    assert ContextBuilder().build("What is your biggest weakness?").max_tokens == DEFAULT_MAX_TOKENS
    assert SuggestionRequest("q", "sys").max_tokens == DEFAULT_MAX_TOKENS
    assert Suggester(FakeLLM()).max_tokens == DEFAULT_MAX_TOKENS


def test_lines_reach_the_overlay_as_they_arrive_and_never_ahead_of_the_guardrail():
    """M3 step 2: the overlay cannot wait for the whole reply, and must not show a line the log
    then drops. Both come from one place, so what was shown is exactly what was recorded."""
    shown = []
    long_line = " ".join(["word"] * (MAX_BULLET_WORDS + 1))
    provider = FakeLLM(bullets=["I worked nights for a year and it stopped suiting me",
                                long_line,                                     # too_long
                                "I worked Nights for a year and it stopped suiting me!",  # duplicate
                                "Personal action: clarified ownership, isolated changes"])  # label_prefix
    s = Suggester(provider, ContextBuilder(), on_line=lambda n, text: shown.append((n, text)))
    a = asyncio.run(s.handle(utt()))
    assert [text for _, text in shown] == a.bullets
    assert [n for n, _ in shown] == [1]


def test_a_question_is_announced_before_the_model_is_asked():
    """A UI must be able to show the question with the wait in front of it, and the number it is
    given is the one the lines and the record use. Utterance.n counts every utterance instead."""
    seen = []
    s = Suggester(FakeLLM(bullets=["I worked nights"]), ContextBuilder(),
                  on_question=lambda n, text: seen.append(("question", n, text)),
                  on_line=lambda n, text: seen.append(("line", n, text)))
    a = asyncio.run(s.handle(utt(n=7)))
    assert seen[0] == ("question", 1, "What hours can you work?")
    assert [kind for kind, _, _ in seen] == ["question", "line"]
    assert a.n == 1 and all(n == a.n for _, n, _ in seen)


def test_retry_asks_the_same_question_again_under_the_same_number():
    """A failed answer is exactly when someone wants another try, and the failures are not
    deterministic. The number has to stay the same or the overlay opens a second card for one
    question."""
    seen = []
    provider = FakeLLM(bullets=["I worked nights for a year and it stopped suiting me"])
    s = Suggester(provider, ContextBuilder(),
                  on_question=lambda n, text: seen.append(("question", n, text)))
    first = asyncio.run(s.handle(utt()))
    again = asyncio.run(s.retry(first.n))
    assert again is not None
    assert again.n == first.n and again.question == first.question
    assert again.bullets == first.bullets
    assert [kind for kind, _, _ in seen] == ["question", "question"]
    assert s.n == 1, "a retry is not a new question"
    assert asyncio.run(s.retry(99)) is None


def test_retry_is_ignored_when_there_is_no_suggester():
    """The overlay can ask for a retry on a session started without --suggest."""
    from fawa2ly.engine import Engine, EngineConfig
    e = Engine(EngineConfig(), on_event=lambda _e: None)
    e.retry(1)          # must not raise


def test_a_revised_question_is_asked_again_under_its_number_with_the_fuller_text():
    """The pipeline fired on "...how you handled state management?" and later heard the rest.
    The revision must reuse the question's number, so the overlay replaces the card instead of
    opening a second one, and the fuller text must become the context for later questions."""
    seen = []
    s = Suggester(FakeLLM(bullets=["I kept the chat screens on Provider backed by Firebase streams"]),
                  ContextBuilder(),
                  on_question=lambda n, text: seen.append(("question", n, text)),
                  on_result=lambda a: seen.append(("assist", a.n, a.question)))
    first = asyncio.run(s.handle(utt(n=6, text="Can you walk me through how you handled state management?")))
    grown = utt(n=9, text="Can you walk me through how you handled state management with provider and why you chose it?")
    grown.revises = 6
    again = asyncio.run(s.handle(grown))
    assert again.n == first.n == 1 and s.n == 1, "a revision is not a new question"
    assert [kind for kind, _, _ in seen] == ["question", "assist", "question", "assist"]
    assert seen[-1][2] == grown.text
    assert s.history[-1][1] == grown.text

