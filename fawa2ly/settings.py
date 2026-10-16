"""
What the user picked last time.

An interview is not the moment to fill in a form. The setup screen opens on the last session's
choices, so starting a call is one click for everyone after the first, and the background
document is kept as extracted text so it does not have to be found on disk again.

Everything lives in one folder, `%APPDATA%/Fawa2ly` on Windows and `~/.fawa2ly` elsewhere:

    settings.json          preset, language, provider, microphone choices, overlay preferences
    documents/<mode>/      the documents for interviews and for meetings, as text extracted
                           once, with index.json naming them (roadmap item 5; `cv.txt` from
                           before is moved in on first read)

The documents are the user's own, on their own machine, and stored in the clear, exactly as
`cv.txt` is in the project folder today. They are written where the user can find and delete
them, and `remove_document()` does that from the UI. Nothing here is ever uploaded: the text is
read by the local engine and goes only to the model in the prompt, which is the same path the
CLI has used since M2.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from .limits import MAX_DOCUMENT_WORDS
from .llm.prompts import MODES


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    name = "Fawa2ly" if os.environ.get("APPDATA") else ".fawa2ly"
    return os.path.join(base, name)


def settings_path() -> str:
    return os.path.join(app_dir(), "settings.json")


def cv_path() -> str:
    return os.path.join(app_dir(), "cv.txt")


# ---- API keys ----
# A secret never goes into the installer, so each user pastes their own keys into Settings and they
# are kept here, on their machine. The UI is only ever told whether a key is set, never its value.


def env_path() -> str:
    return os.path.join(app_dir(), ".env")


def key_names() -> tuple[str, ...]:
    """Deepgram, then each model provider's key, as the provider table names them."""
    from .llm.openai_compat import VENDORS
    names = ["DEEPGRAM_API_KEY"] + [v["key_env"] for v in VENDORS.values() if v.get("key_env")]
    return tuple(dict.fromkeys(names))


def keys_status() -> dict[str, bool]:
    """Which keys the engine has, from its environment. Never the values."""
    return {name: bool(os.environ.get(name)) for name in key_names()}


def save_keys(values) -> list[str]:
    """Store keys in env_path() and in this process's environment, so a key works from the next call
    without a restart. An empty value removes a key. Refused, with nothing written, for a name that
    is not a key this app uses or a value that is not a single line of text."""
    from dotenv import dotenv_values, set_key, unset_key
    if not isinstance(values, dict):
        raise ValueError("keys must be an object of name -> value")
    known = key_names()
    for name, value in values.items():
        if name not in known:
            raise ValueError(f"{name} is not a key this app uses")
        if value is not None and (not isinstance(value, str) or "\n" in value or "\r" in value):
            raise ValueError(f"{name} must be a single line of text")
    os.makedirs(app_dir(), exist_ok=True)
    path = env_path()
    if not os.path.exists(path):
        open(path, "a", encoding="utf-8").close()
    changed = []
    for name, value in values.items():
        value = (value or "").strip()
        if value:
            set_key(path, name, value)
            os.environ[name] = value
        else:
            if name in dotenv_values(path):
                unset_key(path, name)
            os.environ.pop(name, None)
        changed.append(name)
    return changed


# What the settings window may change. Anything else sent to it is ignored, so a window can never
# write a field the engine does not know how to read.
EDITABLE = ("preset", "lang", "vendor", "model", "stt_vendor", "whisper_model", "user_mic", "speculate",
            "style", "animate", "text_size", "opacity", "hotkeys", "theme", "mode", "speaker_device",
            "mic_device", "ui_lang", "save_transcripts", "pace_meter", "live_feedback", "negotiation")


WHISPER_MODELS = ("tiny", "base", "small", "medium")


def clean_whisper_model(value) -> Optional[str]:
    """The local transcription model's size, as the Settings window offers them. None (or "")
    means the provider's default. Anything else is refused: the name goes straight to a download."""
    if value in (None, ""):
        return None
    if value not in WHISPER_MODELS:
        raise ValueError(f"whisper_model must be one of {', '.join(WHISPER_MODELS)}, got {value!r}")
    return value


def clean_mode(value) -> str:
    """interview or meeting (roadmap item 4). It picks the prompt and which roles fit, so an unknown
    value is refused rather than quietly treated as an interview."""
    if not isinstance(value, str) or value not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, got {value!r}")
    return value

# Overlay themes (2026-09-11, design canvas 2a and 12). Each has a block in app/src/styles.css
# and an option in app/src/settings.html; tests/test_themes.py keeps the three lists together.
# The canvas marks the six after "light" as paid (Pro); nothing enforces that before plans (M5).
THEMES = ("dark", "light", "aurora", "teleprompter", "paper", "terminal", "frost", "contrast")


def clean_theme(value) -> str:
    """One of THEMES, or ValueError: an unknown name would leave the overlay on the default while
    the settings window showed something else."""
    if not isinstance(value, str) or value not in THEMES:
        raise ValueError(f"theme must be one of {', '.join(THEMES)}, got {value!r}")
    return value


# The app's own language (2026-09-11, maintainer's request): English, or Arabic laid out right to left in
# both windows and every theme. Not `lang`, which is the language the call is in.
UI_LANGS = ("en", "ar")


def clean_ui_lang(value) -> str:
    """One of UI_LANGS, or ValueError."""
    if not isinstance(value, str) or value not in UI_LANGS:
        raise ValueError(f"ui_lang must be one of {', '.join(UI_LANGS)}, got {value!r}")
    return value

# The overlay's background, not its text: below this the panel stops separating the lines from
# whatever video is behind them, and a slider dragged to zero would leave text floating on nothing.
MIN_OPACITY = 0.3


def clamp_opacity(value) -> float:
    """A number between MIN_OPACITY and 1. Raises ValueError for anything that is not a number."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"opacity must be a number, got {value!r}")
    if v != v:                       # NaN
        raise ValueError("opacity must be a number, got NaN")
    return round(min(1.0, max(MIN_OPACITY, v)), 2)


# Global hotkeys (roadmap item 3). The window registers them (app/src-tauri/src/lib.rs) and says
# which ones it could not, because only the OS knows whether another application owns one; this
# side checks what can be checked without it. A global hotkey takes its combination from every
# application while the overlay runs, so the call actions default to Ctrl+Alt, which editors and
# browsers use less than Ctrl+Shift, and every one of them can be changed or cleared.
HOTKEY_ACTIONS = ("toggle", "click_through", "pause", "previous", "next", "more", "retry",
                  "say", "recap", "ask", "shorter")
DEFAULT_HOTKEYS = {
    "toggle": "Ctrl+Shift+P",        # show and hide; the only way back to a hidden window
    "click_through": "Ctrl+Shift+L",
    "pause": "Ctrl+Alt+Space",
    "previous": "Ctrl+Alt+Left",
    "next": "Ctrl+Alt+Right",
    "more": "Ctrl+Alt+M",
    "retry": "Ctrl+Alt+R",
    # The on-demand actions (roadmap item 17), which shipped as buttons only. A button is no use
    # for the case they exist for: the user is mid-call, looking at the other person or at a shared
    # screen, and reaching for the mouse is the thing they cannot do. Ctrl+Alt like the rest, and
    # each letter is the action's own (R was already retry's, so a recap is C).
    "say": "Ctrl+Alt+S",
    "recap": "Ctrl+Alt+C",
    "ask": "Ctrl+Alt+Q",
    "shorter": "Ctrl+Alt+H",
}
MAX_HOTKEY_CHARS = 40

# The modifier names the Rust parser (global-hotkey 0.8) accepts, and what each one means on
# Windows, so two spellings of one combination are recognised as the same combination.
_MODIFIERS = {
    "CTRL": "CTRL", "CONTROL": "CTRL",
    "CMDORCTRL": "CTRL", "CMDORCONTROL": "CTRL", "COMMANDORCTRL": "CTRL", "COMMANDORCONTROL": "CTRL",
    "ALT": "ALT", "OPTION": "ALT",
    "SHIFT": "SHIFT",
    "SUPER": "SUPER", "CMD": "SUPER", "COMMAND": "SUPER",
}
_MODIFIER_ORDER = ("CTRL", "ALT", "SHIFT", "SUPER")


def _canonical_hotkey(action: str, combo: str) -> str:
    """The combination with its spelling removed ("Control+Shift+KeyP" -> "CTRL+SHIFT+P").

    Raises ValueError when it is not a modifier-plus-key combination. A key with no modifier is
    refused on purpose: registered globally it would stop that key typing in every application."""
    tokens = [t.strip() for t in combo.split("+")]
    example = "like Ctrl+Alt+M"
    if any(not t for t in tokens):
        raise ValueError(f"{action}: {combo!r} is not a hotkey, {example}")
    *mods, key = [t.upper() for t in tokens]
    if not mods or key in _MODIFIERS or any(m not in _MODIFIERS for m in mods):
        raise ValueError(f"{action}: a hotkey needs at least one modifier and one key, {example}")
    if key.startswith("ARROW"):
        key = key[len("ARROW"):]
    elif key.startswith("KEY") and len(key) == 4:
        key = key[3:]
    elif key.startswith("DIGIT") and len(key) == 6:
        key = key[5:]
    held = {_MODIFIERS[m] for m in mods}
    return "+".join([m for m in _MODIFIER_ORDER if m in held] + [key])


def clean_hotkeys(values, current: dict) -> dict:
    """Merge a change from the settings window into the current hotkeys, or raise ValueError.

    `values` may name only the actions that changed. An empty string (or None) clears an action,
    except the show/hide toggle: with it cleared, the Hide button would hide the overlay for good.
    """
    if not isinstance(values, dict):
        raise ValueError("hotkeys must be an object of action -> combination")
    merged = {**DEFAULT_HOTKEYS, **{a: c for a, c in (current or {}).items() if a in HOTKEY_ACTIONS}}
    for action, combo in values.items():
        if action not in HOTKEY_ACTIONS:
            continue
        combo = "" if combo is None else combo
        if not isinstance(combo, str) or len(combo) > MAX_HOTKEY_CHARS:
            raise ValueError(f"{action}: a hotkey is a short text like Ctrl+Alt+M")
        combo = "+".join(t.strip() for t in combo.split("+")) if combo.strip() else ""
        if combo:
            _canonical_hotkey(action, combo)
        merged[action] = combo
    if not merged["toggle"]:
        raise ValueError("toggle: show and hide cannot be cleared, it is the only way back to a hidden window")
    seen = {}
    for action in HOTKEY_ACTIONS:
        if not merged[action]:
            continue
        canonical = _canonical_hotkey(action, merged[action])
        if canonical in seen:
            raise ValueError(f"{action} and {seen[canonical]} would both be {merged[action]}")
        seen[canonical] = action
    return merged


@dataclass
class Settings:
    preset: str = ""                 # "" = no role preset
    lang: str = "en"
    vendor: str = "gemini"
    model: Optional[str] = None
    stt_vendor: str = "deepgram"     # deepgram | whisper (roadmap item 26: offline, no key, no interims)
    whisper_model: Optional[str] = None  # faster-whisper size; None = the provider's default
    user_mic: bool = False           # transcribe your own microphone as context
    mic: bool = False                # listen to the microphone instead of system audio
    suggest: bool = True
    speculate: bool = True           # ask early: faster, and more requests against a rate limit
    style: str = "full"              # answer profile: full | keywords | direction
    animate: bool = True             # overlay: type text in rather than showing it at once
    text_size: str = "medium"        # overlay: small | medium | large
    opacity: float = 0.9             # overlay background, MIN_OPACITY..1; the text stays solid
    hotkeys: dict = field(default_factory=lambda: dict(DEFAULT_HOTKEYS))
    theme: str = "dark"              # overlay and settings window: one of THEMES
    ui_lang: str = "en"              # the app's own language: en | ar (right to left)
    save_transcripts: bool = False   # roadmap item 6: keep each call's transcript; off unless turned on
    pace_meter: bool = True          # roadmap item 8: show the speaking pace meter during a call
    live_feedback: bool = False      # roadmap item 9: a verdict on each answer; off unless turned on (it costs requests)
    negotiation: bool = False        # roadmap item 25: a pay or price talk; off unless turned on
    mode: str = "interview"          # the call: interview | meeting (picks the prompt and the roles)
    speaker_device: str = ""         # the call's audio: a loopback device by name; "" = Windows default
    mic_device: str = ""             # the user's microphone by name; "" = Windows default
    cv_name: str = ""                # before item 5: names the old cv.txt when it is moved in
    fields: dict = field(default_factory=dict)   # room for later without a migration

    def to_json(self) -> dict:
        d = asdict(self)
        d["keys"] = keys_status()        # set or not, never the values
        from .stt import whisper_available
        d["whisper_available"] = whisper_available()   # roadmap item 26: offline STT installed?
        d["documents"] = {m: list_documents(m) for m in MODES}
        # the words the next call of the saved kind would have, for the "no document" warnings
        d["cv_words"] = sum(doc["words"] for doc in d["documents"][self.mode])
        d["document_words_limit"] = document_words_limit(self.mode)
        d["transcripts"] = {"count": len(list_transcripts()), "folder": transcripts_dir()}
        return d


def load() -> Settings:
    try:
        with open(settings_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return Settings()
    known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
    s = Settings(**known)
    # A file from before an action existed gets that action's default; a file edited by hand into
    # something unusable gets all the defaults rather than an overlay with no way to show itself.
    try:
        s.hotkeys = clean_hotkeys({}, s.hotkeys if isinstance(s.hotkeys, dict) else {})
    except ValueError:
        s.hotkeys = dict(DEFAULT_HOTKEYS)
    if s.theme not in THEMES:            # a theme that was renamed or removed since it was saved
        s.theme = THEMES[0]
    if s.mode not in MODES:
        s.mode = MODES[0]
    if s.ui_lang not in UI_LANGS:
        s.ui_lang = UI_LANGS[0]
    if s.stt_vendor not in ("deepgram", "whisper"):
        s.stt_vendor = "deepgram"
    return s


def save(s: Settings) -> None:
    os.makedirs(app_dir(), exist_ok=True)
    with open(settings_path(), "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, indent=2)


# ---- transcripts (roadmap item 6) ----
# Kept only when the user turns save_transcripts on: one JSON file per
# call, written by fawa2ly/summary.py as the call ends. Never audio.

def transcripts_dir() -> str:
    return os.path.join(app_dir(), "transcripts")


def list_transcripts() -> list[str]:
    """Saved transcript files, oldest first."""
    folder = transcripts_dir()
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json"))


def delete_transcripts() -> int:
    """Delete every saved transcript (the Delete button in Settings). Returns how many went."""
    removed = 0
    for path in list_transcripts():
        try:
            os.remove(path)
            removed += 1
        except OSError:
            continue
    return removed


# ---- documents (roadmap item 5) ----
# A call can carry several documents: a CV and a portfolio for an interview, an agenda and project
# notes for a meeting. They belong to a mode, because a CV has no place in a meeting's prompt. Each is kept as extracted text beside a small index of the original names:
#
#     documents/<mode>/index.json   [{"name": "cv.pdf", "file": "1.txt", "words": 512}, ...]
#     documents/<mode>/1.txt
#
# The single cv.txt of earlier versions becomes an interview document the first time documents
# are read, under the filename settings.json remembered for it.


def documents_dir(mode: str) -> str:
    return os.path.join(app_dir(), "documents", clean_mode(mode))


def _index_path(mode: str) -> str:
    return os.path.join(documents_dir(mode), "index.json")


def _read_index(mode: str) -> list[dict]:
    try:
        with open(_index_path(mode), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("name") and d.get("file")]


def _write_index(mode: str, entries: list[dict]) -> None:
    os.makedirs(documents_dir(mode), exist_ok=True)
    with open(_index_path(mode), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def _store(mode: str, name: str, text: str) -> None:
    entries = _read_index(mode)
    existing = next((d for d in entries if d["name"] == name), None)
    if existing is not None:
        file = existing["file"]
    else:
        used = {d["file"] for d in entries}
        n = 1
        while f"{n}.txt" in used:
            n += 1
        file = f"{n}.txt"
    os.makedirs(documents_dir(mode), exist_ok=True)
    with open(os.path.join(documents_dir(mode), file), "w", encoding="utf-8") as f:
        f.write(text)
    entry = {"name": name, "file": file, "words": len(text.split())}
    if existing is not None:
        entries[entries.index(existing)] = entry
    else:
        entries.append(entry)
    _write_index(mode, entries)


def _migrate_single_cv() -> None:
    """cv.txt from before item 5 becomes the first interview document, then goes."""
    old = cv_path()
    if not os.path.isfile(old) or _read_index("interview"):
        return
    try:
        with open(old, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return
    if text:
        name = ""
        try:
            with open(settings_path(), encoding="utf-8") as f:
                name = json.load(f).get("cv_name") or ""
        except (OSError, ValueError, AttributeError):
            pass
        _store("interview", os.path.basename(name) or "cv.txt", text)
    try:
        os.remove(old)
    except OSError:
        pass


def list_documents(mode: str) -> list[dict]:
    """Name and word count of each document stored for this kind of call, in the order added."""
    _migrate_single_cv()
    return [{"name": d["name"], "words": int(d.get("words") or 0)} for d in _read_index(mode)]


def load_documents(mode: str) -> list[tuple[str, str]]:
    """(name, text) of each document stored for this kind of call, for the prompt."""
    _migrate_single_cv()
    out = []
    for d in _read_index(mode):
        try:
            with open(os.path.join(documents_dir(mode), d["file"]), encoding="utf-8") as f:
                text = f.read().strip()
        except OSError:
            continue
        if text:
            out.append((d["name"], text))
    return out


def document_words_limit(mode: str) -> int:
    """How many words one kind of call's documents may have together."""
    clean_mode(mode)
    return MAX_DOCUMENT_WORDS


def add_document(mode: str, name: str, text: str) -> None:
    """Store a document for one kind of call, replacing one of the same name. Refused, with nothing
    stored, when it has no text or would take the call's documents past MAX_DOCUMENT_WORDS."""
    name = os.path.basename((name or "").strip())
    text = (text or "").strip()
    if not name:
        raise ValueError("a document needs a name")
    if not text:
        raise ValueError(f"{name} has no readable text")
    _migrate_single_cv()
    words = len(text.split())
    others = sum(int(d.get("words") or 0) for d in _read_index(mode) if d["name"] != name)
    limit = document_words_limit(mode)
    if words + others > limit:
        raise ValueError(f"{name} has {words} words, which would bring this call's documents to "
                         f"{words + others}; the limit is {limit}. Remove a document or "
                         "shorten this one.")
    _store(mode, name, text)


def remove_document(mode: str, name: Optional[str] = None) -> None:
    """Forget one document by name, or every document of this kind of call when no name is given."""
    _migrate_single_cv()
    entries = _read_index(mode)
    keep = [] if name is None else [d for d in entries if d["name"] != name]
    for d in entries:
        if d not in keep:
            try:
                os.remove(os.path.join(documents_dir(mode), d["file"]))
            except OSError:
                pass
    _write_index(mode, keep)
