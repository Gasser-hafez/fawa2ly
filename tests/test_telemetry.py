import json

import pytest

from fawa2ly.telemetry import load_rows, main, percentile, stats, summarize


def test_percentile_empty_and_single():
    assert percentile([], 50) is None
    assert percentile([7], 95) == 7


def test_percentile_interpolates_and_sorts():
    assert percentile([40, 10, 30, 20], 0) == 10
    assert percentile([40, 10, 30, 20], 50) == 25
    assert percentile([40, 10, 30, 20], 95) == pytest.approx(38.5)
    assert percentile([40, 10, 30, 20], 100) == 40


def test_stats_drops_none():
    s = stats([None, 300, 100, None, 500])
    assert (s["n"], s["min"], s["max"]) == (3, 100, 500)
    assert s["p50"] == pytest.approx(300)
    assert s["p95"] == pytest.approx(480)
    assert stats([None]) == {"n": 0}


ROWS = [
    {"n": 1, "trigger": "question_mark", "speech_to_text_ms": 400, "gap_ms": 0, "text": "a?"},
    {"n": 2, "trigger": "silence", "speech_to_text_ms": 1200, "gap_ms": 1000, "text": "b"},
    {"n": 3, "trigger": "question_mark", "speech_to_text_ms": -50, "gap_ms": 0, "text": "c?"},
    {"n": 4, "trigger": "silence", "speech_to_text_ms": None, "gap_ms": 900, "text": "d"},
]


def test_summarize_groups_by_trigger_and_flags_negative():
    s = summarize(ROWS)
    assert s["rows"] == 4
    assert s["metrics"]["speech_to_text_ms"]["n"] == 3
    assert s["metrics"]["speech_to_text_ms"]["missing"] == 1
    assert s["metrics"]["gap_ms"]["n"] == 4
    assert set(s["by_trigger"]) == {"question_mark", "silence"}
    assert s["by_trigger"]["question_mark"]["min"] == -50
    assert s["by_trigger"]["silence"]["n"] == 1  # the None row is dropped
    assert [r["n"] for r in s["negative"]] == [3]


def test_load_rows_skips_blank_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps(ROWS[0]) + "\n\n" + json.dumps(ROWS[1]) + "\n", encoding="utf-8")
    assert [r["n"] for r in load_rows(p)] == [1, 2]


def test_main_exit_code_reflects_negatives(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps(ROWS[0]) + "\n", encoding="utf-8")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(ROWS[2]) + "\n", encoding="utf-8")

    assert main([str(good)]) == 0
    assert "all speech_to_text_ms values positive" in capsys.readouterr().out

    assert main([str(bad)]) == 1
    assert "NEGATIVE" in capsys.readouterr().out

    assert main([]) == 2


def test_trigger_key_marks_interim_fires():
    from fawa2ly.telemetry import trigger_key
    assert trigger_key({"trigger": "question_mark"}) == "question_mark"
    assert trigger_key({"trigger": "question_mark", "source": "final"}) == "question_mark"
    assert trigger_key({"trigger": "question_mark", "source": "interim"}) == "question_mark@interim"
    s = summarize([{"trigger": "question_mark", "source": "interim", "speech_to_text_ms": 400, "gap_ms": 0,
                    "question_end_to_fire_ms": 400}])
    assert set(s["by_trigger"]) == {"question_mark@interim"}
    assert s["metrics"]["question_end_to_fire_ms"]["n"] == 1
