"""
Score the question detector against labeled sentences (M1 DoD: P >= 85 %, R >= 85 %).

    python -m fawa2ly.eval docs/eval/*.labels.jsonl
    python -m fawa2ly.eval docs/eval/*.labels.jsonl --gate 0.85   # exit 1 if P or R is below
    python -m fawa2ly.eval docs/eval/*.labels.jsonl --quiet       # numbers only
    python -m fawa2ly.eval docs/eval/*.labels.jsonl --turns       # per turn, as the app answers

Runs detector.classify on every labeled sentence and prints precision, recall,
F1, then every miss and every false alarm with its file, so a rule change is
judged by numbers on the versioned set, not by eye.
Wildcards are expanded here because PowerShell does not expand them.

**Per turn** (`--turns`, 2026-09-12). Since 2026-09-10 the app does not answer a sentence: it
answers a whole turn of the other side, closed by a second of silence or a change of speaker, when
any sentence in it is a question (pipeline turn mode). The per-sentence numbers above therefore
count things the app no longer does, such as a question's tail firing again after its head. With
`--turns` each recording's raw log is replayed through the same pipeline, every labeled piece is
placed in the turn that holds it (by audio time), and a turn counts as a question when any piece
in it is labeled one. Precision is then the share of answered turns that held a question, recall
the share of question turns that were answered. The raw logs are found next to the label file's
`file` names, in `--raw-dir` (the project folder by default).
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import sys
from typing import Callable, Optional

from .turn.detector import classify

# The triggers that make the app answer a turn (fawa2ly.assist.QUESTION_TRIGGERS, repeated here so
# the eval does not import the model code).
ANSWERED = ("question_mark", "request_opener", "wh_word", "yes_no")
PLACE_TOLERANCE_S = 0.05


def load_rows(paths) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _rates(tp: int, fp: int, fn: int) -> tuple:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return precision, recall, f1


def score(rows: list[dict], classify_fn: Callable[[str], Optional[str]] = classify) -> dict:
    tp = fp = fn = tn = 0
    misses, false_alarms = [], []
    for r in rows:
        reason = classify_fn(r["text"])
        pred, truth = reason is not None, bool(r["label"])
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
            false_alarms.append({**r, "reason": reason})
        elif not pred and truth:
            fn += 1
            misses.append(r)
        else:
            tn += 1
    precision, recall, f1 = _rates(tp, fp, fn)
    return {"n": len(rows), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1,
            "misses": misses, "false_alarms": false_alarms}


def replay_turns(raw_path: str, classify_fn: Callable[[str], Optional[str]] = classify) -> list:
    """The turns one recording closes into, as the live app closes them (turn mode). Early asks
    are left out: they never decide what is answered."""
    from .pipeline import Pipeline
    from .stt.fake import FakeSTT

    async def run() -> list:
        pipe = Pipeline(classify=classify_fn, turns=True, speculate=False)
        turns = []
        async for ev in FakeSTT(raw_path).events():
            turns.extend(u for u in pipe.feed(ev) if not u.speculative)
        return turns

    return asyncio.run(run())


def score_turns(rows: list[dict], turns_by_file: dict[str, list]) -> dict:
    """Per-turn precision and recall. `turns_by_file` maps a label row's `file` to the closed turns
    of that recording (Utterances with audio_start and audio_end)."""
    tp = fp = fn = tn = 0
    misses, false_alarms, unplaced = [], [], []
    for file, turns in turns_by_file.items():
        held: list[list[dict]] = [[] for _ in turns]
        for r in (r for r in rows if r["file"] == file):
            mid = (r["start"] + r["end"]) / 2
            i = next((k for k, u in enumerate(turns) if u.audio_start is not None and u.audio_end is not None
                      and u.audio_start - PLACE_TOLERANCE_S <= mid <= u.audio_end + PLACE_TOLERANCE_S), None)
            if i is None:
                unplaced.append(r)          # a turn the recording never closed
            else:
                held[i].append(r)
        for u, pieces in zip(turns, held):
            if not pieces:
                continue
            pred, questions = u.trigger in ANSWERED, [p["text"] for p in pieces if p["label"]]
            entry = {"file": file, "trigger": u.trigger, "text": u.text, "questions": questions}
            if pred and questions:
                tp += 1
            elif pred:
                fp += 1
                false_alarms.append(entry)
            elif questions:
                fn += 1
                misses.append(entry)
            else:
                tn += 1
    precision, recall, f1 = _rates(tp, fp, fn)
    return {"turns": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1, "misses": misses,
            "false_alarms": false_alarms, "unplaced": unplaced}


def _pct(x) -> str:
    return "  n/a" if x is None else f"{100 * x:5.1f}%"


def print_report(s: dict, quiet: bool = False) -> None:
    print(f"{s['n']} labeled sentences: {s['tp'] + s['fn']} questions, {s['fp'] + s['tn']} non-questions")
    print(f"  precision {_pct(s['precision'])}   recall {_pct(s['recall'])}   F1 {_pct(s['f1'])}"
          f"   (tp={s['tp']} fp={s['fp']} fn={s['fn']} tn={s['tn']})")
    if quiet:
        return
    if s["misses"]:
        print(f"  MISSED questions ({len(s['misses'])}):")
        for r in s["misses"]:
            print(f"    {r['file']:<24} spk{r['speaker']}  {r['text'][:100]}")
    if s["false_alarms"]:
        print(f"  FALSE ALARMS ({len(s['false_alarms'])}):")
        for r in s["false_alarms"]:
            print(f"    {r['file']:<24} spk{r['speaker']}  [{r['reason']}] {r['text'][:100]}")


def print_turn_report(s: dict, quiet: bool = False) -> None:
    print(f"{s['turns']} turns: {s['tp'] + s['fn']} held a question, {s['fp'] + s['tn']} did not")
    print(f"  precision {_pct(s['precision'])}   recall {_pct(s['recall'])}   F1 {_pct(s['f1'])}"
          f"   (tp={s['tp']} fp={s['fp']} fn={s['fn']} tn={s['tn']})")
    if s["unplaced"]:
        print(f"  {len(s['unplaced'])} labeled pieces are in no closed turn (the recording ended mid-turn)")
    if quiet:
        return
    if s["misses"]:
        print(f"  UNANSWERED question turns ({len(s['misses'])}):")
        for e in s["misses"]:
            print(f"    {e['file']:<24} [{e['trigger']}] {e['text'][:100]}")
            for q in e["questions"]:
                print(f"      question: {q[:90]}")
    if s["false_alarms"]:
        print(f"  ANSWERED turns with no question ({len(s['false_alarms'])}):")
        for e in s["false_alarms"]:
            print(f"    {e['file']:<24} [{e['trigger']}] {e['text'][:100]}")


def expand(patterns) -> list[str]:
    paths = []
    for p in patterns:
        hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        paths.extend(hits)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", nargs="+", help="labels JSONL file(s), wildcards ok")
    ap.add_argument("--gate", type=float, default=None, help="exit 1 if precision or recall is below this")
    ap.add_argument("--quiet", action="store_true", help="numbers only")
    ap.add_argument("--turns", action="store_true", help="score whole turns, as the app answers them")
    ap.add_argument("--raw-dir", default=".", help="with --turns: the folder holding the raw logs")
    a = ap.parse_args(argv)
    paths = expand(a.labels)
    if not paths:
        print("no label files matched", a.labels)
        return 2
    rows = load_rows(paths)
    if a.turns:
        files = sorted({r["file"] for r in rows})
        missing = [f for f in files if not os.path.isfile(os.path.join(a.raw_dir, f))]
        if missing:
            print("raw logs not found in", a.raw_dir, ":", ", ".join(missing))
            return 2
        s = score_turns(rows, {f: replay_turns(os.path.join(a.raw_dir, f)) for f in files})
        print_turn_report(s, a.quiet)
    else:
        s = score(rows)
        print_report(s, a.quiet)
    if a.gate is not None:
        below = [m for m in ("precision", "recall") if s[m] is None or s[m] < a.gate]
        if below:
            print(f"  GATE FAILED: {', '.join(below)} below {a.gate:.0%}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
