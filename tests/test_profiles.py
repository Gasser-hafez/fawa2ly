"""Answer profiles, More, the interview rule, the CV word count and the settings command (2026-09-11).

rehearsal7 ran with no background loaded and nothing on screen said so; the maintainer then asked for
profiles (full answers, keywords, or direction only), a More button, an interview-aware prompt
and a settings window. No test here reads the real app folder or reaches a network.
"""

import asyncio
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder, filter_bullets
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.limits import KEYWORD_WORDS, MAX_BULLET_WORDS
from fawa2ly.llm import FakeLLM
from fawa2ly.llm.prompts import STYLES, SYSTEM_V1, system_for
from fawa2ly.pipeline import Utterance
from fawa2ly.server import SessionServer


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_every_profile_keeps_the_rules_that_are_not_about_shape():
    for style in STYLES:
        text = system_for(style)
        assert "Never ask a question back" in text
        assert "never invent an employer, a job, a school, a date" in text
        assert "want to hear more" in text, "the interview rule belongs to every profile"
    assert system_for("full") == SYSTEM_V1


def test_each_profile_asks_for_its_own_shape():
    assert "complete, natural spoken sentence" in system_for("full")
    assert f"At most {KEYWORD_WORDS} words per bullet" in system_for("keywords")
    assert "not the words to say" in system_for("direction")
    with pytest.raises(ValueError):
        system_for("poetry")


def test_the_guardrail_follows_the_profile():
    cue = "Chat app: offline queue, Firebase sync"
    assert filter_bullets([cue], style="keywords")[0] == [cue]
    assert filter_bullets([cue], style="full")[1][0].reason == "label_prefix"
    long_cue = " ".join(["word"] * (KEYWORD_WORDS + 1))
    assert filter_bullets([long_cue], style="keywords")[1][0].reason == "too_long"
    sentence = " ".join(["word"] * MAX_BULLET_WORDS)
    assert filter_bullets([sentence], style="full")[0] == [sentence]


def test_the_profile_can_change_mid_call_and_a_bad_one_changes_nothing():
    b = ContextBuilder(style="keywords")
    assert b.system().startswith(system_for("keywords"))
    b.set_style("direction")
    assert b.system().startswith(system_for("direction")) and b.style == "direction"
    with pytest.raises(ValueError):
        b.set_style("poetry")
    assert b.style == "direction"


def utt(n=1, text="How did you handle state in the chat app?"):
    return Utterance(n=n, speaker=0, trigger="question_mark", text=text, segments=1,
                     t_first_final=0.0, t_last_final=0.0, t_flush=0.0, gap_ms=0,
                     speech_to_text_ms=None, source="turn", question_end_to_fire_ms=1500,
                     stream="other")


def test_more_adds_lines_that_do_not_repeat_what_is_on_screen():
    llm = FakeLLM(bullets=["I used Provider for the chat screens",
                           "Messages came in as Firebase streams"])
    seen = []
    s = Suggester(llm, ContextBuilder(), on_line=lambda n, t: seen.append(t),
                  on_result=lambda a: seen.append(("assist", a.more, tuple(a.bullets))))

    async def go():
        first = await s.handle(utt())
        llm.bullets = ["Messages came in as Firebase streams",               # a repeat: dropped
                       "Typing indicators were debounced so the list did not flicker",
                       "Presence lived in its own small provider to keep rebuilds cheap"]
        return first, await s.more(first.n)

    first, more = asyncio.run(go())
    assert more.more is True and more.n == first.n
    assert more.bullets == ["Typing indicators were debounced so the list did not flicker",
                            "Presence lived in its own small provider to keep rebuilds cheap"]
    assert "Already on screen" in llm.requests[-1].question
    assert seen[-1] == ("assist", True, tuple(more.bullets))


def test_more_needs_an_answer_to_build_on():
    s = Suggester(FakeLLM(), ContextBuilder())
    assert asyncio.run(s.more(42)) is None


def test_the_session_says_how_much_background_the_model_has():
    """rehearsal7: no background was loaded and nothing on screen said so."""
    loaded = Engine(EngineConfig(suggest=True, fake_llm=True, cv_text="Flutter developer with Firebase"),
                    on_event=lambda _e: None)
    loaded._build_suggester(None)
    assert loaded.cv_words == 4
    empty = Engine(EngineConfig(suggest=True, fake_llm=True), on_event=lambda _e: None)
    empty._build_suggester(None)
    assert empty.cv_words == 0


def test_the_settings_window_saves_choices_and_every_window_hears_about_them():
    async def go():
        server = SessionServer(lambda chosen, on_event: None, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as settings_ws, \
                connect(f"ws://127.0.0.1:{server.port}") as overlay_ws:
            while json.loads(await asyncio.wait_for(overlay_ws.recv(), timeout=3))["type"] != "settings":
                pass                        # what a window gets on connect ends with the saved settings
            await settings_ws.send(json.dumps({"cmd": "settings", "values": {
                "style": "keywords", "animate": False, "text_size": "large", "api_key": "nope"}}))
            while True:
                event = json.loads(await asyncio.wait_for(overlay_ws.recv(), timeout=3))
                if event["type"] == "settings":
                    break
        task.cancel()
        return event

    event = asyncio.run(go())
    s = event["settings"]
    assert (s["style"], s["animate"], s["text_size"]) == ("keywords", False, "large")
    assert "api_key" not in s, "only settings the engine knows are stored"
