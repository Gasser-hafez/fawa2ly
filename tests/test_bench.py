import asyncio

from fawa2ly.bench import ModelResult, bench_model, looks_refused, main, usable_bullets
from fawa2ly.limits import MAX_BULLET_WORDS
from fawa2ly.llm import FakeLLM
from fawa2ly.llm.base import Suggestion


def suggestion(bullets, text=None, ttft=100, total=200, usage=None, error=None, model="m"):
    text = "\n".join(f"- {b}" for b in bullets) if text is None else text
    return Suggestion(question="q?", text=text, bullets=bullets, model=model, provider="p",
                      t_start=0.0, t_first_token=None if ttft is None else ttft / 1000,
                      t_done=total / 1000, usage=usage, error=error)


def test_usable_bullets_drops_overlong_ones():
    too_long = " ".join(["word"] * (MAX_BULLET_WORDS + 1))
    s = suggestion(["short and useful", too_long])
    assert usable_bullets(s) == ["short and useful"]
    assert usable_bullets(suggestion([" ".join(["word"] * MAX_BULLET_WORDS)])) != []


def test_looks_refused_catches_decline_text_and_all_long_bullets():
    assert looks_refused(suggestion(["I can't help with this. I'm Kiro, a development environment."]))
    assert looks_refused(suggestion(["Interview coaching falls outside what I do here today."]))
    assert looks_refused(suggestion([" ".join(["word"] * (MAX_BULLET_WORDS + 5))]))       # no usable bullet at all
    assert not looks_refused(suggestion(["perfectionism, learning to ship earlier"]))
    assert not looks_refused(suggestion([], text=""))                  # empty is an error, not a refusal


def test_model_result_aggregates_and_formats():
    res = ModelResult(model="vendor/model-x")
    res.add(suggestion(["a b c"], ttft=100, total=300, usage={"input_tokens": 110}, model="routed"))
    res.add(suggestion(["d e f"], ttft=300, total=500, usage={"input_tokens": 130}, model="routed"))
    res.add(suggestion(["I can't help with that request at all right now."], ttft=200, total=400,
                       model="routed"))
    res.add(suggestion([], error="ConnectionError: down"))
    assert res.ok == 3 and res.errors == ["ConnectionError: down"]
    assert res.refusals == 1 and res.routed_to == {"routed"}
    assert res.stat(res.ttfts) == 200 and res.stat(res.input_tokens) == 120
    row = res.row()
    assert row.startswith("vendor/model-x") and "3/4" in row
    assert res.samples == ["a b c", "d e f"]


def test_bench_model_rotates_questions_and_counts_runs():
    fake = FakeLLM(bullets=["one two", "three four"])
    res = asyncio.run(bench_model(fake, "fake-model", ["Q1?", "Q2?"], runs=3, interval=0, max_tokens=99))
    assert res.model == "fake-model" and res.ok == 3 and res.refusals == 0
    assert [r.question for r in fake.requests] == ["Q1?", "Q2?", "Q1?"]
    assert fake.requests[0].max_tokens == 99
    assert res.stat(res.usable) == 2


def test_main_with_fake_writes_json_and_needs_models(tmp_path, capsys):
    out = tmp_path / "bench.jsonl"
    assert main(["--fake", "--models", "a,b", "--runs", "1", "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "fastest without refusals" in printed
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 2

    assert main(["--fake"]) == 2
