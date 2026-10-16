"""The Dynamic answer profile (roadmap item 27, 2026-09-13): the model picks the shape of each
answer - full, keywords or direction - and says which on the first line of its reply.

What has to hold: the three fixed profiles send exactly the prompt they sent before; the dynamic
prompt carries all three shapes and the rule for choosing; the marker is read before any line is
judged, because the word limit and the label rule differ per shape and the lines are filtered as
they stream; the marker is never shown; an answer that arrives without one is still shown rather
than emptied; and a follow-up keeps the shape of the card it is added to.
"""

import asyncio

import pytest

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder
from fawa2ly.context.guardrail import filter_bullets, style_choice
from fawa2ly.limits import DIRECTION_WORDS, KEYWORD_WORDS, MAX_BULLET_WORDS
from fawa2ly.llm import FakeLLM
from fawa2ly.llm.prompts import DYNAMIC, MODES, SHAPES, STYLES, system_for

from test_assist import utt

CUE = "Chat app: offline queue, Firebase sync"          # a keyword cue, and a label_prefix to "full"
SENTENCE = "I built the offline queue so messages survive a dropped connection."


def suggester(bullets, style=DYNAMIC, **kw):
    """A suggester on one profile, answering with these lines."""
    return Suggester(FakeLLM(bullets=bullets), builder=ContextBuilder(style=style), **kw)


# ---- the prompt ----

def test_the_three_fixed_profiles_are_unchanged_and_dynamic_holds_all_three():
    for mode in MODES:
        dynamic = system_for(DYNAMIC, mode)
        for shape in SHAPES:
            fixed = system_for(shape, mode)
            assert "STYLE:" not in fixed, f"{shape}/{mode} must not learn about the marker"
            assert f"Rules for {shape}, when you choose it:" in dynamic
            # every shape rule of the fixed profile is in the dynamic one, so they cannot drift
            for rule in fixed.split("\nRules:\n")[1].split("\n- "):
                if rule.strip():
                    assert rule.strip() in dynamic, f"{mode}/{shape}: {rule[:60]}"
        assert "full is the default" in dynamic
        assert dynamic.isascii(), "the marker and the rules stay English in an Arabic call"


def test_dynamic_is_a_style_the_builder_and_the_settings_accept():
    assert STYLES == SHAPES + (DYNAMIC,)
    b = ContextBuilder(style="keywords")
    b.set_style(DYNAMIC)
    assert b.style == DYNAMIC and b.system().startswith(system_for(DYNAMIC, "interview"))
    with pytest.raises(ValueError):
        b.set_style("whatever")


# ---- reading the marker ----

def test_the_marker_is_read_and_never_shown():
    lines = []
    styles = []
    s = suggester(["STYLE: keywords", CUE, "Acme Tax: Laravel, shared repo"],
                  on_line=lambda n, t: lines.append(t), on_style=lambda n, st: styles.append((n, st)))
    a = asyncio.run(s.handle(utt()))

    assert styles == [(1, "keywords")], "announced once, under the question's number"
    assert "STYLE: keywords" not in lines and all("STYLE" not in b for b in a.bullets)
    assert a.style == "keywords" and a.chose_style is True
    assert lines == [CUE, "Acme Tax: Laravel, shared repo"], "keyword cues survive their own rules"
    assert [d["reason"] for d in a.dropped if d["text"].startswith("STYLE")] == ["style"]


def test_the_shape_is_known_before_the_first_line_is_judged():
    """The lines are filtered as they stream, and a cue is a label_prefix under the full rules: if
    the marker were read after the fact, a keyword answer would reach the overlay empty."""
    lines = []
    s = suggester(["STYLE: keywords", CUE], on_line=lambda n, t: lines.append(t))
    asyncio.run(s.handle(utt()))
    assert lines == [CUE]

    # the same lines on the full profile, where that shape is not what was asked for
    kept, dropped = filter_bullets([CUE], style="full")
    assert kept == [] and dropped[0].reason == "label_prefix"


def test_each_shapes_word_limit_is_applied_to_the_shape_the_model_chose():
    long_cue = " ".join(["word"] * (KEYWORD_WORDS + 2))
    s = suggester(["STYLE: keywords", long_cue, CUE])
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == [CUE] and "too_long" in [d["reason"] for d in a.dropped]

    long_move = "Open with " + " ".join(["word"] * DIRECTION_WORDS)
    s = suggester(["STYLE: direction", long_move, "Open with the offline queue"])
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == ["Open with the offline queue"] and a.style == "direction"

    s = suggester(["STYLE: full", SENTENCE, " ".join(["word"] * (MAX_BULLET_WORDS + 1))])
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == [SENTENCE] and a.style == "full"


def test_an_answer_with_no_marker_is_shown_rather_than_emptied():
    """A model that forgets the first line must not cost the user the whole answer, so an unplaced
    answer is judged by the most permissive of the three."""
    lines = []
    s = suggester([CUE, SENTENCE], on_line=lambda n, t: lines.append(t))
    a = asyncio.run(s.handle(utt()))

    assert lines == [CUE, SENTENCE], "a cue is not dropped as a label here, and nor is a sentence"
    assert a.style == "" and a.chose_style is False, "the record says the model never placed it"


def test_a_marker_naming_something_that_is_not_a_shape_is_dropped_but_places_nothing():
    s = suggester(["STYLE: poetry", SENTENCE])
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == [SENTENCE], "never shown, whatever it names"
    assert a.style == "" and a.chose_style is False
    assert style_choice("STYLE: poetry") is None


def test_the_marker_is_only_read_on_dynamic():
    """On a fixed profile the model is never asked for one, and a line that looks like one is still
    not a line to say."""
    s = suggester(["STYLE: keywords", SENTENCE], style="full")
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == [SENTENCE] and a.style == "full" and a.chose_style is False
    assert s.answer_style == {}


# ---- follow-ups ----

def test_more_keeps_the_shape_of_the_card_it_is_added_to():
    lines = []
    s = suggester(["STYLE: keywords", CUE], on_line=lambda n, t: lines.append(t))
    asyncio.run(s.handle(utt()))

    s.provider = FakeLLM(bullets=["STYLE: full", "Firebase: retry and backoff"])
    a = asyncio.run(s.more(1))

    assert a is not None and s.answer_style[1] == "keywords", "the card does not change shape"
    assert "Firebase: retry and backoff" in lines, "a cue is still allowed on this card"
    assert all("STYLE" not in line for line in lines)


def test_a_retry_lets_the_model_place_the_answer_again():
    s = suggester(["STYLE: keywords", CUE])
    asyncio.run(s.handle(utt()))
    assert s.answer_style[1] == "keywords"

    s.provider = FakeLLM(bullets=["STYLE: full", SENTENCE])
    a = asyncio.run(s.retry(1))
    assert a is not None and a.style == "full" and s.answer_style[1] == "full"
    assert a.bullets == [SENTENCE]


# ---- what a call and an eval keep of the choice ----

def test_the_call_summary_keeps_the_shape_each_answer_took():
    """A saved call is the only place the choice can be judged after a real conversation rather
    than an eval, so it has to say which shape each answer was given."""
    from fawa2ly.summary import CallRecord

    record = CallRecord(clock=iter([0.0] + [1.0] * 12).__next__)
    record.observe({"type": "status", "state": "listening", "style": DYNAMIC})
    record.observe({"type": "question", "n": 1, "text": "Why did you leave?"})
    record.observe({"type": "assist", "assist": {"n": 1, "question": "Why did you leave?",
                                                 "bullets": ["Open with the shift pattern"],
                                                 "style": "direction", "chose_style": True}})
    summary = record.summary()
    assert summary["style"] == DYNAMIC, "the call's profile"
    assert summary["questions"][0]["style"] == "direction", "and this answer's shape"


def test_the_fake_model_answers_in_each_shape_in_turn_only_when_asked_to_choose():
    """The overlay has to be workable against all three with no key, and a fixture that only ever
    picked one would leave two of them unseen until a real call."""
    from fawa2ly.llm import FakeLLM
    from fawa2ly.llm.prompts import system_for

    fake = FakeLLM()
    s = Suggester(fake, builder=ContextBuilder(style=DYNAMIC))
    shapes = [asyncio.run(s.handle(utt(n=i, text=f"Question {i}?"))).style for i in range(1, 5)]
    assert shapes == ["full", "keywords", "direction", "full"], "one shape per question, in turn"
    assert fake.shapes_answered == shapes

    fixed = FakeLLM()
    s = Suggester(fixed, builder=ContextBuilder(style="full"))
    a = asyncio.run(s.handle(utt()))
    assert fixed.shapes_answered == [] and "STYLE:" not in system_for("full")
    assert all("STYLE" not in b for b in a.bullets)

    told = FakeLLM(bullets=["Just this one"])
    s = Suggester(told, builder=ContextBuilder(style=DYNAMIC))
    a = asyncio.run(s.handle(utt()))
    assert a.bullets == ["Just this one"], "bullets given explicitly still win: a test says what it means"


def test_the_relevance_harness_records_and_counts_the_shapes_chosen(tmp_path, monkeypatch):
    """Rating Dynamic means knowing what it chose, not only how the answers scored: a run that never
    leaves full, or one that reaches for cues on questions the CV is silent about, is a prompt
    problem the ratings alone would not name."""
    import asyncio as aio

    from fawa2ly import relevance
    from fawa2ly.llm import FakeLLM
    from fawa2ly.relevance import run_tag, summarise

    monkeypatch.setattr(relevance, "RESULT_DIR", str(tmp_path))   # never into docs/eval
    questions = [{"preset": "mobile", "question": f"Question {i}?"} for i in range(1, 4)]
    rows = aio.run(run_tag("test-dynamic", FakeLLM(), "A CV.", questions, show=lambda *a: None,
                           style=DYNAMIC))
    assert [r["shape"] for r in rows] == ["full", "keywords", "direction"]
    assert summarise(rows)["shapes"] == {"full": 1, "keywords": 1, "direction": 1}
    assert summarise([{**r, "style": "full", "shape": None} for r in rows])["shapes"] == {}, \
        "a fixed profile reports no shapes at all"


# ---- what the review of 2026-09-13 found ----

def test_only_the_first_marker_places_an_answer():
    """A model that names a second shape further down would otherwise re-place a card whose earlier
    lines are already on screen: the arrows would appear partway down, and a line kept under one
    word limit would be re-judged by another."""
    events = []
    s = suggester(["STYLE: full", SENTENCE, "STYLE: keywords", CUE],
                  on_line=lambda n, t: events.append(("line", t)),
                  on_style=lambda n, st: events.append(("style", st)))
    a = asyncio.run(s.handle(utt()))

    assert [e for e in events if e[0] == "style"] == [("style", "full")], "placed once"
    assert a.style == "full" and s.answer_style[1] == "full"
    assert [t for kind, t in events if kind == "line"] == [SENTENCE], \
        "the card stays a full card, so the cue that followed is not one of its lines"
    assert all("STYLE" not in b for b in a.bullets), "the second marker is still never shown"


def test_switching_the_profile_during_a_call_tells_the_windows():
    """The overlay draws by the style it was last told: direction lines as arrows, and on Dynamic a
    shape per card. Until this was sent, the answers after a mid-call switch were drawn in the shape
    of the profile before it."""
    from fawa2ly.engine import Engine, EngineConfig

    seen = []
    e = Engine(EngineConfig(suggest=True, fake_llm=True), on_event=seen.append)
    e.suggester = Suggester(FakeLLM(), builder=ContextBuilder(style="full"))

    e.set_style(DYNAMIC)
    assert [x for x in seen if x["type"] == "status"] == [
        {"type": "status", "state": "listening", "style": DYNAMIC}]

    seen.clear()
    with pytest.raises(ValueError):
        e.set_style("nonsense")
    assert seen == [] and e.cfg.style == DYNAMIC, "a refused switch changes nothing and says nothing"
