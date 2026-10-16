import pytest
import yaml

from fawa2ly.context import (ContextBuilder, Dropped, Preset, available, filter_bullets,
                              load_cv, summarise)
from fawa2ly.context.builder import NO_BACKGROUND
from fawa2ly.limits import MAX_BULLET_WORDS
from fawa2ly.llm.prompts import SYSTEM_V1

PRESET = {"name": "swe", "mode": "interview", "role": "Software engineer",
          "focus": ["tools they shipped with", "scale numbers"],
          "avoid": ["technologies not in their background"]}


def write_preset(tmp_path, data=PRESET, stem="swe"):
    p = tmp_path / f"{stem}.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def test_preset_load_and_prompt_sections(tmp_path):
    pre = Preset.load(write_preset(tmp_path))
    assert (pre.name, pre.mode, pre.role) == ("swe", "interview", "Software engineer")
    text = pre.as_prompt()
    assert "Who is speaking: Software engineer." in text
    assert "- tools they shipped with" in text
    assert "Do not suggest:\n- technologies not in their background" in text


def test_preset_defaults_and_empty_prompt(tmp_path):
    p = tmp_path / "bare.yaml"
    p.write_text("", encoding="utf-8")
    pre = Preset.load(p)
    assert (pre.name, pre.mode, pre.role, pre.focus, pre.avoid) == ("bare", "interview", "", [], [])
    assert pre.as_prompt() == ""


def test_preset_find_by_name_or_path_and_lists_available(tmp_path):
    write_preset(tmp_path)
    write_preset(tmp_path, {**PRESET, "name": "hr"}, stem="hr")
    assert available(str(tmp_path)) == ["hr", "swe"]
    assert Preset.find("swe", str(tmp_path)).role == "Software engineer"
    assert Preset.find(str(tmp_path / "hr.yaml")).name == "hr"
    with pytest.raises(FileNotFoundError) as e:
        Preset.find("nope", str(tmp_path))
    assert "hr, swe" in str(e.value)


def test_shipped_presets_are_valid():
    assert set(available()) >= {"swe", "hr", "meeting-standup"}
    for name in ("swe", "hr", "meeting-standup"):
        pre = Preset.find(name)
        assert pre.role and pre.focus and pre.mode in ("interview", "meeting")


def test_system_prompt_is_stable_and_carries_preset_and_cv(tmp_path):
    b = ContextBuilder(preset=Preset.load(write_preset(tmp_path)), cv_text="  Sam, teacher.  ")
    system = b.system()
    assert system.startswith(SYSTEM_V1)
    assert "Software engineer" in system
    assert system.endswith("Sam, teacher.")
    assert NO_BACKGROUND not in system
    assert b.build("Q1?").system == b.build("Q2?").system == system   # unchanged per question


def test_without_cv_the_prompt_forbids_inventing_facts():
    system = ContextBuilder().system()
    assert NO_BACKGROUND in system
    assert system.startswith(SYSTEM_V1)


def test_build_trims_history_to_the_last_turns():
    b = ContextBuilder(history_turns=2)
    turns = [("other", "a"), ("user", "b"), ("other", "c"), ("user", "d")]
    req = b.build("Why?", turns, max_tokens=99)
    assert req.history == [("other", "c"), ("user", "d")]
    assert req.question == "Why?" and req.max_tokens == 99
    assert ContextBuilder().build("Why?").history == []


def test_load_cv_text_and_unsupported_formats(tmp_path):
    p = tmp_path / "cv.txt"
    p.write_text("  Ten years of Python.\n", encoding="utf-8")
    assert load_cv(str(p)) == "Ten years of Python."
    docx = tmp_path / "cv.docx"
    docx.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="DOCX"):
        load_cv(str(docx))


def test_guardrail_keeps_good_lines_and_reports_reasons():
    over = " ".join(["word"] * (MAX_BULLET_WORDS + 1))
    kept, dropped = filter_bullets([
        "I moved to nights last year and it stopped working for me",
        over,                                              # too_long
        "Personal action: clarified ownership, isolated changes",   # label_prefix
        "I moved to Nights last year, and it stopped working for me!",  # duplicate of the first
        "The shift pattern was the reason, not the work itself",
        "I was made a trainer in my last job",
        "I finished the course with a certificate",
        "one line too many",                               # over_limit (5 already kept)
    ])
    assert kept == ["I moved to nights last year and it stopped working for me",
                    "The shift pattern was the reason, not the work itself",
                    "I was made a trainer in my last job",
                    "I finished the course with a certificate", "one line too many"]
    assert [(d.reason) for d in dropped] == ["too_long", "label_prefix", "duplicate"]
    assert summarise(dropped) == "1 duplicate, 1 label_prefix, 1 too_long"


def test_guardrail_over_limit_and_empty():
    kept, dropped = filter_bullets(["a", "b", "c", "", "d"], max_bullets=3)
    assert kept == ["a", "b", "c"]
    assert [(d.text, d.reason) for d in dropped] == [("", "empty"), ("d", "over_limit")]
    assert filter_bullets([]) == ([], [])
    assert summarise([]) == ""


def test_guardrail_keeps_spoken_sentences_and_drops_cv_labels():
    """The 2026-09-10 reversal (prompt v2.0): a sentence in the speaker's voice is the product.

    Until v2.0 the guardrail dropped exactly these lines as `full_sentence`, because the overlay
    showed keywords. The maintainer's verdict on a full rated run was that keywords are unsayable in
    front of an interviewer, so the rule now runs the other way: speech is kept, and the CV
    label-and-tags shape that used to survive is what gets dropped.
    """
    kept, dropped = filter_bullets([
        "I am very organized and I plan the week on Monday",
        "Real-Time Chat Application: offline queue, Firebase, deduplication",
        "What I would do differently: agree the interfaces before coding",
    ])
    assert kept == ["I am very organized and I plan the week on Monday",
                    "What I would do differently: agree the interfaces before coding"]
    assert [d.reason for d in dropped] == ["label_prefix"]


def test_dropped_is_a_plain_record():
    d = Dropped("x", "too_long")
    assert (d.text, d.reason) == ("x", "too_long")
