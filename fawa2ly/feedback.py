"""
Live feedback: after each of the user's answers, whether
it was on point, missed the point, ran long or drifted.

The Coach watches the events the engine already sends a front end (question, line, assist,
utterance), so it knows the question on screen, the lines shown for it, and what the user then said
on their own microphone. An answer is over when the other side speaks again: a new question, or
anything of CLOSE_WORDS words or more ("Okay, great." is the interviewer listening, not the end of
the answer). The interviewer adding to the question before the user has said anything leaves it
open. An answer of MIN_ANSWER_WORDS words or more gets one request, with the question, the lines and
what the user said, and one verdict and a short note come back for that question's card.

Answers come first. A feedback request waits while any answer is being written, and a call has at
most FEEDBACK_LIMIT of them, so turning this on can neither delay the lines nor spend without bound.
It is off unless the user turns it on (Settings > Call and answers). The last answer of a call is
not judged live, since nobody speaks after it; the review after the call (item 7) covers it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

from .assist import QUESTION_TRIGGERS
from .llm import collect
from .llm.base import SuggestionRequest

VERDICTS = ("on_point", "missed_point", "ran_long", "drifted")
MIN_ANSWER_WORDS = 12     # "Yes, I have." is not an answer to judge
CLOSE_WORDS = 5           # the other side saying this much has taken the turn back
FEEDBACK_LIMIT = 20       # requests per call
BUSY_WAIT_S = 30          # how long a feedback request waits for answers to finish
FEEDBACK_MAX_TOKENS = 200
MAX_NOTE_CHARS = 160
_KIND = {"interview": "job interview", "meeting": "work meeting"}

FEEDBACK_SYSTEM = """You coach someone live during a {kind}. They were asked a question, an assistant showed them lines they could say, and then they answered in their own words. Compare what they said with the question. Reply with one JSON object and nothing else:
{{"verdict": "on_point", "note": "..."}}
- verdict: "on_point" (answered what was asked), "missed_point" (did not answer what was asked), "ran_long" (answered it, but far longer than the question needed), or "drifted" (started on it, then wandered to something else).
- note: at most 12 words, in {language}: for on_point what worked, otherwise the one thing to do in the next answer.
Judge only what they said: the lines shown are suggestions, not their words."""


def _language(lang: str) -> str:
    return "Arabic, in the dialect they spoke" if (lang or "").startswith("ar") else "the language they spoke"


def parse_feedback(text: str) -> Optional[dict]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    verdict = str(data.get("verdict") or "").strip().lower() if isinstance(data, dict) else ""
    if verdict not in VERDICTS:
        return None
    return {"verdict": verdict, "note": str(data.get("note") or "").strip()[:MAX_NOTE_CHARS]}


class Coach:
    def __init__(self, provider: Callable[[], object], spawn: Callable, emit: Callable[[dict], None],
                 busy: Callable[[], bool], mode: str = "interview", lang: str = "en",
                 limit: int = FEEDBACK_LIMIT, poll_s: float = 0.25):
        self.provider = provider          # looked up per request: the call may switch models
        self._spawn = spawn
        self.emit = emit
        self.busy = busy
        self.mode, self.lang, self.limit, self.poll_s = mode, lang, limit, poll_s
        self.current: Optional[dict] = None
        self.judged: set = set()
        self.requests = 0
        self.tasks: set = set()

    def observe(self, event: dict) -> None:
        kind = event.get("type")
        cur = self.current
        if kind == "question":
            if event.get("action"):
                return                                     # an on-demand card (item 17): nobody asked it
            n = event.get("n")
            if cur is not None and cur["n"] == n:          # a retry, or the question grew: same one
                cur["question"] = event.get("text") or cur["question"]
                return
            self._close()
            self.current = {"n": n, "question": event.get("text") or "", "lines": [], "said": [],
                            "t0": None, "t1": None}
        elif kind == "line" and cur is not None and event.get("n") == cur["n"]:
            cur["lines"].append(event.get("text", ""))
        elif kind == "assist" and cur is not None:
            a = event.get("assist") or {}
            if a.get("n") == cur["n"] and not a.get("more") and a.get("bullets"):
                cur["lines"] = list(a["bullets"])
        elif kind == "utterance" and cur is not None:
            u = event.get("utterance") or {}
            text = (u.get("text") or "").strip()
            if not text or u.get("speculative"):
                return
            if u.get("stream") == "user":
                cur["said"].append(text)
                cur["t0"] = cur["t0"] if cur["t0"] is not None else u.get("t_first_final")
                cur["t1"] = u.get("t_last_final")
            elif len(text.split()) >= CLOSE_WORDS or u.get("trigger") in QUESTION_TRIGGERS:
                self._close()

    def _close(self) -> None:
        cur = self.current
        if cur is None or not cur["said"]:
            return                                          # nothing said yet: still their turn to answer
        words = sum(len(t.split()) for t in cur["said"])
        if words < MIN_ANSWER_WORDS:
            return
        self.current = None
        if cur["n"] in self.judged or self.requests >= self.limit:
            return
        self.judged.add(cur["n"])
        self.requests += 1
        seconds = round(cur["t1"] - cur["t0"]) if cur["t0"] is not None and cur["t1"] is not None else None
        task = self._spawn(self._ask(cur, words, seconds))
        if task is not None:
            self.tasks.add(task)

    async def _ask(self, cur: dict, words: int, seconds: Optional[int]) -> None:
        waited = 0.0
        while self.busy() and waited < BUSY_WAIT_S:         # the lines for a question come first
            await asyncio.sleep(self.poll_s)
            waited += self.poll_s
        length = f"{words} words" + (f", {seconds} seconds" if seconds is not None else "")
        user = (f"Question: {cur['question']}\n"
                f"Lines shown: {' | '.join(cur['lines']) or '(none)'}\n"
                f"Their answer ({length}): {' '.join(cur['said'])}")
        req = SuggestionRequest(question="", max_tokens=FEEDBACK_MAX_TOKENS, user=user,
                                system=FEEDBACK_SYSTEM.format(kind=_KIND.get(self.mode, "job interview"),
                                                              language=_language(self.lang)))
        try:
            s = await collect(self.provider(), req)
        except Exception:
            return
        feedback = None if s.error else parse_feedback(s.text)
        if feedback is not None:
            self.emit({"type": "feedback", "n": cur["n"], **feedback, "words": words, "seconds": seconds})
