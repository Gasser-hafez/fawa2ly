"""
Relevance eval: is a prompt change actually better?

    python -m fawa2ly.relevance run v1.3 --vendor gemini --cv cv.txt   # ask, save answers
    python -m fawa2ly.relevance rate v1.3                                 # score each 1-5
    python -m fawa2ly.relevance report v1.3                               # mean, per preset
    python -m fawa2ly.relevance report v1.3 v1.4 --gate 4.0               # compare, and gate

Latency has its own tools (`fawa2ly.bench`, `question_end_to_first_token_ms`). This one asks the
other question: given the question that was asked and this person's background, were the bullets
worth showing? A prompt change that shaves a word off every bullet and loses the specifics is a
regression, and only a rating catches that.

Answers and ratings live in `docs/eval/relevance/<tag>.jsonl`, one row per question, committed.
Ratings are keyed by question text, so re-running a tag keeps the scores you already gave and
only asks about answers that changed.

Rating scale, deliberately about usefulness and not about style:
  5  I could start answering from these alone
  4  useful, one or two bullets wasted
  3  half of it is generic filler
  2  mostly generic, barely touches my background
  1  wrong, invented, or nothing usable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from typing import Callable, Optional

import yaml

from .console import progress, safe_console
from .context import ContextBuilder, Preset, filter_bullets, load_cv
from .context.guardrail import style_choice
from .llm import FakeLLM, OpenAICompatibleLLM, collect, mute_asyncgen_close_noise
from .llm.prompts import DYNAMIC, STYLES

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass

QUESTIONS = os.path.join("docs", "eval", "questions.yaml")
RESULT_DIR = os.path.join("docs", "eval", "relevance")
SCALE = {1: "wrong or nothing usable", 2: "mostly generic", 3: "half filler",
         4: "useful, some waste", 5: "could answer from these alone"}


def load_questions(path: str = QUESTIONS) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out = []
    for preset, items in data.items():
        for item in items or []:
            out.append({"preset": preset, "question": item["text"],
                        "source": item.get("source", "")})
    if not out:
        raise ValueError(f"{path}: no questions")
    return out


def result_path(tag: str) -> str:
    return os.path.join(RESULT_DIR, f"{tag}.jsonl")


def load_rows(tag: str) -> list[dict]:
    path = result_path(tag)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_rows(tag: str, rows: list[dict]) -> None:
    os.makedirs(RESULT_DIR, exist_ok=True)
    with open(result_path(tag), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


async def run_tag(tag: str, provider, cv_text: str, questions: list[dict],
                  interval: float = 0.0, show: Callable = progress,
                  style: str = "full") -> list[dict]:
    """Ask every question. Keeps an existing rating when the bullets come back identical. Every
    preset is answered from the CV.

    On the Dynamic profile (roadmap item 27) each answer also carries the shape the model chose for
    it, and the report counts them: a run that never leaves "full", or one that reaches for cues on
    questions the CV says nothing about, is a prompt problem the ratings alone would not name."""
    previous = {r["question"]: r for r in load_rows(tag)}
    rows = []
    for i, q in enumerate(questions, 1):
        if i > 1 and interval:
            await asyncio.sleep(interval)
        preset = Preset.find(q["preset"])
        builder = ContextBuilder(preset=preset, cv_text=cv_text, style=style)
        s = await collect(provider, builder.build(q["question"]))
        shape = next((c for c in map(style_choice, s.bullets) if c), None) if style == DYNAMIC else None
        kept, dropped = filter_bullets(s.bullets, style=shape or style)
        old = previous.get(q["question"])
        keep_rating = old is not None and old.get("bullets") == kept
        rows.append({**q, "bullets": kept, "style": style, "shape": shape,
                     "documents": ["cv"] if cv_text else [],
                     "dropped": [{"text": d.text, "reason": d.reason} for d in dropped],
                     "model": s.model, "provider": s.provider, "ttft_ms": s.ttft_ms,
                     "error": s.error, "truncated": s.truncated,
                     "rating": old.get("rating") if keep_rating else None,
                     "note": old.get("note", "") if keep_rating else ""})
        # save after every answer, not at the end: a free-tier run of 30 questions takes over ten
        # minutes, and losing all of it to one interruption is the difference between a tool you
        # use and one you avoid
        save_rows(tag, rows)
        show(f"[{i}/{len(questions)}] {q['preset']:<14} {len(kept)} bullet(s)"
             + (f"  {shape or 'UNPLACED'}" if style == DYNAMIC else "")
             + (f"  ERROR {s.error[:60]}" if s.error else ""))
    return rows


def rate(tag: str, ask: Callable[[str], str] = input, show: Callable = progress) -> int:
    """Score the unrated rows. Each answer is saved as it is given, so quitting loses nothing."""
    rows = load_rows(tag)
    if not rows:
        show(f"nothing to rate: {result_path(tag)} does not exist, run first")
        return 0
    todo = [r for r in rows if r.get("rating") is None]
    show(f"{len(rows)} answers, {len(rows) - len(todo)} already rated, {len(todo)} to go")
    show("  " + " | ".join(f"{k}={v}" for k, v in SCALE.items()) + " | s=skip | q=quit")
    rated = 0
    for r in todo:
        show(f"\n[{r['preset']}] {r['question']}")
        for b in r["bullets"]:
            show(f"     - {b}")
        if not r["bullets"]:
            show(f"     (nothing usable; error: {r.get('error')})")
        while True:
            a = ask("   1-5 / s / q: ").strip().lower()
            if a in {"1", "2", "3", "4", "5", "s", "q"}:
                break
        if a == "q":
            break
        if a == "s":
            continue
        r["rating"] = int(a)
        rated += 1
        save_rows(tag, rows)
    show(f"saved {rated} rating(s)")
    return rated


def summarise(rows: list[dict]) -> dict:
    rated = [r for r in rows if r.get("rating") is not None]
    by_preset: dict[str, list[int]] = {}
    for r in rated:
        by_preset.setdefault(r["preset"], []).append(r["rating"])
    shapes: dict[str, int] = {}
    for r in rows:
        if r.get("style") == DYNAMIC:
            shapes[r.get("shape") or "unplaced"] = shapes.get(r.get("shape") or "unplaced", 0) + 1
    return {
        "shapes": shapes,
        "n": len(rows), "rated": len(rated),
        "mean": statistics.mean(r["rating"] for r in rated) if rated else None,
        "by_preset": {p: statistics.mean(v) for p, v in sorted(by_preset.items())},
        "empty": sum(1 for r in rows if not r["bullets"]),
        "errors": sum(1 for r in rows if r.get("error")),
        "weak": sorted((r for r in rated if r["rating"] <= 2), key=lambda r: r["rating"]),
    }


def print_report(tag: str, s: dict, show: Callable = progress) -> None:
    mean = "n/a" if s["mean"] is None else f"{s['mean']:.2f}"
    show(f"== {tag}: {s['rated']}/{s['n']} rated, mean {mean}, "
         f"{s['empty']} empty, {s['errors']} error(s)")
    if s.get("shapes"):
        chosen = ", ".join(f"{n} {name}" for name, n in sorted(s["shapes"].items()))
        show(f"   shapes chosen: {chosen}")
    for preset, m in s["by_preset"].items():
        show(f"   {preset:<16} {m:.2f}")
    for r in s["weak"][:5]:
        show(f"   weak ({r['rating']}) [{r['preset']}] {r['question'][:70]}")


async def _run(a) -> int:
    safe_console()
    mute_asyncgen_close_noise()
    questions = load_questions(a.questions)
    if a.only:
        questions = [q for q in questions if q["preset"] in a.only]
    provider = (FakeLLM(first_token_delay=0.02) if a.fake
                else OpenAICompatibleLLM.from_env(a.vendor, a.model))
    try:
        rows = await run_tag(a.tag, provider, load_cv(a.cv) if a.cv else "", questions,
                             a.interval, style=a.style)
    finally:
        await provider.close()
    print(f"[relevance] {len(rows)} answers -> {result_path(a.tag)}")
    print(f"next: python -m fawa2ly.relevance rate {a.tag}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="ask every question and save the answers")
    r.add_argument("tag", help="a name for this prompt version, e.g. v1.3")
    r.add_argument("--vendor", default="gemini")
    r.add_argument("--model", default=None)
    r.add_argument("--cv", default="cv.txt")
    r.add_argument("--questions", default=QUESTIONS)
    r.add_argument("--interval", type=float, default=0.0, help="seconds between questions (rate limits)")
    r.add_argument("--fake", action="store_true")
    r.add_argument("--only", nargs="+", default=None, metavar="PRESET", help="ask only these presets' questions")
    r.add_argument("--style", default="full", choices=list(STYLES),
                   help="answer profile to rate; 'dynamic' also reports which shape each answer chose")

    t = sub.add_parser("rate", help="score the saved answers 1-5")
    t.add_argument("tag")

    p = sub.add_parser("report", help="mean and per-preset scores; several tags compare")
    p.add_argument("tags", nargs="+")
    p.add_argument("--gate", type=float, default=None, help="exit 1 if the last tag is below this")

    a = ap.parse_args(argv)
    if a.cmd == "run":
        return asyncio.run(_run(a))
    if a.cmd == "rate":
        try:
            rate(a.tag)
        except (KeyboardInterrupt, EOFError):
            print()
        return 0
    last = None
    for tag in a.tags:
        rows = load_rows(tag)
        if not rows:
            print(f"== {tag}: no results at {result_path(tag)}")
            continue
        last = summarise(rows)
        print_report(tag, last)
    if a.gate is not None and last is not None:
        if last["mean"] is None or last["mean"] < a.gate:
            print(f"   GATE FAILED: mean below {a.gate}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
