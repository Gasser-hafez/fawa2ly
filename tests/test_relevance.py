import asyncio
import json

import pytest
import yaml

from fawa2ly.llm import FakeLLM
from fawa2ly.relevance import (load_questions, load_rows, main, print_report, rate, run_tag,
                                save_rows, summarise)

QUESTIONS = {
    "mobile": [{"text": "Why Flutter?", "source": "script"}],
    "hr": [{"text": "What is your biggest weakness?", "source": "standard"}],
}


def write_questions(tmp_path, data=QUESTIONS):
    p = tmp_path / "q.yaml"
    # sort_keys=False so the fixture keeps the order written here, as the real file does
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return str(p)


def use_tmp_results(monkeypatch, tmp_path):
    monkeypatch.setattr("fawa2ly.relevance.RESULT_DIR", str(tmp_path / "relevance"))


def test_load_questions_flattens_presets_and_rejects_empty(tmp_path):
    qs = load_questions(write_questions(tmp_path))
    assert [(q["preset"], q["question"]) for q in qs] == [
        ("mobile", "Why Flutter?"), ("hr", "What is your biggest weakness?")]
    assert qs[0]["source"] == "script"
    with pytest.raises(ValueError, match="no questions"):
        load_questions(write_questions(tmp_path, {}))


def test_shipped_question_set_covers_every_preset_it_names():
    from fawa2ly.context import Preset
    qs = load_questions()
    presets = {q["preset"] for q in qs}
    assert presets == {"mobile", "hr"}
    for p in presets:
        Preset.find(p)                       # must exist, or `run` would fail halfway through
    assert len(qs) >= 15
    assert all(q["question"].strip() for q in qs)


def test_question_order_follows_the_file(tmp_path):
    """Ratings are compared across prompt versions, so the order must not depend on a dict."""
    path = write_questions(tmp_path, {"hr": [{"text": "b"}], "mobile": [{"text": "a"}]})
    assert [q["preset"] for q in load_questions(path)] == ["hr", "mobile"]


def test_run_saves_one_row_per_question_with_the_guardrail_applied(monkeypatch, tmp_path):
    use_tmp_results(monkeypatch, tmp_path)
    long_bullet = " ".join(["word"] * 30)
    fake = FakeLLM(bullets=["short and specific", long_bullet])
    rows = asyncio.run(run_tag("t", fake, "my background", load_questions(write_questions(tmp_path)),
                               show=lambda *a: None))
    assert len(rows) == 2
    assert rows[0]["bullets"] == ["short and specific"]
    assert rows[0]["dropped"] == [{"text": long_bullet, "reason": "too_long"}]
    assert rows[0]["rating"] is None and rows[0]["preset"] == "mobile"
    assert "my background" in fake.requests[0].system
    assert load_rows("t") == rows                     # persisted


def test_rerunning_keeps_ratings_for_answers_that_did_not_change(monkeypatch, tmp_path):
    use_tmp_results(monkeypatch, tmp_path)
    qs = load_questions(write_questions(tmp_path))
    asyncio.run(run_tag("t", FakeLLM(bullets=["same answer"]), "", qs, show=lambda *a: None))
    rows = load_rows("t")
    rows[0]["rating"] = 5
    rows[1]["rating"] = 4
    save_rows("t", rows)

    # identical bullets -> ratings survive
    asyncio.run(run_tag("t", FakeLLM(bullets=["same answer"]), "", qs, show=lambda *a: None))
    assert [r["rating"] for r in load_rows("t")] == [5, 4]

    # the answer changed -> the old score no longer applies to it
    asyncio.run(run_tag("t", FakeLLM(bullets=["a different answer"]), "", qs, show=lambda *a: None))
    assert [r["rating"] for r in load_rows("t")] == [None, None]


def test_rate_saves_each_answer_and_can_be_quit(monkeypatch, tmp_path):
    use_tmp_results(monkeypatch, tmp_path)
    qs = load_questions(write_questions(tmp_path))
    asyncio.run(run_tag("t", FakeLLM(bullets=["a"]), "", qs, show=lambda *a: None))
    answers = iter(["x", "5", "q"])            # invalid, then a score, then quit
    assert rate("t", ask=lambda _: next(answers), show=lambda *a: None) == 1
    assert [r["rating"] for r in load_rows("t")] == [5, None]

    answers = iter(["s"])                       # skip leaves it unrated
    rate("t", ask=lambda _: next(answers), show=lambda *a: None)
    assert [r["rating"] for r in load_rows("t")] == [5, None]


def test_rate_without_results_says_so(monkeypatch, tmp_path):
    use_tmp_results(monkeypatch, tmp_path)
    shown = []
    assert rate("missing", ask=lambda _: "5", show=shown.append) == 0
    assert "run first" in shown[0]


def test_summarise_reports_mean_per_preset_and_the_weak_answers():
    rows = [
        {"preset": "mobile", "question": "q1", "bullets": ["a"], "rating": 5},
        {"preset": "mobile", "question": "q2", "bullets": ["a"], "rating": 3},
        {"preset": "hr", "question": "q3", "bullets": [], "rating": 1, "error": None},
        {"preset": "hr", "question": "q4", "bullets": ["a"], "rating": None},
    ]
    s = summarise(rows)
    assert (s["n"], s["rated"], s["empty"]) == (4, 3, 1)
    assert s["mean"] == pytest.approx(3.0)
    assert s["by_preset"] == {"hr": 1.0, "mobile": 4.0}
    assert [r["question"] for r in s["weak"]] == ["q3"]
    shown = []
    print_report("t", s, show=shown.append)
    assert "mean 3.00" in shown[0] and any("weak (1)" in line for line in shown)


def test_summarise_with_nothing_rated():
    s = summarise([{"preset": "hr", "question": "q", "bullets": ["a"], "rating": None}])
    assert s["mean"] is None and s["by_preset"] == {}


def test_report_gate_fails_below_the_threshold(monkeypatch, tmp_path, capsys):
    use_tmp_results(monkeypatch, tmp_path)
    save_rows("low", [{"preset": "hr", "question": "q", "bullets": ["a"], "rating": 2}])
    save_rows("high", [{"preset": "hr", "question": "q", "bullets": ["a"], "rating": 5}])
    assert main(["report", "high", "--gate", "4.0"]) == 0
    assert main(["report", "low", "--gate", "4.0"]) == 1
    assert "GATE FAILED" in capsys.readouterr().out
    assert main(["report", "nosuchtag"]) == 0


def test_answers_are_saved_as_they_arrive_not_only_at_the_end(monkeypatch, tmp_path):
    """A 30-question free-tier run takes over ten minutes; an interruption must not lose it."""
    use_tmp_results(monkeypatch, tmp_path)
    qs = load_questions(write_questions(tmp_path))
    seen = []

    class Failing(FakeLLM):
        async def suggest(self, req):
            seen.append(req.question)
            if len(seen) == 2:
                raise KeyboardInterrupt
            async for ev in super().suggest(req):
                yield ev

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_tag("t", Failing(bullets=["a"]), "", qs, show=lambda *a: None))
    assert len(load_rows("t")) == 1        # the first answer survived the interruption
