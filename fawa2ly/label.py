"""
Label sentences from a raw Deepgram log as question / not question.

    python -m fawa2ly.label interview6.raw.jsonl              # -> docs/eval/interview6.labels.jsonl
    python -m fawa2ly.label interview6.raw.jsonl --list       # just print the sentences, no prompts
    python -m fawa2ly.label interview6.raw.jsonl --out x.jsonl

Keys:  y = a question or request that expects an answer from the other party
       n = anything else (answers, small talk, statements, asides)
       s = skip for now (asked again next run)
       q = quit (every answer is saved immediately, so quitting loses nothing)

Sentences come from FINAL segments only, split by diarized speaker, one per
sentence-final punctuation mark. An unfinished tail of a segment is kept too
(complete=false) because the live detector sees exactly those. Labels are keyed
by (file, start, end, text), so re-running on the same log resumes where you
stopped. The labels file is the versioned eval set: commit it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable, Optional

from .pipeline import sentences_from_words, split_words_by_speaker
from .stt.deepgram import parse_message

DEFAULT_DIR = os.path.join("docs", "eval")


def extract_sentences(raw_path) -> list[dict]:
    """Every sentence of every non-empty FINAL segment in a raw log, in order."""
    name = os.path.basename(str(raw_path))
    out = []
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            ev = parse_message(rec["msg"], rec["t_recv"], rec.get("t_rel"))
            if ev is None or ev.kind != "final" or not ev.words:
                continue
            for spk, run in split_words_by_speaker(ev.words):
                for s in sentences_from_words(run):
                    out.append({"file": name, "speaker": spk, "start": round(s.start, 3),
                                "end": round(s.end, 3), "text": s.text, "complete": s.complete})
    return out


def label_key(row: dict) -> tuple:
    return (row["file"], row["start"], row["end"], row["text"])


def load_labels(path) -> dict[tuple, dict]:
    labels = {}
    if not os.path.exists(path):
        return labels
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                labels[label_key(row)] = row
    return labels


def default_out_path(raw_path) -> str:
    stem = os.path.basename(str(raw_path))
    for suffix in (".raw.jsonl", ".jsonl"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return os.path.join(DEFAULT_DIR, stem + ".labels.jsonl")


def run(raw_path, out_path, ask: Callable[[str], str] = input,
        show: Callable[[str], None] = print) -> tuple[int, int]:
    """Interactive loop. Returns (newly labeled, total sentences)."""
    sents = extract_sentences(raw_path)
    done = load_labels(out_path)
    todo = [s for s in sents if label_key(s) not in done]
    show(f"{len(sents)} sentences in {os.path.basename(str(raw_path))}, "
         f"{len(done)} already labeled, {len(todo)} to go -> {out_path}")
    if not todo:
        return 0, len(sents)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    new = 0
    with open(out_path, "a", encoding="utf-8") as out:
        for i, s in enumerate(sents):
            if label_key(s) in done:
                continue
            prev = sents[i - 1]["text"] if i > 0 else ""
            tail = "" if s["complete"] else "   (unfinished)"
            show(f"[{i + 1}/{len(sents)}] spk{s['speaker']}{tail}")
            if prev:
                show(f"      prev: {prev[:90]}")
            show(f"   >  {s['text']}")
            while True:
                a = ask("   y/n/s/q: ").strip().lower()
                if a in ("y", "n", "s", "q"):
                    break
            if a == "q":
                break
            if a == "s":
                continue
            row = {**s, "label": a == "y", "labeled_by": "human",
                   "labeled_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            done[label_key(s)] = row
            new += 1
    show(f"saved {new} new label(s); {len(done)}/{len(sents)} labeled")
    return new, len(sents)


def import_labels(raw_path, out_path, question_indices: set[int], labeled_by: str,
                  show: Callable[[str], None] = print) -> int:
    """Non-interactive: label every not-yet-labeled sentence, y for the 1-based indices given
    (as printed by --list), n for the rest. Used for model-assisted labeling; the source is
    recorded in labeled_by so human and model labels can be told apart."""
    sents = extract_sentences(raw_path)
    done = load_labels(out_path)
    bad = [i for i in question_indices if i < 1 or i > len(sents)]
    if bad:
        raise SystemExit(f"indices out of range 1..{len(sents)}: {bad}")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    new = 0
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(out_path, "a", encoding="utf-8") as out:
        for i, s in enumerate(sents, 1):
            if label_key(s) in done:
                continue
            row = {**s, "label": i in question_indices, "labeled_by": labeled_by, "labeled_at": stamp}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            new += 1
    show(f"imported {new} label(s) ({sum(1 for i in question_indices)} questions) by {labeled_by} -> {out_path}")
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", help="raw Deepgram log (<session>.raw.jsonl)")
    ap.add_argument("--out", default=None, help=f"labels file (default: {DEFAULT_DIR}/<session>.labels.jsonl)")
    ap.add_argument("--list", action="store_true", help="print the sentences and exit")
    ap.add_argument("--questions", default=None,
                    help="non-interactive: comma-separated 1-based indices (from --list) that are questions; "
                         "all other unlabeled sentences get n")
    ap.add_argument("--labeled-by", default="import", help="who produced --questions (recorded per row)")
    a = ap.parse_args(argv)
    out_path = a.out or default_out_path(a.raw)
    if a.questions is not None:
        idx = {int(x) for x in a.questions.replace(" ", "").split(",") if x}
        import_labels(a.raw, out_path, idx, a.labeled_by)
        return 0
    if a.list:
        done = load_labels(out_path)
        for i, s in enumerate(extract_sentences(a.raw)):
            row = done.get(label_key(s))
            mark = "?" if row is None else ("Q" if row["label"] else "-")
            print(f"[{i + 1:>3}] {mark} spk{s['speaker']} {s['start']:>7.2f}-{s['end']:<7.2f} {s['text']}")
        return 0
    try:
        run(a.raw, out_path)
    except (KeyboardInterrupt, EOFError):
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
