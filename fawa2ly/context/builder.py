"""
Context builder.

Assembles the prompt for one question:

    system  = base instruction + role preset + the speaker's background   <- stable all session
    user    = last N turns of transcript + the question                   <- changes every time

The split is the point. Everything stable sits in the system prompt so a provider's prompt
cache can match a long prefix, and only the short user message changes per question. The
OpenAI-compatible providers cache implicitly on a matching prefix, so the structure is what buys
the saving, and no provider-specific field is set here.

Background matters for more than personalisation: without it the model invents specifics
("increased team efficiency by 30%", observed 2026-09-08). With no background loaded, the
prompt says so and asks for angles to cover rather than facts.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import yaml

from ..limits import DEFAULT_MAX_TOKENS
from ..llm.base import SuggestionRequest
from ..llm.prompts import ASKED_LEAD, MODES, NEGOTIATION_RULE, STYLES, system_for


def bundled_path(*parts: str) -> str:
    """A path inside the packaged engine (PyInstaller puts data files under sys._MEIPASS) or, when
    running from source, inside the project folder."""
    root = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, *parts)


# The role presets: `presets` in the working directory when there is one (the project folder, where
# every command-line tool runs), else the copy shipped inside the engine. M3 step 5: the installed
# app starts the engine from its install folder, where a relative "presets" does not exist.
PRESET_DIR = "presets" if os.path.isdir("presets") else bundled_path("presets")
DEFAULT_HISTORY_TURNS = 6

# Every settings event lists each role's mode and title, which read all ~70 preset files twice
# (about 180 ms on the event loop, per slider step, mid-call too: audit F16). A file is parsed again
# only when its size or modification time changes, so a role added or edited is seen at once.
_YAML_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}


def read_preset_file(path: str) -> dict:
    """A preset file's YAML as a dict, cached by (mtime, size). Callers must not modify it."""
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    hit = _YAML_CACHE.get(path)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _YAML_CACHE[path] = (stamp, data)
    return data


@dataclass
class Preset:
    name: str
    mode: str = "interview"          # interview | meeting
    role: str = ""
    focus: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict, name: str = "") -> "Preset":
        return cls(name=d.get("name") or name, mode=d.get("mode", "interview"),
                   role=d.get("role", ""), focus=list(d.get("focus") or []),
                   avoid=list(d.get("avoid") or []))

    @classmethod
    def load(cls, path: str) -> "Preset":
        data = read_preset_file(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        return cls.from_dict(data, name=stem)

    @classmethod
    def find(cls, name: str, preset_dir: str = PRESET_DIR) -> "Preset":
        if os.path.isfile(name):
            return cls.load(name)
        for folder in preset_dirs(preset_dir):
            path = os.path.join(folder, f"{name}.yaml")
            if os.path.isfile(path):
                return cls.load(path)
        raise FileNotFoundError(f"no preset {name!r}; have: {', '.join(available(preset_dir)) or 'none'}")

    def as_prompt(self) -> str:
        parts = []
        if self.role:
            parts.append(f"Who is speaking: {self.role}.")
        if self.focus:
            parts.append("Steer the bullets toward:\n" + "\n".join(f"- {x}" for x in self.focus))
        if self.avoid:
            parts.append("Do not suggest:\n" + "\n".join(f"- {x}" for x in self.avoid))
        return "\n\n".join(parts)


def user_preset_dir() -> str:
    """Roles the user described and the model wrote up (2026-09-11), kept with their settings."""
    from .. import settings as user_settings
    return os.path.join(user_settings.app_dir(), "presets")


def preset_dirs(preset_dir: str = PRESET_DIR) -> list[str]:
    """Where roles are looked for: the shipped presets, then the user's own. A caller that names its
    own folder (the tests, a command line) gets only that folder."""
    return [preset_dir, user_preset_dir()] if preset_dir == PRESET_DIR else [preset_dir]


def available(preset_dir: str = PRESET_DIR) -> list[str]:
    names: set[str] = set()
    for folder in preset_dirs(preset_dir):
        if os.path.isdir(folder):
            names.update(os.path.splitext(f)[0] for f in os.listdir(folder) if f.endswith((".yaml", ".yml")))
    return sorted(names)


def load_cv(path: str) -> str:
    """Read a document. Plain text and Markdown always; PDF when pypdf is installed; DOCX when
    python-docx is (roadmap item 5, 2026-09-11)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError("reading PDF needs pypdf: pip install pypdf, or pass a .txt file") from e
        return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages).strip()
    if ext == ".docx":
        try:
            import docx
        except ImportError as e:
            raise RuntimeError("reading DOCX needs python-docx: pip install python-docx, "
                               "or export the file to .pdf or .txt") from e
        try:
            document = docx.Document(path)
        except Exception as e:
            raise RuntimeError(f"not a readable DOCX file ({e.__class__.__name__})") from e
        return _docx_text(document)
    if ext == ".doc":
        raise RuntimeError("the old Word .doc format cannot be read; save it as .docx or .pdf")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def _docx_text(document) -> str:
    """Paragraphs and table rows, in the order they appear. A CV written in Word is often a table
    (dates in one column, roles in the other), and reading paragraphs alone would drop all of it."""
    lines = []
    for block in document.iter_inner_content():
        if hasattr(block, "rows"):                       # a table
            for row in block.rows:
                cells: list[str] = []
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and (not cells or cells[-1] != text):   # a merged cell repeats itself
                        cells.append(text)
                if cells:
                    lines.append(" | ".join(cells))
        elif block.text.strip():
            lines.append(block.text.strip())
    return "\n".join(lines).strip()


def extract_cv(filename: str, data: bytes) -> str:
    """Text out of a background document the UI handed over as bytes.

    A webview file picker deliberately does not reveal the path, so the setup screen sends the
    file itself. Parsing it here means one reader for both front ends: whatever the CLI accepts
    from a path, the UI accepts from a picker, and a format neither can read fails the same way.
    """
    ext = os.path.splitext(filename)[1].lower() or ".txt"
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "background" + ext)
        with open(path, "wb") as f:
            f.write(data)
        return load_cv(path)


NO_BACKGROUND = ("No background document was loaded, so do not state facts about them. "
                 "Give lines about how to approach the question that they can say as they are, "
                 "leaving the specifics to them.")

# The job description an interview is for (roadmap item 23), stored as an interview document under
# this name so both sides of the app can recognise it: fawa2ly/jobfit.py leaves it out of the CV it
# compares against, and the prompt below says what it is. Without that line it sits under "Their
# background" with the CV, and a model reading a requirement there can answer as though the
# candidate had done it - the one failure this feature could introduce into a rated prompt.
JOB_DESCRIPTION_NAME = "Job description.txt"
JOB_DESCRIPTION_NOTE = ("One of the documents is the job description this interview is for, not the "
                        "speaker's own experience. Use it to know what the interviewer is hiring for "
                        "and which of the speaker's experience to lead with. Never state anything in "
                        "it as something the speaker has done: their experience is only what the "
                        "other documents say.")
DOCUMENT_LABEL = {"interview": "Their background, use only what is here for specifics:",
                  "meeting": "The meeting material, use only what is here for facts:"}
# Meeting mode (roadmap item 18): the agenda or project notes, when the user loaded any.
NO_MEETING_MATERIAL = ("No meeting material was loaded, so do not state project facts, figures, dates or "
                       "decisions that were not said in the conversation. Give lines about how to handle what "
                       "was asked that they can say as they are, leaving the facts to them.")
NO_DOCUMENT = {"interview": NO_BACKGROUND, "meeting": NO_MEETING_MATERIAL}

# A call in Arabic (lang ar or ar-EG, 2026-09-11): the lines are said in the conversation, so they
# come in its language and dialect. The rules above stay in English, which models follow as well.
ARABIC_LINES = ("The conversation is in Arabic. Write every line in Arabic, in the dialect the "
                "question was asked in (Egyptian Arabic when it was asked in Egyptian), so it can be "
                "said exactly as written. Keep the names of tools, products and companies the way "
                "people say them.")


def available_modes(preset_dir: str = PRESET_DIR) -> dict[str, str]:
    """Each preset's call mode, so a setup screen offers only the roles that fit the call. A file
    that does not load is left out here and reported when someone chooses it."""
    out = {}
    for name in available(preset_dir):
        try:
            out[name] = Preset.find(name, preset_dir).mode
        except Exception:
            continue
    return out


USER_CATEGORY = "Your roles"


def preset_info(preset_dir: str = PRESET_DIR) -> dict[str, dict]:
    """How each role is shown in a list: a title (the role line when the file has none), the section
    it is listed under (its category; the roles the user described are one section of their own),
    the role line, which the settings search also matches, and whether the user created it, so a
    window offers to delete only those. A shipped preset wins over a user file of the same name, as
    in Preset.find."""
    out: dict[str, dict] = {}
    user_dir = os.path.normcase(os.path.abspath(user_preset_dir()))
    for folder in preset_dirs(preset_dir):
        if not os.path.isdir(folder):
            continue
        custom = os.path.normcase(os.path.abspath(folder)) == user_dir
        for f in sorted(os.listdir(folder)):
            name, ext = os.path.splitext(f)
            if ext not in (".yaml", ".yml") or name in out:
                continue
            try:
                data = read_preset_file(os.path.join(folder, f))
            except (OSError, yaml.YAMLError):
                continue
            out[name] = {"title": str(data.get("title") or data.get("role") or name), "custom": custom,
                         "category": USER_CATEGORY if custom else str(data.get("category") or "General"),
                         "role": str(data.get("role") or "")}
    return out


class ContextBuilder:
    def __init__(self, preset: Optional[Preset] = None, cv_text: str = "",
                 history_turns: int = DEFAULT_HISTORY_TURNS, base: Optional[str] = None,
                 style: str = "full", mode: Optional[str] = None,
                 documents: Optional[list[tuple[str, str]]] = None, lang: str = ""):
        self.preset = preset
        self.lang = (lang or "").lower()           # the call's language, as Deepgram was asked for it
        # A call's mode comes from the setup screen. A command-line run that only names a preset
        # gets that preset's mode, so `--preset meeting-standup` is still a meeting.
        preset_mode = preset.mode if preset is not None else None
        if mode and preset_mode and mode != preset_mode:
            raise ValueError(f"the {preset.name} role is for {preset_mode} calls, and this call is {mode}; "
                             "choose a role for this kind of call, or none")
        self.mode = mode or preset_mode or "interview"
        if self.mode not in MODES:
            raise ValueError(f"unknown call mode {self.mode!r}; one of {', '.join(MODES)}")
        # Documents are (name, text) pairs (roadmap item 5). `cv_text` is one unnamed document, which
        # is what the command line and older callers pass.
        self.documents: list[tuple[str, str]] = []
        self.negotiation = False                   # this call is a pay or price talk (roadmap item 25)
        self.set_documents(([("", cv_text)] if cv_text else []) + list(documents or []))
        self.history_turns = history_turns
        self.style = style if style in STYLES else "full"
        self.base = base or system_for(self.style, self.mode)

    @property
    def cv_text(self) -> str:
        """Every document's text together; empty when there are none."""
        return "\n\n".join(text for _, text in self.documents)

    @cv_text.setter
    def cv_text(self, text: str) -> None:
        self.set_documents([("", text)] if text else [])

    @property
    def document_words(self) -> int:
        return sum(len(text.split()) for _, text in self.documents)

    @property
    def has_job_description(self) -> bool:
        """Whether one of this interview's documents is the job description (roadmap item 23)."""
        return self.mode == "interview" and any(name == JOB_DESCRIPTION_NAME for name, _ in self.documents)

    def set_documents(self, documents) -> None:
        """Replace the documents, from the next question on. Empty ones are dropped."""
        self.documents = [(name or "", text.strip()) for name, text in documents if text and text.strip()]

    def set_style(self, style: str) -> None:
        """Change the answer profile mid-call. The prefix changes, so a provider's prompt cache
        starts over for the questions after it; that is the price of switching."""
        self.base = system_for(style, self.mode)   # raises on an unknown style before anything changes
        self.style = style

    def system(self) -> str:
        """The stable prefix. Same string for every question in a session."""
        parts = [self.base]
        if self.lang.startswith("ar"):
            # An Arabic call got English lines until 2026-09-11: nothing told the model which language
            # to answer in. English calls keep their prompt unchanged.
            parts.append(ARABIC_LINES)
        if self.preset is not None:
            block = self.preset.as_prompt()
            if block:
                parts.append(block)
        if self.has_job_description:
            parts.append(JOB_DESCRIPTION_NOTE)
        if len(self.documents) == 1:
            # One document reads exactly as the single CV did before documents had names, so a
            # user with one file sends the same prompt as before (and the ratings still stand).
            parts.append(DOCUMENT_LABEL[self.mode] + "\n" + self.documents[0][1])
        elif self.documents:
            parts.append(DOCUMENT_LABEL[self.mode] + "\n" + "\n\n".join(
                f"From {name or 'a document'}:\n{text}" for name, text in self.documents))
        else:
            parts.append(NO_DOCUMENT[self.mode])
        if self.negotiation:
            # after the documents, so "the documents" in it means everything above
            parts.append(NEGOTIATION_RULE[self.mode])
        return "\n\n".join(parts)

    def build(self, question: str, history: Optional[list[tuple[str, str]]] = None,
              max_tokens: int = DEFAULT_MAX_TOKENS, turns: Optional[int] = None) -> SuggestionRequest:
        """`turns` overrides how much of the conversation comes with this one request (a recap needs
        the call, not the last few turns, roadmap item 17)."""
        turns = list(history or [])[-(self.history_turns if turns is None else turns):]
        return SuggestionRequest(question=question, system=self.system(), history=turns,
                                 max_tokens=max_tokens, lead=ASKED_LEAD)
