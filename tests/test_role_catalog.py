"""The shipped roles (2026-09-11, maintainer's request: many more roles, in sections, chosen in Settings).

Every file loads, is written for a kind of call, and has a section and a short title for the
settings list. project-manager.yaml did not parse from the day it was added (a quoted phrase with
more words after it), and available_modes() left it out without a word, so that role was never
offered; this is the check that would have said so.
"""

import os

import yaml

from fawa2ly.context.builder import PRESET_DIR, Preset
from fawa2ly.llm.prompts import MODES


def shipped() -> dict[str, dict]:
    out = {}
    for f in sorted(os.listdir(PRESET_DIR)):
        if f.endswith(".yaml"):
            with open(os.path.join(PRESET_DIR, f), encoding="utf-8") as fh:
                out[f[:-5]] = yaml.safe_load(fh)
    return out


def test_every_shipped_role_loads_with_a_section_a_title_and_what_to_bring_out():
    roles = shipped()
    assert len(roles) >= 60
    for name, data in roles.items():
        assert data["name"] == name
        assert data["mode"] in MODES, name
        assert data.get("category") and data.get("title") and data.get("role"), name
        assert 3 <= len(data["focus"]) <= 5 and data.get("avoid"), name
        assert Preset.find(name, PRESET_DIR).role == data["role"]


def test_titles_are_unique_for_a_kind_of_call_and_the_sections_stay_few():
    by_mode: dict[str, list[dict]] = {}
    for data in shipped().values():
        by_mode.setdefault(data["mode"], []).append(data)
    assert set(by_mode) == set(MODES)
    for mode, roles in by_mode.items():
        titles = [r["title"] for r in roles]
        assert len(titles) == len(set(titles)), mode
        sections = {r["category"] for r in roles}
        assert "General" in sections and len(sections) <= 14, (mode, sorted(sections))
