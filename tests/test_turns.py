"""Turn mode: answer the interviewer's whole turn, closed by silence.

Every sentence-level rule before this answered a fragment in some recording. rehearsal5 is the
case these tests are built from: Deepgram endpointed three times inside one question, because a
synthetic voice pauses between phrases, and only UtteranceEnd arrived after all of it.
"""

import asyncio

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder
from fawa2ly.llm import FakeLLM
from fawa2ly.pipeline import Pipeline, Utterance
from fawa2ly.stt.base import STTEvent, Word


def final(t_recv, t_rel, spec, endpointed=True):
    words = [Word(t, s, e, 0) for t, s, e in spec]
    return STTEvent("final", t_recv, t_rel, " ".join(w.text for w in words), words,
                    endpointed=endpointed)


def end(t_recv, t_rel):
    return STTEvent("utterance_end", t_recv, t_rel)


SELF = [("Tell", 6.95, 7.1), ("me", 7.1, 7.2), ("about", 7.2, 7.5), ("yourself.", 7.5, 8.15)]
FLUTTER = [("Your", 8.77, 8.9), ("experience", 8.9, 9.4), ("with", 9.4, 9.6), ("Flutter.", 9.6, 10.21)]
PROUD = [("Including", 10.66, 11.0), ("one", 11.0, 11.2), ("project", 11.2, 11.6), ("you're", 11.6, 11.9),
         ("particularly", 11.9, 12.5), ("proud", 12.5, 12.9), ("of.", 12.9, 13.3)]
WHOLE = ("Tell me about yourself. Your experience with Flutter. "
         "Including one project you're particularly proud of.")


def feed(pipe, events):
    out = []
    for ev in events:
        out += pipe.feed(ev)
    return out


def test_the_whole_turn_is_the_question_even_across_three_endpointed_finals():
    pipe = Pipeline(turns=True, speculate=False)
    assert feed(pipe, [final(8.9, 8.9, SELF), final(10.8, 10.8, FLUTTER), final(13.9, 13.9, PROUD)]) == []
    out = pipe.feed(end(14.9, 14.87))
    assert len(out) == 1
    assert out[0].text == WHOLE and out[0].source == "turn" and out[0].trigger == "request_opener"
    assert out[0].question_end_to_fire_ms == round((14.87 - 13.3) * 1000)


def test_a_turn_without_a_question_is_history():
    pipe = Pipeline(turns=True, speculate=False)
    feed(pipe, [final(2.0, 2.0, [("Great,", 1.0, 1.3), ("thanks", 1.3, 1.6), ("for", 1.6, 1.7),
                                 ("that.", 1.7, 2.0)])])
    out = pipe.feed(end(3.1, 3.0))
    assert len(out) == 1 and out[0].trigger == "silence"


def test_an_early_ask_is_made_once_per_question_text_and_is_never_the_answer_itself():
    pipe = Pipeline(turns=True)
    early = feed(pipe, [final(8.9, 8.9, SELF), final(10.8, 10.8, FLUTTER), final(13.9, 13.9, PROUD)])
    assert [u.text for u in early] == ["Tell me about yourself."]
    assert all(u.speculative for u in early)
    closed = pipe.feed(end(14.9, 14.87))
    assert [u.speculative for u in closed] == [False] and closed[0].text == WHOLE


def test_reopening_is_off_by_default_and_capped_when_on():
    """interview7: all 24 reopens glued the candidate's answer onto the question."""
    later = [("Especially", 9.6, 10.0), ("your", 10.0, 10.2), ("Firebase", 10.2, 10.7), ("work.", 10.7, 11.2)]
    for reopen in (False, True):
        pipe = Pipeline(turns=True, speculate=False, reopen=reopen)
        first = feed(pipe, [final(8.9, 8.9, SELF), end(9.3, 9.2)])
        assert len(first) == 1
        out = feed(pipe, [final(11.3, 11.3, later), end(12.3, 12.2)])
        assert len(out) == 1
        if not reopen:
            assert out[0].revises is None and out[0].trigger == "silence"
        else:
            assert out[0].revises == first[0].n
            assert out[0].text == "Tell me about yourself. Especially your Firebase work."

    answer = [(f"w{i}", 9.6 + i * 0.2, 9.75 + i * 0.2) for i in range(24)]
    pipe = Pipeline(turns=True, speculate=False, reopen=True)
    feed(pipe, [final(8.9, 8.9, SELF), end(9.3, 9.2)])
    out = feed(pipe, [final(15.0, 15.0, answer), end(16.0, 15.6)])
    assert out[0].revises is None, "a turn that long after a question is an answer, not the rest of it"


def utterance(n, text, speculative=False):
    return Utterance(n=n, speaker=0, trigger="request_opener", text=text, segments=1,
                     t_first_final=0.0, t_last_final=0.0, t_flush=0.0, gap_ms=0,
                     speech_to_text_ms=None, source="speculative" if speculative else "turn",
                     question_end_to_fire_ms=None if speculative else 1570,
                     stream="other", speculative=speculative)


def run_suggester(utterances):
    seen = []
    llm = FakeLLM(bullets=["I built the chat app on Firebase with an offline queue"])
    s = Suggester(llm, ContextBuilder(),
                  on_question=lambda n, t: seen.append(("question", n, t)),
                  on_line=lambda n, t: seen.append(("line", n, t)),
                  on_result=lambda a: seen.append(("assist", a.n, a.question)))

    async def go():
        for u in utterances:
            await s.handle(u)

    asyncio.run(go())
    return s, llm, seen


def test_an_early_ask_that_matches_the_closed_turn_is_shown_without_asking_again():
    s, llm, seen = run_suggester([utterance(1, "Why Flutter?", speculative=True),
                                  utterance(2, "Why Flutter?")])
    assert len(llm.requests) == 1, "the answer was already on its way"
    assert [kind for kind, _, _ in seen] == ["question", "line", "assist"]
    assert s.history == [("them", "Why Flutter?")], "an early ask is not conversation history"


def test_an_early_ask_for_part_of_the_turn_is_dropped_and_the_whole_turn_is_asked():
    s, llm, seen = run_suggester([utterance(1, "Tell me about yourself.", speculative=True),
                                  utterance(2, WHOLE)])
    assert len(llm.requests) == 2
    assert [kind for kind, _, _ in seen] == ["question", "line", "assist"]
    assert seen[0][2] == WHOLE and seen[-1][2] == WHOLE
