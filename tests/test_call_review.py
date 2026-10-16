"""After the call, part 2 (roadmap item 7, 2026-09-11): the model reviews a saved call.

What must hold: only the user's own words are judged, and a call without any is refused; the
model's reply is checked before it is shown or saved (a score out of range is clamped, a verdict
or a question number that does not exist is dropped); a review is kept in the call's file and not
asked for twice; and nothing outside the transcripts folder can be opened by name.
"""

import asyncio
import datetime
import json

import pytest
from websockets.asyncio.client import connect

from fawa2ly import review
from fawa2ly import settings as user_settings
from fawa2ly.llm import FakeLLM
from fawa2ly.server import SessionServer
from fawa2ly.summary import save_transcript


def call(lang="en", you=True):
    turns = [{"t": 1.0, "who": "them", "text": "Tell me about yourself", "question": True},
             {"t": 8.0, "who": "them", "text": "Why Flutter?", "question": True}]
    if you:
        turns.insert(1, {"t": 3.0, "who": "you", "text": "I am a final-year IT student building Flutter apps",
                         "question": False})
    summary = {"mode": "interview", "lang": lang, "heard": 2, "answered": 2, "duration_s": 60,
               "questions": [{"n": 1, "question": "Tell me about yourself", "lines": ["Final-year IT student"]},
                             {"n": 2, "question": "Why Flutter?", "lines": ["One codebase for both platforms"]}]}
    return {"version": 1, "summary": summary, "transcript": turns}


REPLY = json.dumps({
    "score": 12, "summary": "Clear on background, silent on the second question.",
    "questions": [{"n": 1, "verdict": "Landed", "missing": ""},
                  {"n": 2, "verdict": "great", "missing": "why Flutter over native"},
                  {"n": 9, "verdict": "missed", "missing": "a question that was never asked"}],
    "strengths": ["named the degree", "named the stack", "short", "a fourth"],
    "improve": ["answer why, not what"],
})


def test_a_review_judges_only_what_the_user_said_and_is_checked_before_use():
    provider = FakeLLM(bullets=[REPLY])
    r = asyncio.run(review.review(call(lang="ar-EG"), provider))
    assert r["score"] == 10, "clamped to the scale"
    assert r["questions"] == [{"n": 1, "verdict": "landed", "missing": ""}], \
        "a verdict that is not one of the three, and a question that does not exist, are dropped"
    assert len(r["strengths"]) == 3 and r["improve"] == ["answer why, not what"]
    req = provider.requests[0]
    assert "you: I am a final-year IT student" in req.user and "suggested: Final-year IT student" in req.user
    assert "Arabic" in req.system and "not the user's words" in req.system


def test_a_call_with_none_of_the_users_words_or_a_bad_reply_is_refused():
    with pytest.raises(ValueError, match="none of your words"):
        asyncio.run(review.review(call(you=False), FakeLLM(bullets=[REPLY])))
    with pytest.raises(ValueError, match="did not return a review"):
        asyncio.run(review.review(call(), FakeLLM(bullets=["Sorry, I cannot help with that"])))
    with pytest.raises(ValueError, match="judged none"):
        asyncio.run(review.review(call(), FakeLLM(bullets=[json.dumps({"score": 5, "questions": []})])))


def save(tmp_path, monkeypatch, data):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = save_transcript(data["summary"], data["transcript"], user_settings.transcripts_dir(),
                           now=datetime.datetime(2026, 9, 11, 10, 0, 0))
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def test_a_review_is_kept_with_the_call_and_asked_for_again_only_on_request(tmp_path, monkeypatch):
    name = save(tmp_path, monkeypatch, call())
    provider = FakeLLM(bullets=[REPLY])
    first = asyncio.run(review.review_call(name, provider))
    again = asyncio.run(review.review_call(name, provider))
    assert again == first and len(provider.requests) == 1, "opened again, not paid for again"
    asyncio.run(review.review_call(name, provider, again=True))
    assert len(provider.requests) == 2
    listed = review.list_calls()
    assert listed[0]["file"] == name and listed[0]["reviewed"] and listed[0]["has_you"]
    assert review.load_call(name)["review"]["score"] == 10


def test_only_a_file_in_the_transcripts_folder_can_be_opened(tmp_path, monkeypatch):
    save(tmp_path, monkeypatch, call())
    (tmp_path / "Fawa2ly" / "settings.json").write_text("{}", encoding="utf-8")
    for name in ("../settings.json", "..\\settings.json", "missing.json", "", None, "C:/Windows/win.ini"):
        with pytest.raises(ValueError):
            review.find_call(name)


def test_a_call_is_listed_and_reviewed_from_the_settings_window(tmp_path, monkeypatch):
    name = save(tmp_path, monkeypatch, call())
    monkeypatch.setattr("fawa2ly.roles.make_provider", lambda vendor, model: FakeLLM(bullets=[REPLY]))

    async def go():
        server = SessionServer(lambda chosen, on_event: None, host="127.0.0.1", port=0)
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def until(match):
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if match(event):
                        return event
            await ws.send(json.dumps({"cmd": "calls"}))
            before = await until(lambda e: e["type"] == "calls")
            await ws.send(json.dumps({"cmd": "call", "file": name, "review": True}))
            reviewed = await until(lambda e: e["type"] in ("call", "call_error"))
            after = await until(lambda e: e["type"] == "calls")
            await ws.send(json.dumps({"cmd": "call", "file": "../settings.json"}))
            refused = await until(lambda e: e["type"] in ("call", "call_error"))
        task.cancel()
        return before, reviewed, after, refused

    before, reviewed, after, refused = asyncio.run(go())
    assert before["calls"][0]["file"] == name and not before["calls"][0]["reviewed"]
    assert reviewed["type"] == "call" and reviewed["review"]["score"] == 10
    assert reviewed["summary"]["heard"] == 2
    assert after["calls"][0]["reviewed"]
    assert refused["type"] == "call_error"


NOTES = json.dumps({"summary": "Background, then why Flutter.", "decisions": ["", "Second round next week"],
                    "actions": [{"task": "Send the portfolio", "owner": "you"}, {"task": "  ", "owner": "them"},
                                "Share the test task"],
                    "questions": ["Salary range"]})


def test_notes_come_from_the_conversation_alone_and_are_checked_before_use():
    """Notes (roadmap item 19): the suggested lines are not sent, the user's words are not required,
    blank items are dropped, and a reply with nothing in it is refused."""
    provider = FakeLLM(bullets=[NOTES])
    n = asyncio.run(review.notes(call(you=False), provider))
    assert n["decisions"] == ["Second round next week"] and n["questions"] == ["Salary range"]
    assert n["actions"] == [{"task": "Send the portfolio", "owner": "you"}, {"task": "Share the test task", "owner": ""}]
    assert "them: Why Flutter?" in provider.requests[0].user and "suggested" not in provider.requests[0].user
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(review.notes(call(), FakeLLM(bullets=[json.dumps({"summary": "", "decisions": []})])))


def test_notes_are_kept_with_the_call_and_given_as_markdown(tmp_path, monkeypatch):
    name = save(tmp_path, monkeypatch, call())
    made = []
    monkeypatch.setattr("fawa2ly.roles.make_provider",
                        lambda vendor, model: made.append(1) or FakeLLM(bullets=[NOTES]))

    class Socket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(json.loads(message))

    ws = Socket()

    async def go():
        server = SessionServer(lambda chosen, on_event: None)
        await server.handle_call(ws, {"file": name, "notes": True})
        await server.handle_call(ws, {"file": name})

    asyncio.run(go())
    written, listed, opened = ws.sent
    assert written["type"] == "call" and written["notes"]["decisions"] == ["Second round next week"]
    assert "## Action items\n- [ ] Send the portfolio (you)\n- [ ] Share the test task" in written["notes_markdown"]
    assert listed["type"] == "calls"
    assert opened["notes"] == written["notes"] and made == [1], "opened again from the file, not asked again"
