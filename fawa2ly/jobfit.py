"""
Job description gap analysis, 2026-09-12.

Before an interview the user pastes the job description. The saved model reads it against the CV
already loaded and answers three questions: what the job asks for that the CV supports (with the
line of the CV that supports it), what it asks for that the CV does not show, and what to have
ready to say. One request, before the call, kept in the app folder so reading it again is free.

The description is then an interview document like the CV, so the same text is in the prompt while
the call runs and the model knows what the interview is for.

Three things this module is careful about, each of which would be a quiet wrong answer otherwise:

- **The job description is not the candidate's background.** Stored as a document it would sit under
  "Their background, use only what is here for specifics" with the CV, and a model reading a
  requirement there can start answering as if the candidate had done it. The document keeps a fixed
  name (`JOB_DESCRIPTION_NAME`, defined with the prompt in context/builder.py) and the interview
  prompt gains one line saying which document it is and what it is for. A call with no job
  description sends the rated interview prompt character for character.
- **The comparison never reads the job description as the CV.** Once stored, the description is one
  of the interview documents, so the second analysis would otherwise compare the description with
  itself and report a perfect fit. `cv_documents()` leaves it out.
- **A match has to be evidence.** The model is asked for the words of the CV that support each
  match, and a match without them is dropped here, because the whole use of this is knowing which
  claims the user can actually make.
"""

from __future__ import annotations

import datetime
import json
import os

from . import settings as user_settings
from .context.builder import JOB_DESCRIPTION_NAME
from .llm import collect
from .llm.base import SuggestionRequest

MIN_DESCRIPTION_WORDS = 25       # shorter than this is a job title and a wish, not a description
MAX_DESCRIPTION_CHARS = 24000
GAP_MAX_TOKENS = 2000
MAX_TEXT_CHARS = 240
MAX_ITEMS = 8

GAP_SYSTEM = """You compare a job description with a candidate's CV, before their interview, so they know what to say and what to prepare. Reply with one JSON object and nothing else:
{{"summary": "...", "matches": [{{"asks": "...", "evidence": "..."}}], "missing": ["..."], "prepare": ["..."]}}
- summary: one or two sentences on how well this CV fits this job.
- matches: what the job asks for that the CV supports. asks: the requirement, in the job description's own terms. evidence: what in the CV supports it, close to the CV's own words. Only what the CV actually says: no match without evidence in it.
- missing: what the job asks for that the CV does not show. Say the requirement, not advice.
- prepare: what the candidate should have ready to say at this interview: the experience to lead with, an example to have in mind, how to handle each missing requirement honestly.
At most {items} entries in each list, every text at most 35 words, in {language}. Never state experience, a tool, an employer or a number the CV does not contain: a requirement the CV is silent on is missing, not a match."""


def _language(lang: str) -> str:
    return ("Arabic, in the dialect of the CV" if (lang or "").startswith("ar")
            else "the language of the job description")


def _short(value, limit: int = MAX_TEXT_CHARS) -> str:
    return str(value or "").strip()[:limit]


def path() -> str:
    """Where the last analysis is kept: one file next to the settings, on this machine."""
    return os.path.join(user_settings.app_dir(), "jobfit.json")


def cv_documents() -> list[tuple[str, str]]:
    """The interview documents to compare against: everything except the job description itself."""
    return [(name, text) for name, text in user_settings.load_documents("interview")
            if name != JOB_DESCRIPTION_NAME]


def _cv_text(documents: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"From {name or 'a document'}:\n{text}" for name, text in documents)


def parse(text: str) -> dict:
    """The model's reply, checked before it is shown or saved."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the model did not return a comparison; try again")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise ValueError("the model's reply was not valid JSON; try again")
    if not isinstance(data, dict):
        data = {}

    def items(key):
        value = data.get(key)
        return [_short(x) for x in value if _short(x)][:MAX_ITEMS] if isinstance(value, list) else []

    matches = []
    for m in data.get("matches") if isinstance(data.get("matches"), list) else []:
        if not isinstance(m, dict):
            continue
        asks, evidence = _short(m.get("asks")), _short(m.get("evidence"))
        if asks and evidence:            # a match the model could not point at in the CV is not one
            matches.append({"asks": asks, "evidence": evidence})
    result = {"summary": _short(data.get("summary"), 600), "matches": matches[:MAX_ITEMS],
              "missing": items("missing"), "prepare": items("prepare")}
    if not any(result.values()):
        raise ValueError("the model's comparison was empty; try again")
    return result


def check(description: str) -> tuple[str, list[tuple[str, str]]]:
    """(the description, the documents to compare it with), or ValueError. Everything that can be
    refused without a request, so a caller can refuse before it even reaches for a model."""
    description = (description or "").strip()[:MAX_DESCRIPTION_CHARS]
    if len(description.split()) < MIN_DESCRIPTION_WORDS:
        raise ValueError(f"paste the job description itself: at least {MIN_DESCRIPTION_WORDS} words")
    documents = cv_documents()
    if not documents:
        raise ValueError("load your CV first, under Documents: there is nothing to compare the job "
                         "description with")
    # The description is stored as an interview document at the end, and that store is refused when
    # it would take them past the word budget. Asking here as well, before the request, is the
    # difference between being told now and being told after paying for a comparison that is then
    # thrown away: add_document is what actually enforces it, this only declines to waste the call.
    limit = user_settings.document_words_limit("interview")
    others = sum(len(text.split()) for name, text in documents)
    words = len(description.split())
    if words + others > limit:
        raise ValueError(f"this job description is {words} words, which would bring your interview "
                         f"documents to {words + others}; the limit is {limit}. Remove a document "
                         "under Documents, or paste a shorter description.")
    return description, documents


async def analyze(description: str, provider, lang: str = "") -> dict:
    """Compare a pasted job description with the CV, store it as an interview document, and keep the
    result. Raises ValueError with a message for the user when it cannot."""
    description, documents = check(description)
    req = SuggestionRequest(question="", max_tokens=GAP_MAX_TOKENS,
                            system=GAP_SYSTEM.format(items=MAX_ITEMS, language=_language(lang)),
                            user=f"The job description:\n{description}\n\nThe candidate's CV:\n"
                                 + _cv_text(documents))
    s = await collect(provider, req)
    if s.error:
        raise ValueError(f"the model could not compare them: {s.error[:160]}")
    result = parse(s.text)
    # Stored only once the comparison worked, so a failed request leaves no document behind. This
    # can still be refused for the word budget, which is a message the user needs either way.
    user_settings.add_document("interview", JOB_DESCRIPTION_NAME, description)
    result.update(model=f"{s.provider}/{s.model}" if s.provider else s.model,
                  written=datetime.datetime.now().isoformat(timespec="seconds"),
                  words=len(description.split()), cv=[name for name, _ in documents])
    save(result)
    return {**result, "stored": True}


def save(result: dict) -> None:
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path())


def stored() -> bool:
    """Whether the job description is still one of the interview documents. It can be removed on its
    own from the documents list, and then a kept comparison is a record of something the next call
    will not see - which the window has to say rather than repeat "used in the next interview"."""
    return any(name == JOB_DESCRIPTION_NAME for name, _ in user_settings.load_documents("interview"))


def load() -> dict | None:
    """The last comparison with `stored` on it, or None. A file that cannot be read is treated as
    none: it is a convenience, never a reason to fail opening Settings."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return {**data, "stored": stored()} if isinstance(data, dict) else None


def clear() -> None:
    """Forget the comparison and the stored job description, so the next interview starts clean."""
    try:
        os.remove(path())
    except OSError:
        pass
    user_settings.remove_document("interview", JOB_DESCRIPTION_NAME)
