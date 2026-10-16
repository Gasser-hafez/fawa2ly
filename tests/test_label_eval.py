import json

from fawa2ly.eval import expand, score
from fawa2ly.label import default_out_path, extract_sentences, label_key, load_labels, run


def dg_final(words):
    """words: (punctuated, start, end, speaker)."""
    return {"type": "Results", "is_final": True,
            "channel": {"alternatives": [{"transcript": " ".join(w[0] for w in words), "words": [
                {"word": t.lower().strip("?.,"), "punctuated_word": t, "start": s, "end": e, "speaker": spk}
                for t, s, e, spk in words]}]}}


def raw_line(t_recv, t_rel, msg):
    return json.dumps({"t_recv": t_recv, "t_rel": t_rel, "msg": msg})


def write_raw(path):
    path.write_text("\n".join([
        raw_line(1.0, None, {"type": "Metadata"}),
        raw_line(2.0, 1.0, {"type": "Results", "is_final": False,
                            "channel": {"alternatives": [{"transcript": "What is", "words": [
                                {"word": "what", "punctuated_word": "What", "start": 0.0, "end": 0.2, "speaker": 0}]}]}}),
        raw_line(5.0, 3.0, dg_final([("What", 0.0, 0.2, 0), ("is", 0.2, 0.3, 0), ("your", 0.3, 0.5, 0), ("name?", 0.5, 1.0, 0),
                                     ("Sam.", 1.5, 1.8, 1), ("Nice", 1.9, 2.1, 1), ("to", 2.1, 2.2, 1)])),
        raw_line(8.0, 6.0, dg_final([("meet", 2.3, 2.5, 1), ("you.", 2.5, 2.8, 1)])),
    ]) + "\n", encoding="utf-8")


def test_extract_sentences_finals_only_split_by_speaker_and_sentence(tmp_path):
    raw = tmp_path / "s.raw.jsonl"
    write_raw(raw)
    sents = extract_sentences(raw)
    assert [(s["speaker"], s["text"], s["complete"]) for s in sents] == [
        (0, "What is your name?", True),
        (1, "Sam.", True),
        (1, "Nice to", False),
        (1, "meet you.", True),
    ]
    assert sents[0]["file"] == "s.raw.jsonl" and sents[0]["start"] == 0.0 and sents[0]["end"] == 1.0


def test_default_out_path_strips_raw_suffix():
    assert default_out_path("interview6.raw.jsonl").replace("\\", "/") == "docs/eval/interview6.labels.jsonl"
    assert default_out_path("C:/x/y/session.jsonl").replace("\\", "/") == "docs/eval/session.labels.jsonl"


def test_run_saves_answers_skips_and_resumes(tmp_path):
    raw = tmp_path / "s.raw.jsonl"
    write_raw(raw)
    out = tmp_path / "s.labels.jsonl"
    shown = []

    answers = iter(["x", "y", "n", "s", "q"])      # invalid, then y, n, skip, quit
    new, total = run(raw, out, ask=lambda _: next(answers), show=shown.append)
    assert (new, total) == (2, 4)
    labels = load_labels(out)
    assert [(k[3], v["label"]) for k, v in labels.items()] == [("What is your name?", True), ("Sam.", False)]

    answers = iter(["n", "y"])                      # second run only asks the two unlabeled ones
    new, total = run(raw, out, ask=lambda _: next(answers), show=shown.append)
    assert (new, total) == (2, 4)
    labels = load_labels(out)
    assert len(labels) == 4
    assert labels[label_key(extract_sentences(raw)[3])]["label"] is True

    new, total = run(raw, out, ask=lambda _: (_ for _ in ()).throw(AssertionError("should not ask")), show=shown.append)
    assert (new, total) == (0, 4)


def test_score_precision_recall_and_lists():
    rows = [
        {"file": "a", "speaker": 0, "text": "What is your name?", "label": True},        # tp
        {"file": "a", "speaker": 0, "text": "Tell me about yourself.", "label": True},   # tp
        {"file": "a", "speaker": 1, "text": "How many of them are there.", "label": False},  # fp (wh_word)
        {"file": "a", "speaker": 0, "text": "I want to ask you about salary.", "label": True},     # fn
        {"file": "a", "speaker": 1, "text": "I worked there for two years.", "label": False},       # tn
    ]
    s = score(rows)
    assert (s["tp"], s["fp"], s["fn"], s["tn"]) == (2, 1, 1, 1)
    assert s["precision"] == 2 / 3 and s["recall"] == 2 / 3
    assert s["f1"] == 2 / 3
    assert [r["text"] for r in s["misses"]] == ["I want to ask you about salary."]
    assert [(r["text"], r["reason"]) for r in s["false_alarms"]] == [("How many of them are there.", "wh_word")]


def test_turns_are_scored_as_the_app_answers_them(tmp_path):
    """--turns (2026-09-12): the recording is replayed in turn mode and each labeled piece is placed in
    the turn that holds it, so a turn is right or wrong once, however Deepgram cut it."""
    from fawa2ly.eval import replay_turns, score_turns
    end = lambda t, last: raw_line(t, t, {"type": "UtteranceEnd", "last_word_end": last})  # noqa: E731
    raw = tmp_path / "s.raw.jsonl"
    raw.write_text("\n".join([
        raw_line(1.0, None, {"type": "Metadata"}),
        raw_line(2.0, 1.2, dg_final([("What", 0.0, 0.2, 0), ("is", 0.2, 0.3, 0), ("your", 0.3, 0.5, 0), ("name?", 0.5, 1.0, 0)])),
        end(2.5, 1.0),
        raw_line(3.0, 2.9, dg_final([("Sam.", 1.5, 1.8, 1), ("Nice", 1.9, 2.1, 1), ("to", 2.1, 2.2, 1),
                                     ("meet", 2.3, 2.5, 1), ("you.", 2.5, 2.8, 1)])),
        end(3.5, 2.8),
        raw_line(4.0, 3.7, dg_final([("Is", 3.0, 3.2, 0), ("that", 3.2, 3.4, 0), ("right?", 3.4, 3.6, 0)])),
        end(4.5, 3.6),
        raw_line(5.0, 5.1, dg_final([("I", 4.0, 4.1, 0), ("want", 4.1, 4.3, 0), ("to", 4.3, 4.4, 0), ("ask", 4.4, 4.6, 0),
                                     ("about", 4.6, 4.8, 0), ("salary.", 4.8, 5.0, 0)])),
        end(5.5, 5.0),
        raw_line(6.0, 6.6, dg_final([("Tell", 6.0, 6.2, 0), ("me", 6.2, 6.3, 0), ("more.", 6.3, 6.5, 0)])),
    ]) + "\n", encoding="utf-8")
    piece = lambda text, s, e, label, spk=0: {"file": "s.raw.jsonl", "speaker": spk, "start": s, "end": e,  # noqa: E731
                                              "text": text, "label": label}
    rows = [piece("What is your name?", 0.0, 1.0, True),
            piece("Sam.", 1.5, 1.8, False, 1), piece("Nice to meet you.", 1.9, 2.8, False, 1),
            piece("Is that right?", 3.0, 3.6, False),                 # rhetorical: answering it is a false alarm
            piece("I want to ask about salary.", 4.0, 5.0, True),     # a question the rules miss
            piece("Tell me more.", 6.0, 6.5, True)]                   # the recording ends before its turn closes
    turns = replay_turns(str(raw))
    assert [(u.trigger, u.audio_start, u.audio_end) for u in turns] == [
        ("question_mark", 0.0, 1.0), ("silence", 1.5, 2.8), ("question_mark", 3.0, 3.6), ("silence", 4.0, 5.0)]
    s = score_turns(rows, {"s.raw.jsonl": turns})
    assert (s["tp"], s["fp"], s["fn"], s["tn"]) == (1, 1, 1, 1)
    assert s["precision"] == 0.5 and s["recall"] == 0.5
    assert [e["text"] for e in s["false_alarms"]] == ["Is that right?"]
    assert [e["questions"] for e in s["misses"]] == [["I want to ask about salary."]]
    assert [r["text"] for r in s["unplaced"]] == ["Tell me more."]


def test_score_handles_empty_classes():
    s = score([{"file": "a", "speaker": 0, "text": "Fine.", "label": False}])
    assert s["precision"] is None and s["recall"] is None and s["f1"] is None


def test_expand_globs(tmp_path):
    (tmp_path / "a.labels.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "b.labels.jsonl").write_text("", encoding="utf-8")
    hits = expand([str(tmp_path / "*.labels.jsonl")])
    assert [h.replace("\\", "/").rsplit("/", 1)[1] for h in hits] == ["a.labels.jsonl", "b.labels.jsonl"]
    assert expand(["plain.jsonl"]) == ["plain.jsonl"]


def test_import_labels_marks_listed_indices_and_records_source(tmp_path):
    from fawa2ly.label import import_labels
    raw = tmp_path / "s.raw.jsonl"
    write_raw(raw)
    out = tmp_path / "s.labels.jsonl"
    shown = []
    assert import_labels(raw, out, {1}, "model-x", show=shown.append) == 4
    labels = load_labels(out)
    assert [(k[3], v["label"], v["labeled_by"]) for k, v in labels.items()] == [
        ("What is your name?", True, "model-x"), ("Sam.", False, "model-x"),
        ("Nice to", False, "model-x"), ("meet you.", False, "model-x")]
    assert import_labels(raw, out, {1}, "model-x", show=shown.append) == 0   # nothing left to label
    import pytest
    with pytest.raises(SystemExit):
        import_labels(raw, tmp_path / "other.jsonl", {99}, "model-x", show=shown.append)
