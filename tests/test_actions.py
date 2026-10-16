"""Actions on demand during a call (roadmap item 17, 2026-09-12): What should I say, Recap, Questions
to ask and Shorter, without waiting for a detected question."""

import asyncio
import json

from fawa2ly.assist import Suggester
from fawa2ly.context import ContextBuilder
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.llm import FakeLLM
from fawa2ly.llm.prompts import ACTION_LEAD, ACTIONS
from fawa2ly.pipeline import Utterance
from fawa2ly.server import SessionServer
from fawa2ly.summary import CallRecord


def utt(text="Why did you leave your last job?", n=1, trigger="question_mark"):
    return Utterance(n=n, speaker=0, trigger=trigger, text=text, segments=1, t_first_final=0.0,
                     t_last_final=0.0, t_flush=0.0, gap_ms=0, speech_to_text_ms=None, source="turn",
                     question_end_to_fire_ms=1500, stream="other")


def test_an_action_opens_its_own_card_from_the_conversation_so_far():
    llm = FakeLLM(bullets=["We covered your Flutter work", "They asked why you left your last job"])
    seen = []
    s = Suggester(llm, ContextBuilder(), on_question=lambda n, t: seen.append((n, t)))

    async def go():
        await s.handle(utt())
        card = await s.action("recap")
        await s.retry(card.n)
        return card, await s.action("dance")

    card, unknown = asyncio.run(go())
    # the card remembers which action opened it, so the overlay can label and translate its title
    assert card.n == 2 and seen[1] == (2, ACTIONS["recap"][0]) and s.action_cards == {2: "recap"}
    asked = llm.requests[1]
    assert asked.lead == ACTION_LEAD and asked.question == ACTIONS["recap"][1]
    assert ("them", "Why did you leave your last job?") in asked.history
    assert llm.requests[2].question == ACTIONS["recap"][1], "Retry asks for the recap again, not its title"
    assert unknown is None and len(llm.requests) == 3


def test_shorter_redoes_the_card_on_screen_from_its_lines():
    llm = FakeLLM(bullets=["I left because the night shifts stopped working for my family"])
    s = Suggester(llm, ContextBuilder())

    async def go():
        first = await s.handle(utt())
        llm.bullets = ["Night shifts stopped working for me"]
        return first, await s.action("shorter", first.n), await s.action("shorter", 99)

    first, short, missing = asyncio.run(go())
    assert short.n == first.n and short.bullets == s.shown_lines[first.n] == ["Night shifts stopped working for me"]
    assert "On screen now" in llm.requests[-1].question and first.bullets[0] in llm.requests[-1].question
    assert missing is None


def test_an_action_card_is_marked_and_not_counted_as_a_question_heard():
    events = []
    engine = Engine(EngineConfig(suggest=True, fake_llm=True), events.append)
    engine.suggester = engine._build_suggester(None)
    asyncio.run(engine.suggester.action("say"))
    assert [e["action"] for e in events if e["type"] == "question"] == ["say"], \
        "the action that opened the card, not just that one did"
    record = CallRecord(clock=lambda: 1.0)
    for e in [{"type": "status", "state": "listening"}, *events]:
        record.observe(e)
    assert record.summary()["heard"] == 0


def test_the_action_command_reaches_the_call():
    class Call:
        def __init__(self):
            self.asked = []

        def action(self, kind, n):
            self.asked.append((kind, n))

    class Socket:
        async def send(self, message):
            raise AssertionError(message)

    call = Call()

    async def go():
        server = SessionServer(lambda chosen, publish: call)
        server.engine = call
        await server.handle_command(Socket(), json.dumps({"cmd": "action", "kind": "recap"}))
        await server.handle_command(Socket(), json.dumps({"cmd": "action", "kind": "shorter", "n": "3"}))

    asyncio.run(go())
    assert call.asked == [("recap", None), ("shorter", 3)]


# ---- what item 17 shipped without (2026-09-13) ----

def test_a_recap_is_given_the_call_and_the_other_actions_the_last_few_turns():
    """A recap is for a user who looked away, and they looked away for longer than the six turns
    every other request carries: a recap of those would recap the part they just heard."""
    from fawa2ly.limits import RECAP_WORDS

    fake = FakeLLM()
    s = Suggester(fake, builder=ContextBuilder())
    for i in range(1, 31):
        asyncio.run(s.handle(utt(n=i, text=f"Turn number {i} of the conversation.", trigger="silence")))
    assert len(s.history) == 12, "the per-question history is still short"

    asyncio.run(s.action("recap"))
    turns = fake.requests[-1].history
    assert len(turns) == 30 and turns[0][1].startswith("Turn number 1 "), "the call, from its start"

    asyncio.run(s.action("say"))
    assert len(fake.requests[-1].history) == 6, "the others are about where the conversation is now"

    # and the call kept for a recap is bounded, so an hour of talk is neither unaffordable nor unreadable
    for i in range(400):
        asyncio.run(s.handle(utt(n=100 + i, text=" ".join(["word"] * 20), trigger="silence")))
    assert s.said_words <= RECAP_WORDS and len(s.said) < 400


def test_every_on_demand_action_has_a_hotkey_of_its_own():
    """A button is no use for the case they exist for: mid-call, looking at the other person, with
    no hand free for the mouse."""
    from fawa2ly.llm.prompts import ACTIONS
    from fawa2ly.settings import DEFAULT_HOTKEYS, HOTKEY_ACTIONS, clean_hotkeys

    for kind in (*ACTIONS, "shorter"):
        assert kind in HOTKEY_ACTIONS and DEFAULT_HOTKEYS[kind], kind
    combos = [DEFAULT_HOTKEYS[a] for a in HOTKEY_ACTIONS]
    assert len(set(combos)) == len(combos), "no two actions share a combination"
    assert clean_hotkeys({}, {}) == DEFAULT_HOTKEYS
