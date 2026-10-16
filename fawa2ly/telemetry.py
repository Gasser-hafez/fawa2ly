"""
Latency telemetry: read the per-utterance JSONL written by live_transcribe.py
(or fawa2ly.replay --out) and print count / min / p50 / p95 / max per metric
and per trigger.

Usage:
    python -m fawa2ly.telemetry interview5.jsonl [more.jsonl ...]

Metrics:
    speech_to_text_ms        flush time - audio end of the last word flushed
    question_end_to_fire_ms  flush time - audio end of the question sentence itself; None for
                             silence / speaker_change flushes and for logs written before
                             2026-09-08
    ttft_ms                  request sent -> first token of the suggestion
    question_end_to_first_token_ms
                             the product metric:
                             question_end_to_fire_ms + ttft_ms. Present in <session>.assist.jsonl,
                             which fawa2ly.assist writes alongside the utterance log.

Exit code is 1 if any row has a negative speech_to_text_ms. A negative value
means the measurement itself is wrong (Deepgram's audio clock ran ahead of our
wall clock), not that STT was fast, so it must never be read as a good result.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

METRICS = ("question_end_to_first_token_ms", "speech_to_text_ms", "question_end_to_fire_ms",
           "ttft_ms", "gap_ms", "proc_ms")
_DECIMALS = {"proc_ms": 2}   # pipeline CPU time is fractions of a millisecond


def percentile(values, p):
    """Linear-interpolation percentile, p in 0..100. None for empty input."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load_rows(path):
    """Read a JSONL file into a list of dicts, skipping blank lines."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def stats(values):
    """count / min / p50 / p95 / max over the non-None entries."""
    xs = [v for v in values if v is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "min": min(xs), "p50": percentile(xs, 50),
            "p95": percentile(xs, 95), "max": max(xs)}


def trigger_key(row) -> str:
    """Group label: trigger, suffixed with @interim for early fires."""
    trig = row.get("trigger", "?")
    return f"{trig}@interim" if row.get("source") == "interim" else trig


def summarize(rows):
    out = {"rows": len(rows), "metrics": {}, "by_trigger": {}, "negative": []}
    for m in METRICS:
        s = stats([r.get(m) for r in rows])
        s["missing"] = sum(1 for r in rows if r.get(m) is None)
        out["metrics"][m] = s
    groups = defaultdict(list)
    for r in rows:
        groups[trigger_key(r)].append(r.get("speech_to_text_ms"))
    for trig, vals in sorted(groups.items()):
        out["by_trigger"][trig] = stats(vals)
    out["negative"] = [r for r in rows
                       if r.get("speech_to_text_ms") is not None and r["speech_to_text_ms"] < 0]
    return out


def _fmt(s, decimals: int = 0):
    if s["n"] == 0:
        return "n=0"
    d = decimals
    return (f"n={s['n']:<3} min={s['min']:>6.{d}f}  p50={s['p50']:>6.{d}f}  "
            f"p95={s['p95']:>6.{d}f}  max={s['max']:>6.{d}f}")


def print_report(path, summary):
    print(f"== {path}  ({summary['rows']} utterances)")
    for m, s in summary["metrics"].items():
        miss = f"  missing={s['missing']}" if s.get("missing") else ""
        print(f"  {m:<24} {_fmt(s, _DECIMALS.get(m, 0))}{miss}")
    print("  by trigger (speech_to_text_ms):")
    for trig, s in summary["by_trigger"].items():
        print(f"    {trig:<22} {_fmt(s)}")
    neg = summary["negative"]
    if neg:
        print(f"  NEGATIVE speech_to_text_ms in {len(neg)} row(s) -> measurement bug, not fast STT:")
        for r in neg:
            print(f"    [{r.get('n')}] {r['speech_to_text_ms']} ms  {str(r.get('text', ''))[:70]}")
    else:
        print("  all speech_to_text_ms values positive")


def main(argv=None):
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print(__doc__.strip())
        return 2
    negatives = 0
    for p in paths:
        s = summarize(load_rows(p))
        print_report(p, s)
        print()
        negatives += len(s["negative"])
    return 1 if negatives else 0


if __name__ == "__main__":
    sys.exit(main())
