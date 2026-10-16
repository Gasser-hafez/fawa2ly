"""
After the call, part 1: what happened on the call, and a
transcript kept only when the user asked for one.

CallRecord watches the events the engine already sends a front end, so it sees what the overlay
saw: each question as it was announced, each line the moment it appeared, and the final record of
every answer. When the call ends it gives the summary the overlay shows: how long the call was,
the questions heard, the lines shown for each, and how long each took from the end of the question
to its first line on screen. With transcript saving on, the same summary and what both sides said
go into one JSON file (save_transcript). Audio is never kept (non-goals); roadmap item 7 reads
these files.

Time to the first line is the pipeline's own measure of the wait before it fired (the second of
silence that closes a turn) plus the wall time from the question appearing to its first line. A
question asked again (Retry after a failure, or a question that turned out longer) keeps the time
it was first asked: the user waited through the failure too.

On the Dynamic profile (roadmap item 27) each question also keeps the shape the model chose for it,
so a saved call says what it was actually handed - which is the only way to judge, after a real call
rather than an eval, whether that judgement was any good.

"""

from __future__ import annotations

import datetime
import json
import os
import statistics
import time
from typing import Callable, Optional

from .assist import QUESTION_TRIGGERS

TRANSCRIPT_VERSION = 1


class CallRecord:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.started: Optional[float] = None
        self.meta: dict = {}
        self.questions: dict[int, dict] = {}
        self.turns: list[dict] = []          # what both sides said, in order, for the transcript
        self.pace: dict = {}                 # the user's last pace event (roadmap item 8)

    def observe(self, event: dict) -> None:
        kind = event.get("type")
        now = self.clock()
        if kind == "status":
            if event.get("state") == "listening" and self.started is None:
                self.started = now
            for key in ("provider", "mode", "style"):
                if event.get(key):
                    self.meta[key] = event[key]
        elif kind == "question":
            if event.get("action"):
                return                                # an on-demand card (item 17), not a question heard
            n = event.get("n")
            q = self.questions.get(n)
            if q is None:
                q = self.questions[n] = {"n": n, "question": "", "lines": [], "error": None,
                                         "t_asked": now, "t_first_line": None, "end_to_fire_ms": None,
                                         "feedback": None, "style": ""}
            else:
                q["lines"], q["error"] = [], None     # asked again: the card on screen starts over
            q["question"] = event.get("text") or q["question"]
        elif kind == "line":
            q = self.questions.get(event.get("n"))
            if q is not None:
                q["lines"].append(event.get("text", ""))
                if q["t_first_line"] is None:
                    q["t_first_line"] = now
        elif kind == "assist":
            a = event.get("assist") or {}
            q = self.questions.get(a.get("n"))
            if q is None or a.get("more"):
                return                                # More's lines already arrived as line events
            q["question"] = a.get("question") or q["question"]
            if a.get("bullets"):
                q["lines"], q["error"] = list(a["bullets"]), None
            else:
                q["error"] = a.get("error") or "no usable lines"
            if a.get("question_end_to_fire_ms") is not None:
                q["end_to_fire_ms"] = a["question_end_to_fire_ms"]
            if a.get("style"):
                q["style"] = a["style"]               # the shape Dynamic chose (roadmap item 27)
        elif kind == "feedback":
            q = self.questions.get(event.get("n"))
            if q is not None:                         # live feedback on the user's answer (item 9)
                q["feedback"] = {"verdict": event.get("verdict"), "note": event.get("note", "")}
        elif kind == "pace":
            self.pace = {k: event.get(k) for k in ("average_wpm", "fillers", "words")}
        elif kind == "utterance":
            u = event.get("utterance") or {}
            text = (u.get("text") or "").strip()
            if not text or u.get("speculative"):
                return
            other = u.get("stream") != "user"
            self.turns.append({"t": round(now - (self.started if self.started is not None else now), 1),
                               "who": "them" if other else "you", "text": text,
                               "question": other and u.get("trigger") in QUESTION_TRIGGERS})

    def summary(self, role: str = "", lang: str = "") -> dict:
        ended = self.clock()
        start = self.started if self.started is not None else ended
        questions = []
        for n in sorted(self.questions):
            q = self.questions[n]
            first = None
            if q["t_first_line"] is not None:
                first = round((q["t_first_line"] - q["t_asked"]) * 1000) + (q["end_to_fire_ms"] or 0)
            questions.append({"n": n, "question": q["question"], "lines": list(q["lines"]),
                              "first_line_ms": first, "error": None if q["lines"] else q["error"],
                              "feedback": q["feedback"], "style": q["style"]})
        times = [q["first_line_ms"] for q in questions if q["first_line_ms"] is not None]
        return {
            "started": datetime.datetime.fromtimestamp(start).isoformat(timespec="seconds"),
            "duration_s": round(ended - start),
            "mode": self.meta.get("mode", ""), "style": self.meta.get("style", ""),
            "provider": self.meta.get("provider", ""), "role": role, "lang": lang,
            "questions": questions,
            "heard": len(questions),
            "answered": sum(1 for q in questions if q["lines"]),
            "has_you": any(t["who"] == "you" for t in self.turns),   # item 7 can only review these
            "you_wpm": self.pace.get("average_wpm"),                 # item 8: the whole call's pace
            "you_fillers": self.pace.get("fillers"),                 # None when not counted (not English)
            "median_first_line_ms": round(statistics.median(times)) if times else None,
            "slowest_first_line_ms": max(times) if times else None,
        }


def save_transcript(summary: dict, turns: list[dict], folder: str,
                    now: Optional[datetime.datetime] = None) -> str:
    """One call as <folder>/<date>_<time>.json, never over a file an earlier call wrote."""
    os.makedirs(folder, exist_ok=True)
    stem = (now or datetime.datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    path, k = os.path.join(folder, f"{stem}.json"), 2
    while os.path.exists(path):
        path, k = os.path.join(folder, f"{stem}-{k}.json"), k + 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"version": TRANSCRIPT_VERSION, "summary": summary, "transcript": turns},
                  f, ensure_ascii=False, indent=1)
    return path
