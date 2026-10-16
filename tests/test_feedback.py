"""Live feedback (roadmap item 9, 2026-09-11): a verdict on each of the user's answers.

What matters is when an answer is over and what it costs: the interviewer's "Okay, great." is not
the end of an answer, the interviewer adding to a question before the user speaks does not end it
either, a one-line reply is not judged, a question gets one verdict at most, a call has a limit, and
a feedback request never runs while an answer is being written.
"""

import asyncio
import json

from fawa2ly import settings as user_settings
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.feedback import MIN_ANSWER_WORDS, Coach, parse_feedback
from fawa2ly.llm import FakeLLM
from fawa2ly.server import config_from
from fawa2ly.summary import CallRecord

REPLY = json.dumps({"verdict": "ran_long", "note": "Stop after the example next time."})
ANSWER = " ".join(["word"] * 20)


def question(n, text="Tell me about a project you are proud of"):
    return {"type": "question", "n": n, "text": text}


def said(stream, text, trigger="silence", t=10.0):
    return {"type": "utterance", "utterance": {"stream": stream, "text": text, "trigger": trigger,
                                               "t_first_final": t, "t_last_final": t + 6.0}}


def coach_run(events, provider, busy=lambda: False, **kw):
    out = []

    async def go():
        tasks = []

        def spawn(coro):
            tasks.append(asyncio.create_task(coro))
            return tasks[-1]

        coach = Coach(provider=lambda: provider, spawn=spawn, emit=out.append, busy=busy, poll_s=0.01, **kw)
        for e in events:
            coach.observe(e)
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        return coach

    return out, asyncio.run(go())


def test_an_answer_is_judged_once_the_other_side_speaks_again():
    provider = FakeLLM(bullets=[REPLY])
    out, _ = coach_run([question(1), {"type": "line", "n": 1, "text": "The offline chat queue"},
                        {"type": "assist", "assist": {"n": 1, "bullets": ["The offline chat queue"]}},
                        said("user", ANSWER),
                        said("other", "Thanks, that is really helpful to hear")], provider)
    assert out == [{"type": "feedback", "n": 1, "verdict": "ran_long", "note": "Stop after the example next time.",
                    "words": 20, "seconds": 6}]
    user = provider.requests[0].user
    assert "Tell me about a project" in user and "The offline chat queue" in user and "(20 words, 6 seconds)" in user
    assert "not their words" in provider.requests[0].system


def test_listening_noises_an_added_question_and_short_replies_do_not_end_or_count_as_answers():
    provider = FakeLLM(bullets=[REPLY])
    out, coach = coach_run([
        question(1),
        said("other", "Take your time and include one example"),    # the interviewer adds to it first
        said("user", ANSWER),
        said("other", "Okay, great."),                               # listening, not the end
        said("user", "and it cut lost messages to zero"),
        said("other", "Why Flutter?", trigger="question_mark"),      # a question ends it
        question(2, "Why Flutter?"),
        said("user", "Mostly the hot reload."),                      # too short to judge
        question(3, "What would you do differently?"),
    ], provider)
    assert [x["n"] for x in out] == [1] and len(provider.requests) == 1
    assert "lost messages to zero" in provider.requests[0].user
    assert len("Mostly the hot reload.".split()) < MIN_ANSWER_WORDS


def test_one_verdict_per_question_and_a_limit_per_call():
    provider = FakeLLM(bullets=[REPLY])
    events = []
    for n in (1, 2, 3):
        events += [question(n, f"Question {n} about the work"), said("user", ANSWER),
                   said("other", "Right, and let me ask about something else")]
    events.insert(3, question(1, "Question 1 about the work, asked again"))   # a retry keeps its number
    out, coach = coach_run(events, provider, limit=2)
    assert [x["n"] for x in out] == [1, 2] and coach.requests == 2


def test_a_feedback_request_waits_while_an_answer_is_being_written():
    provider = FakeLLM(bullets=[REPLY])
    checks = {"n": 0}

    def busy():
        checks["n"] += 1
        return checks["n"] <= 3              # an answer is streaming for the first three looks

    out, _ = coach_run([question(1), said("user", ANSWER), said("other", "Great, thank you for that answer")],
                       provider, busy=busy)
    assert checks["n"] == 4 and len(out) == 1


def test_a_reply_that_is_not_one_of_the_four_verdicts_shows_nothing():
    assert parse_feedback('{"verdict": "Drifted", "note": "Stay on the question"}') == \
        {"verdict": "drifted", "note": "Stay on the question"}
    assert parse_feedback('{"verdict": "great"}') is None and parse_feedback("no json here") is None
    out, _ = coach_run([question(1), said("user", ANSWER), said("other", "Right, next thing then please")],
                       FakeLLM(bullets=['{"verdict": "excellent", "note": "wow"}']))
    assert out == []


def test_the_engine_coaches_only_with_feedback_on_and_the_microphone_transcribed(monkeypatch, tmp_path):
    def coach_for(**kw):
        e = Engine(EngineConfig(suggest=True, fake_llm=True, **kw), on_event=lambda _e: None)
        e.suggester = e._build_suggester(None)
        return e, e._coach_if_asked()

    assert coach_for(feedback=True, user_mic=False)[1] is None
    assert coach_for(feedback=False, user_mic=True)[1] is None
    e, coach = coach_for(feedback=True, user_mic=True)
    assert isinstance(coach, Coach)
    e.coach = coach
    e.suggester.provider = FakeLLM(bullets=[REPLY])

    async def go():
        for event in (question(1), said("user", ANSWER), said("other", "That makes sense, thank you for it")):
            e.emit(event)
        await asyncio.wait(e._pending, timeout=5)

    events = []
    e.on_event = events.append
    asyncio.run(go())
    assert [x for x in events if x["type"] == "feedback"][0]["verdict"] == "ran_long"
    assert e.record.summary()["questions"][0]["feedback"]["verdict"] == "ran_long", "kept with the call"

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert "live_feedback" in user_settings.EDITABLE and user_settings.load().live_feedback is False
    assert config_from(EngineConfig(), {}).feedback is False
    s = user_settings.load()
    s.live_feedback = True
    user_settings.save(s)
    assert config_from(EngineConfig(), {}).feedback is True


def test_the_summary_keeps_each_answers_verdict():
    r = CallRecord()
    r.observe({"type": "status", "state": "listening"})
    r.observe(question(1))
    r.observe({"type": "feedback", "n": 1, "verdict": "drifted", "note": "Come back to the question"})
    assert r.summary()["questions"][0]["feedback"] == {"verdict": "drifted", "note": "Come back to the question"}
