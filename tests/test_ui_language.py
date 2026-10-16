"""The app in Arabic (2026-09-11, maintainer's request): both windows and every theme, right to left.

The pages stay written in English and app/src/i18n.js translates what they show, so what can go
wrong is a string with no translation, which would sit in English in the middle of an Arabic
window. These tests read every piece of text both pages are written with, and every role section
name, and require an Arabic entry for each. Strings the scripts build at run time were checked in
the browser. The choice itself is a setting like the theme: checked, saved, and a value from a
hand-edited file falls back to English.
"""

import io
import json
import os
import re
from html.parser import HTMLParser

import pytest
import yaml

from fawa2ly import settings as user_settings
from fawa2ly.context.builder import PRESET_DIR, USER_CATEGORY

I18N = os.path.join("app", "src", "i18n.js")
PAGES = (os.path.join("app", "src", "index.html"), os.path.join("app", "src", "settings.html"))
ARABIC = re.compile(r"[؀-ۿ]")


def dictionary() -> dict[str, str]:
    src = io.open(I18N, encoding="utf-8").read()
    return {json.loads(k): json.loads(v) for k, v in
            re.findall(r'^\s*("(?:[^"\\]|\\.)*"):\s*("(?:[^"\\]|\\.)*"),?\s*$', src, re.M)}


class PageText(HTMLParser):
    """Every text and translatable attribute a page is written with, outside translate="no"."""
    VOID = {"input", "meta", "link", "br", "img", "hr"}

    def __init__(self):
        super().__init__()
        self.stack: list[bool] = []
        self.found: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = a.get("translate") == "no" or tag in ("script", "style") or bool(self.stack and self.stack[-1])
        if not skip:
            self.found.update(a[k].strip() for k in ("placeholder", "title", "aria-label") if a.get(k))
        if tag not in self.VOID:
            self.stack.append(skip)

    def handle_endtag(self, tag):
        if tag not in self.VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if not (self.stack and self.stack[-1]) and data.strip():
            self.found.add(data.strip())


@pytest.mark.parametrize("page", PAGES)
def test_every_string_a_window_is_written_with_has_an_arabic_translation(page):
    parser = PageText()
    parser.feed(io.open(page, encoding="utf-8").read())
    words = {s for s in parser.found if re.search(r"[A-Za-z]", s) and not ARABIC.search(s)} - {"Fawa2ly"}
    assert not sorted(words - set(dictionary()))


def test_every_role_section_has_an_arabic_name():
    sections = {USER_CATEGORY, "General"}
    for f in os.listdir(PRESET_DIR):
        if f.endswith(".yaml"):
            with open(os.path.join(PRESET_DIR, f), encoding="utf-8") as fh:
                sections.add(yaml.safe_load(fh)["category"])
    assert not sorted(sections - set(dictionary()))


def test_translations_are_arabic_and_both_pages_load_the_translator_before_anything_draws():
    d = dictionary()
    assert len(d) > 150
    assert not [k for k, v in d.items() if not ARABIC.search(v)]
    for page in PAGES:
        html = io.open(page, encoding="utf-8").read()
        assert html.index('src="i18n.js"') < html.index("</head>") < html.index('src="chrome.js"')


def test_the_app_language_is_checked_saved_and_falls_back_to_english(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert "ui_lang" in user_settings.EDITABLE
    assert user_settings.clean_ui_lang("ar") == "ar"
    with pytest.raises(ValueError):
        user_settings.clean_ui_lang("fr")
    s = user_settings.load()
    assert s.ui_lang == "en"
    s.ui_lang = "ar"
    user_settings.save(s)
    assert user_settings.load().ui_lang == "ar" and user_settings.load().to_json()["ui_lang"] == "ar"
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"ui_lang": "de"}, f)
    assert user_settings.load().ui_lang == "en"
