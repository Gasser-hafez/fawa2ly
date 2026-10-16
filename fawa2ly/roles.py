"""
Roles the user describes (2026-09-11, maintainer's request).

The shipped presets cannot cover every job. When a role is missing, the user says what they do,
typed or spoken, and the model writes it up in the same shape as a preset: who is speaking, what
a strong answer from them brings out, and what would make it weak. It is saved as a YAML file in
the user's own presets folder (%APPDATA%/Fawa2ly/presets), where the role list finds it next to
the shipped ones, and it can be deleted from there.

The model's reply is checked before anything is written: a reply that is not the requested JSON,
or is missing a title or its focus points, is refused with a message instead of saving a role that
would quietly weaken every answer that uses it.
"""

from __future__ import annotations

import json
import os
import re

import yaml

from .context.builder import user_preset_dir
from .limits import DEFAULT_MAX_TOKENS
from .llm import collect
from .llm.base import SuggestionRequest

MIN_DESCRIPTION_WORDS = 5
MAX_DESCRIPTION_CHARS = 3000
PREFIX = "custom-"

_KIND = {"interview": "a job interview", "meeting": "a work meeting"}

ANALYZE_SYSTEM = """You turn a person's description of their work into a short role brief for an assistant that suggests what to say during {kind}. Reply with one JSON object and nothing else:
{{"title": "...", "role": "...", "focus": ["...", "..."], "avoid": ["...", "..."]}}
- title: 2 to 4 words naming the role, for a list of roles. Example: "Pharmacy technician".
- role: one line saying who is speaking and in what situation. Example: "Pharmacy technician in a hospital pharmacy".
- focus: 3 to 5 things a strong answer from this person should bring out: the concrete work, the tools and methods of this field, the results that count in it, the situations it is judged on.
- avoid: 2 things that would make this person's answers weak or risky in this field.
Every item is a short phrase in English, whatever language the description is in. Use only what the description says or what is true of every job of this kind; never invent an employer, a product or a number."""


def make_provider(vendor: str, model):
    """The saved model, counted like every other request. Looked up at call time, so a test can
    replace it."""
    from .llm import OpenAICompatibleLLM
    from .usage import MeteredProvider
    return MeteredProvider(OpenAICompatibleLLM.from_env(vendor, model))


def _parse(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the model did not return a role brief; try again or describe the role in more detail")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise ValueError("the model's role brief was not valid JSON; try again")
    def phrases(key, least, most):
        items = [str(x).strip() for x in (data.get(key) or []) if str(x).strip()] if isinstance(data.get(key), list) else []
        if len(items) < least:
            raise ValueError(f"the model's role brief had no usable {key}; try again")
        return [x[:160] for x in items[:most]]
    title = str(data.get("title") or "").strip()[:60]
    role = str(data.get("role") or "").strip()[:200]
    if not title or not role:
        raise ValueError("the model's role brief had no title or role; try again")
    return {"title": title, "role": role, "focus": phrases("focus", 1, 5), "avoid": phrases("avoid", 0, 4)}


def _unique_name(title: str, folder: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "role"
    name, n = PREFIX + slug, 2
    while os.path.exists(os.path.join(folder, f"{name}.yaml")):
        name, n = f"{PREFIX}{slug}-{n}", n + 1
    return name


async def analyze(description: str, mode: str, provider) -> dict:
    """Write up a described role and save it. Returns {"name", "title", "role", "focus", "avoid"}."""
    description = (description or "").strip()[:MAX_DESCRIPTION_CHARS]
    if len(description.split()) < MIN_DESCRIPTION_WORDS:
        raise ValueError(f"say a little more about the role: at least {MIN_DESCRIPTION_WORDS} words")
    if mode not in _KIND:
        raise ValueError(f"unknown call mode {mode!r}")
    req = SuggestionRequest(question="", system=ANALYZE_SYSTEM.format(kind=_KIND[mode]),
                            max_tokens=DEFAULT_MAX_TOKENS, user=f"How the person describes their work:\n{description}")
    s = await collect(provider, req)
    if s.error:
        raise ValueError(f"the model could not analyze the role: {s.error[:160]}")
    brief = _parse(s.text)
    folder = user_preset_dir()
    os.makedirs(folder, exist_ok=True)
    name = _unique_name(brief["title"], folder)
    doc = {"name": name, "mode": mode, "title": brief["title"], "role": brief["role"],
           "focus": brief["focus"], "avoid": brief["avoid"], "custom": True, "description": description}
    with open(os.path.join(folder, f"{name}.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    return {"name": name, **brief}


def delete(name: str) -> None:
    """Remove a role the user created. Shipped presets cannot be deleted this way."""
    if not name.startswith(PREFIX) or "/" in name or "\\" in name or ".." in name:
        raise ValueError("only roles you created can be deleted")
    try:
        os.remove(os.path.join(user_preset_dir(), f"{name}.yaml"))
    except FileNotFoundError:
        raise ValueError(f"no role named {name}")
