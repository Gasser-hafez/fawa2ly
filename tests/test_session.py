"""Regression for the live receiver dropping interim results (2026-09-08)."""

import asyncio
import io
import json

from fawa2ly.pipeline import Pipeline
from fawa2ly.session import consume
from fawa2ly.stt.base import STTEvent, Word


def ev(kind, t_recv, t_rel, spec):
    words = [Word(t, s, e, spk) for t, s, e, spk in spec]
    return STTEvent(kind, t_recv, t_rel, " ".join(w.text for w in words), words)


QUESTION = [("What", 10.0, 10.2, 0), ("hours", 10.2, 10.5, 0), ("can", 10.5, 10.6, 0),
            ("you", 10.6, 10.7, 0), ("work?", 10.7, 11.0, 0)]
ANSWER = [("From", 11.4, 11.6, 0), ("nine.", 11.6, 12.0, 0)]


async def agen(items):
    for it in items:
        yield it


def run_consume(events, tag="other", pipe=None):
    log = io.StringIO()
    shown = []
    pipe = pipe or Pipeline(stream=tag)
    n = asyncio.run(consume(agen(events), pipe, log, tag, show=lambda *a, **k: shown.append(a[0])))
    rows = [json.loads(l) for l in log.getvalue().splitlines()]
    return n, rows, shown


def test_interim_results_reach_the_pipeline_and_fire_early():
    events = [
        ev("interim", 100.0, 11.4, QUESTION),                 # question visible with its "?"
        ev("final", 103.0, 14.0, QUESTION + ANSWER),          # final arrives 2.6 s later
        STTEvent("utterance_end", 104.0, 15.0),
    ]
    n, rows, shown = run_consume(events)
    assert n == 2
    assert (rows[0]["source"], rows[0]["trigger"], rows[0]["text"]) == ("interim", "question_mark", "What hours can you work?")
    assert rows[0]["question_end_to_fire_ms"] == 400       # 11.4 - 11.0, not 14.0 - 11.0
    assert (rows[1]["trigger"], rows[1]["text"]) == ("silence", "From nine.")
    assert shown[0].startswith("\r   What hours")             # interim was also displayed


def test_user_stream_is_logged_but_never_fires_and_prints_no_interims():
    pipe = Pipeline(stream="user", classify=lambda _s: None, interim_detection=False)
    events = [
        ev("interim", 100.0, 11.4, QUESTION),
        ev("final", 103.0, 14.0, QUESTION),
        STTEvent("utterance_end", 104.0, 15.0),
    ]
    n, rows, shown = run_consume(events, tag="user", pipe=pipe)
    assert n == 1
    assert (rows[0]["stream"], rows[0]["trigger"], rows[0]["source"]) == ("user", "silence", "final")
    assert not any(s.startswith("\r   What") for s in shown)


def test_error_events_are_shown_and_skipped():
    n, rows, shown = run_consume([STTEvent("error", 1.0, 1.0, "boom")])
    assert n == 0 and rows == [] and shown == ["[stt error other] boom"]


def test_utterances_are_handed_to_the_suggester_without_blocking_stt():
    """The model call must run as a task; awaiting it inline would stall the transcript."""
    from fawa2ly.assist import Suggester
    from fawa2ly.llm import FakeLLM

    fake = FakeLLM(bullets=["night shift"])
    suggester = Suggester(fake)
    spawned = []

    async def main():
        log = io.StringIO()
        # spawn records the coroutine instead of scheduling it, proving consume() does not await
        def spawn(coro):
            spawned.append(coro)
            return coro
        n = await consume(agen([ev("final", 100.0, 11.4, QUESTION), STTEvent("utterance_end", 101.0, 12.0)]),
                          Pipeline(stream="other"), log, "other", show=lambda *a, **k: None,
                          suggester=suggester, spawn=spawn)
        for coro in spawned:            # run them afterwards so nothing is left un-awaited
            await coro
        return n

    n = asyncio.run(main())
    assert n == 1 and len(spawned) == 1
    assert suggester.n == 1 and fake.requests[0].question == "What hours can you work?"


def test_without_a_suggester_nothing_is_spawned():
    spawned = []
    log = io.StringIO()
    asyncio.run(consume(agen([ev("final", 100.0, 11.4, QUESTION)]), Pipeline(), log, "other",
                        show=lambda *a, **k: None, spawn=lambda c: spawned.append(c)))
    assert spawned == []
