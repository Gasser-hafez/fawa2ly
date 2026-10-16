"""Several documents for each kind of call, a size budget, and DOCX (2026-09-11, roadmap item 5).

A meeting wants an agenda and project notes at once, an interview a CV and perhaps a
portfolio. Documents are stored per kind of call, go into the prompt with their names, and are
refused past MAX_DOCUMENT_WORDS rather than cut. Nothing here reads the real app folder.
"""

import asyncio
import base64
import json
import os

import pytest
from websockets.asyncio.client import connect

from fawa2ly import settings as user_settings
from fawa2ly.context import ContextBuilder, extract_cv, load_cv
from fawa2ly.context.builder import DOCUMENT_LABEL
from fawa2ly.engine import Engine, EngineConfig
from fawa2ly.limits import MAX_DOCUMENT_WORDS
from fawa2ly.server import SessionServer, config_from


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))


def test_documents_belong_to_a_kind_of_call_and_keep_their_names():
    user_settings.add_document("interview", "cv.pdf", "Flutter developer")
    user_settings.add_document("interview", "docs/portfolio.md", "Chat app with Firebase sync")
    user_settings.add_document("meeting", "agenda.md", "Pro plan 20 dollars")
    assert user_settings.list_documents("interview") == [{"name": "cv.pdf", "words": 2},
                                                         {"name": "portfolio.md", "words": 5}]
    assert user_settings.load_documents("meeting") == [("agenda.md", "Pro plan 20 dollars")]

    user_settings.add_document("interview", "cv.pdf", "Senior Flutter developer")   # same name replaces
    assert [d["words"] for d in user_settings.list_documents("interview")] == [3, 5]

    user_settings.remove_document("interview", "cv.pdf")
    assert [d["name"] for d in user_settings.list_documents("interview")] == ["portfolio.md"]
    user_settings.remove_document("interview")
    assert user_settings.list_documents("interview") == []
    assert user_settings.list_documents("meeting"), "removing an interview's documents leaves a meeting call's"


def test_a_document_past_the_budget_is_refused_and_nothing_is_stored():
    user_settings.add_document("interview", "cv.md", " ".join(["word"] * (MAX_DOCUMENT_WORDS - 10)))
    with pytest.raises(ValueError, match="limit"):
        user_settings.add_document("interview", "portfolio.md", " ".join(["word"] * 11))
    assert [d["name"] for d in user_settings.list_documents("interview")] == ["cv.md"]
    user_settings.add_document("interview", "cv.md", "short now")        # replacing frees its words
    user_settings.add_document("interview", "portfolio.md", " ".join(["word"] * 11))
    with pytest.raises(ValueError, match="no readable text"):
        user_settings.add_document("interview", "empty.md", "   ")


def test_the_single_cv_from_before_becomes_an_interview_document():
    os.makedirs(user_settings.app_dir(), exist_ok=True)
    with open(user_settings.cv_path(), "w", encoding="utf-8") as f:
        f.write("Sam, Flutter developer")
    with open(user_settings.settings_path(), "w", encoding="utf-8") as f:
        json.dump({"cv_name": "Sam CV.pdf"}, f)
    assert user_settings.load_documents("interview") == [("Sam CV.pdf", "Sam, Flutter developer")]
    assert not os.path.exists(user_settings.cv_path())
    assert user_settings.load_documents("meeting") == []


def test_settings_carry_the_documents_and_the_budget():
    user_settings.add_document("interview", "cv.txt", "one two three")
    d = user_settings.load().to_json()
    assert d["documents"] == {"interview": [{"name": "cv.txt", "words": 3}], "meeting": []}
    assert d["cv_words"] == 3 and d["document_words_limit"] == MAX_DOCUMENT_WORDS


def test_one_document_reads_as_the_cv_did_and_several_are_named():
    one = ContextBuilder(documents=[("cv.pdf", "Flutter developer")])
    assert one.system() == ContextBuilder(cv_text="Flutter developer").system(), "one file: the prompt from before"
    two = ContextBuilder(mode="meeting", documents=[("agenda.md", "Pro 20"), ("notes.md", "Release is on Thursday")])
    assert (DOCUMENT_LABEL["meeting"] + "\nFrom agenda.md:\nPro 20\n\nFrom notes.md:\nRelease is on Thursday"
            in two.system())
    two.set_documents([])
    assert two.cv_text == "" and "From agenda.md" not in two.system()


def test_a_docx_is_read_with_its_tables_in_order(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("Sam Carter")
    table = document.add_table(rows=2, cols=2)
    for r, (year, role) in enumerate([("2024", "Acme Tax internship"), ("2025", "Chat app")]):
        table.cell(r, 0).text = year
        table.cell(r, 1).text = role
    document.add_paragraph("Skills: Flutter, Firebase")
    path = tmp_path / "cv.docx"
    document.save(str(path))
    expected = "Sam Carter\n2024 | Acme Tax internship\n2025 | Chat app\nSkills: Flutter, Firebase"
    assert load_cv(str(path)) == expected
    assert extract_cv("cv.docx", path.read_bytes()) == expected, "the picker's bytes read the same"


def test_an_old_word_file_says_what_to_do(tmp_path):
    path = tmp_path / "cv.doc"
    path.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(RuntimeError, match="docx"):
        load_cv(str(path))


def test_the_documents_of_the_chosen_kind_reach_the_call():
    user_settings.add_document("meeting", "agenda.md", "Pro plan 20 dollars")
    cfg = config_from(EngineConfig(), {"mode": "meeting"})
    assert cfg.documents == [("agenda.md", "Pro plan 20 dollars")]
    assert config_from(EngineConfig(), {"mode": "interview"}).documents is None, "nothing stored for it"

    e = Engine(EngineConfig(suggest=True, fake_llm=True, mode="meeting", documents=cfg.documents),
               on_event=lambda _e: None)
    e.suggester = e._build_suggester(None)
    assert e.cv_words == 4 and e.mode == "meeting"
    e.set_documents([("a.md", "one two"), ("b.md", "three")])
    assert e.cv_words == 3 and "From b.md:\nthree" in e.suggester.builder.system()


class StubEngine:
    mode = "interview"

    def __init__(self, on_event):
        self.on_event = on_event
        self.received = []
        self._stop = asyncio.Event()

    async def run(self):
        self.on_event({"type": "status", "state": "listening", "cv_words": 0})
        await self._stop.wait()

    def stop(self):
        self._stop.set()

    def set_documents(self, documents):
        self.received.append(documents)


def test_during_a_call_only_documents_of_its_own_kind_reach_it():
    async def go():
        made = []
        server = SessionServer(lambda chosen, on_event: made.append(StubEngine(on_event)) or made[-1],
                               host="127.0.0.1", port=0, autostart={})
        task = asyncio.create_task(server.run())
        while server.port == 0:
            await asyncio.sleep(0)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            async def settings_event():
                while True:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
                    assert not (event["type"] == "status" and event.get("state") == "idle" and made
                                and made[0].received), "an idle status mid-call resets the overlay"
                    if event["type"] == "settings":
                        return event

            await settings_event()                  # what a window gets on connect ends with the saved settings
            data = base64.b64encode(b"Pro plan 20 dollars").decode()
            await ws.send(json.dumps({"cmd": "cv", "mode": "meeting", "name": "agenda.md", "data": data}))
            await settings_event()
            after_meeting = list(made[0].received)
            data = base64.b64encode(b"Flutter developer").decode()
            await ws.send(json.dumps({"cmd": "cv", "mode": "interview", "name": "cv.txt", "data": data}))
            await settings_event()
        made[0].stop()
        await asyncio.wait_for(task, timeout=3)
        return after_meeting, made[0].received

    after_meeting, received = asyncio.run(go())
    assert after_meeting == [], "a meeting document does not belong in a running interview"
    assert received == [[("cv.txt", "Flutter developer")]]
