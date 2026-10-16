"""
After the call, part 2: the model reads a saved call and
says what landed and what was missed.

The input is a transcript saved by item 6 (fawa2ly/summary.py): what the other side said, what
the user said (only when their microphone was transcribed), the questions the app heard and the
lines it showed for each. The model is asked for one JSON object, checked here before anything is
shown or saved: a score, a verdict for each question, what a strong answer needed that the user
did not say, and a few strengths and things to do next time. The review is written into the same
file, so opening the call again shows it without another request.

A call with none of the user's words is refused rather than reviewed: there is nothing to judge,
and a model asked anyway would judge the suggested lines as if the user had said them.

Notes (roadmap item 19, 2026-09-12) come from the same file the same way: a summary, decisions,
action items with the owner named in the call, and open questions, from the conversation alone (the
suggested lines are not part of it, and the user's own words are not required). They are kept in
the file next to the review and given to the settings window as Markdown too, for pasting.

"""

from __future__ import annotations

import datetime
import json
import os
from typing import Optional

from . import settings as user_settings
from .llm import collect
from .llm.base import SuggestionRequest

MAX_CONVERSATION_WORDS = 9000     # about an hour of talk; a longer call is reviewed on its first hour
REVIEW_MAX_TOKENS = 1800
MAX_TEXT_CHARS = 240
MAX_SUMMARY_CHARS = 600
MAX_NOTE_ITEMS = 8
VERDICTS = ("landed", "partly", "missed")
_KIND = {"interview": "job interview", "meeting": "work meeting"}

REVIEW_SYSTEM = """You review a {kind} the user has just finished, to help them do better next time. You get the conversation in order ("them" is the other side, "you" is the user) and the questions an assistant heard, each with the lines it suggested. Judge only what the user actually said: the suggested lines are not the user's words. Reply with one JSON object and nothing else:
{{"score": 7, "summary": "...", "questions": [{{"n": 1, "verdict": "landed", "missing": "..."}}], "strengths": ["..."], "improve": ["..."]}}
- score: a whole number from 1 to 10 for how well the user answered overall.
- summary: one sentence on how the call went.
- questions: one entry for each question listed, by its number. verdict is "landed" (answered it well), "partly" (answered part of it, or vaguely) or "missed" (did not answer it, or answered something else). missing: what a strong answer needed that the user did not say, in one short sentence, or "" when nothing important was missing.
- strengths: up to 3 things the user did well, each tied to something they said.
- improve: up to 3 concrete things to do differently next time.
Every text is at most 25 words, in {language}. Never invent facts about the user beyond the conversation."""

NOTES_SYSTEM = """You take the notes of a {kind} that has just ended. You get the conversation in order ("them" is the other side, "you" is the user). Reply with one JSON object and nothing else:
{{"summary": "...", "decisions": ["..."], "actions": [{{"task": "...", "owner": "..."}}], "questions": ["..."]}}
- summary: two or three sentences on what the call covered.
- decisions: what was agreed or decided, one item each.
- actions: what someone said they or someone else would do. owner is who, as the conversation names them ("you", "them" or a name), or "" when nobody was named.
- questions: what was left open or still has to be answered.
Use an empty list when there is nothing of a kind. Every text is at most 25 words, in {language}. Write only what was said: never add a decision, a task, an owner or a date that is not in the conversation."""


def _language(lang: str) -> str:
    return "Arabic, in the dialect of the conversation" if (lang or "").startswith("ar") else "the language of the conversation"


def _short(value, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "").strip()[:limit]


def _has_you(data: dict) -> bool:
    return any(t.get("who") == "you" and str(t.get("text") or "").strip() for t in data.get("transcript") or [])


def _conversation(data: dict) -> tuple[str, bool]:
    """The call's turns as "who: text" lines, cut at MAX_CONVERSATION_WORDS; (text, whether cut)."""
    lines, words, cut = [], 0, False
    for t in data.get("transcript") or []:
        line = f"{t.get('who')}: {str(t.get('text') or '').strip()}"
        words += len(line.split())
        if words > MAX_CONVERSATION_WORDS:
            cut = True
            break
        lines.append(line)
    return "\n".join(lines) + ("\n(the rest of the call was cut)" if cut else ""), cut


def _system(template: str, summary: dict) -> str:
    return template.format(kind=_KIND.get(summary.get("mode") or "interview", "job interview"),
                           language=_language(summary.get("lang") or ""))


def build_request(data: dict) -> tuple[SuggestionRequest, bool]:
    """(the request, whether the conversation had to be cut to fit)."""
    summary = data.get("summary") or {}
    conversation, cut = _conversation(data)
    questions = []
    for q in summary.get("questions") or []:
        suggested = " | ".join(q.get("lines") or []) or "(none shown)"
        questions.append(f"{q.get('n')}. {q.get('question')}\n   suggested: {suggested}")
    user = "Conversation:\n" + conversation + "\n\nQuestions the assistant heard:\n" + "\n".join(questions)
    return SuggestionRequest(question="", system=_system(REVIEW_SYSTEM, summary), max_tokens=REVIEW_MAX_TOKENS,
                             user=user), cut


def _reply_object(text: str, a_noun: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"the model did not return {a_noun}; try again")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise ValueError("the model's reply was not valid JSON; try again")
    return data if isinstance(data, dict) else {}


def parse_review(text: str, numbers: list[int]) -> dict:
    data = _reply_object(text, "a review")
    try:
        score = max(1, min(10, int(round(float(data.get("score"))))))
    except (TypeError, ValueError):
        raise ValueError("the model's review had no score; try again")
    judged: dict[int, dict] = {}
    for q in data.get("questions") if isinstance(data.get("questions"), list) else []:
        if not isinstance(q, dict):
            continue
        try:
            n = int(q.get("n"))
        except (TypeError, ValueError):
            continue
        verdict = str(q.get("verdict") or "").strip().lower()
        if n in numbers and verdict in VERDICTS and n not in judged:
            judged[n] = {"n": n, "verdict": verdict, "missing": _short(q.get("missing"))}
    if not judged:
        raise ValueError("the model's review judged none of the questions; try again")

    def items(key):
        value = data.get(key)
        return [_short(x) for x in value if _short(x)][:3] if isinstance(value, list) else []

    return {"score": score, "summary": _short(data.get("summary")),
            "questions": [judged[n] for n in numbers if n in judged],
            "strengths": items("strengths"), "improve": items("improve")}


def parse_notes(text: str) -> dict:
    data = _reply_object(text, "notes")

    def items(value):
        return [_short(x) for x in value if _short(x)][:MAX_NOTE_ITEMS] if isinstance(value, list) else []

    actions = []
    for a in data.get("actions") if isinstance(data.get("actions"), list) else []:
        task = _short(a.get("task") if isinstance(a, dict) else a)
        if task:
            actions.append({"task": task, "owner": _short(a.get("owner")) if isinstance(a, dict) else ""})
    result = {"summary": _short(data.get("summary"), MAX_SUMMARY_CHARS), "decisions": items(data.get("decisions")),
              "actions": actions[:MAX_NOTE_ITEMS], "questions": items(data.get("questions"))}
    if not any(result.values()):
        raise ValueError("the model's notes were empty; try again")
    return result


def notes_markdown(summary: dict, notes: dict) -> str:
    """The notes as Markdown, for pasting into a document or a CRM."""
    out = [f"# Call notes {summary.get('started') or ''}".rstrip(), "", notes.get("summary") or ""]
    sections = (("Decisions", [f"- {d}" for d in notes.get("decisions") or []]),
                ("Action items", [f"- [ ] {a['task']}" + (f" ({a['owner']})" if a.get("owner") else "")
                                  for a in notes.get("actions") or []]),
                ("Open questions", [f"- {q}" for q in notes.get("questions") or []]))
    for title, rows in sections:
        if rows:
            out += ["", f"## {title}", *rows]
    return "\n".join(out) + "\n"


async def review(data: dict, provider) -> dict:
    """Review one saved call. Raises ValueError with a message for the user when it cannot."""
    if not _has_you(data):
        raise ValueError("this call has none of your words to review: turn on "
                         "\"Also transcribe my microphone\" before the next call")
    numbers = [int(q["n"]) for q in (data.get("summary") or {}).get("questions") or [] if "n" in q]
    if not numbers:
        raise ValueError("no questions were heard on this call, so there is nothing to review")
    req, cut = build_request(data)
    s = await collect(provider, req)
    if s.error:
        raise ValueError(f"the model could not review the call: {s.error[:160]}")
    result = parse_review(s.text, numbers)
    result.update(model=f"{s.provider}/{s.model}" if s.provider else s.model, cut=cut,
                  reviewed=datetime.datetime.now().isoformat(timespec="seconds"))
    return result


async def notes(data: dict, provider) -> dict:
    """Notes of one saved call. Raises ValueError with a message for the user when it cannot."""
    if not any(str(t.get("text") or "").strip() for t in data.get("transcript") or []):
        raise ValueError("nothing was said on this call, so there is nothing to take notes of")
    summary = data.get("summary") or {}
    conversation, cut = _conversation(data)
    s = await collect(provider, SuggestionRequest(question="", system=_system(NOTES_SYSTEM, summary),
                                                  max_tokens=REVIEW_MAX_TOKENS, user="Conversation:\n" + conversation))
    if s.error:
        raise ValueError(f"the model could not write the notes: {s.error[:160]}")
    result = parse_notes(s.text)
    result.update(model=f"{s.provider}/{s.model}" if s.provider else s.model, cut=cut,
                  written=datetime.datetime.now().isoformat(timespec="seconds"))
    return result


# ---- saved calls ----

def find_call(name) -> str:
    """The path of a saved call, by its file name. Nothing outside the transcripts folder."""
    base = os.path.basename(str(name or ""))
    path = os.path.join(user_settings.transcripts_dir(), base)
    if base != name or not base.endswith(".json") or not os.path.isfile(path):
        raise ValueError("there is no saved call by that name")
    return path


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise ValueError("that saved call could not be read")
    return data if isinstance(data, dict) else {}


def list_calls() -> list[dict]:
    """Every saved call, newest first, as a settings list shows it."""
    out = []
    for path in reversed(user_settings.list_transcripts()):
        try:
            data = _read(path)
        except ValueError:
            continue
        s = data.get("summary") or {}
        out.append({"file": os.path.basename(path), "started": s.get("started", ""),
                    "duration_s": s.get("duration_s", 0), "heard": s.get("heard", 0),
                    "answered": s.get("answered", 0), "mode": s.get("mode", ""), "role": s.get("role", ""),
                    "noted": bool(data.get("notes")),
                    "has_you": _has_you(data), "reviewed": bool(data.get("review"))})
    return out


def load_call(name) -> dict:
    data = _read(find_call(name))
    summary, written = data.get("summary") or {}, data.get("notes")
    return {"file": name, "summary": summary, "review": data.get("review"), "has_you": _has_you(data),
            "notes": written, "notes_markdown": notes_markdown(summary, written) if written else None}


async def _kept(name, key: str, make, provider, again: bool) -> dict:
    """What `make` writes for a saved call, kept in its file under `key`: the saved one unless asked again."""
    path = find_call(name)
    data = _read(path)
    if data.get(key) and not again:
        return data[key]
    result = await make(data, provider)
    data[key] = result
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return result


async def review_call(name, provider, again: bool = False) -> dict:
    """The call's review: the one saved with it, or a new one written into its file."""
    return await _kept(name, "review", review, provider, again)


async def notes_call(name, provider, again: bool = False) -> dict:
    """The call's notes (roadmap item 19): the ones saved with it, or new ones written into its file."""
    return await _kept(name, "notes", notes, provider, again)
