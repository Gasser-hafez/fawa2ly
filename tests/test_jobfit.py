"""Job description gap analysis (roadmap item 23, 2026-09-12): the model reads a pasted job
description against the CV and says what matches, what is missing and what to prepare, and the
description becomes an interview document for the call.

The model is a stub that returns a fixed reply, so what is under test is what this side does with
it: the request it builds, what it refuses, what it drops, and what the call's prompt then says.
"""

import asyncio
import json

import pytest

from fawa2ly import jobfit
from fawa2ly import settings as user_settings
from fawa2ly.context import ContextBuilder
from fawa2ly.context.builder import JOB_DESCRIPTION_NAME, JOB_DESCRIPTION_NOTE
from fawa2ly.llm.base import SuggestionEvent, SuggestionProvider

CV = ("Sam Carter. Flutter developer, two years. Built a chat app with an offline message queue "
      "and Firebase sync. Laravel internship at Acme Tax. Arabic and English.")

JD = ("We are hiring a senior mobile engineer to own our Flutter application end to end. "
      "You will ship features to production weekly, own release management on the App Store and "
      "Play Store, mentor two junior engineers, and work with our backend team on API design. "
      "Five years of mobile experience and fluent English are required. Kubernetes is a plus.")

REPLY = json.dumps({
    "summary": "A good Flutter fit, short on seniority and release ownership.",
    "matches": [{"asks": "Own the Flutter application", "evidence": "Flutter developer, two years, built a chat app"},
                {"asks": "Fluent English", "evidence": "Arabic and English"},
                {"asks": "Kubernetes is a plus", "evidence": ""}],
    "missing": ["Five years of mobile experience", "Release management on the stores", "Mentoring junior engineers"],
    "prepare": ["Lead with the offline queue as ownership", "Have an answer ready for the seniority gap"],
})


class StubLLM(SuggestionProvider):
    name, model = "stub", "stub-1"

    def __init__(self, text=REPLY, error=None):
        self.text, self.error, self.seen = text, error, []

    async def suggest(self, req):
        self.seen.append(req)
        if self.error:
            yield SuggestionEvent(kind="error", t=0.0, text=self.error)
            return
        yield SuggestionEvent(kind="first_token", t=0.0, text=self.text)
        yield SuggestionEvent(kind="done", t=1.0, model=self.model)


@pytest.fixture(autouse=True)
def app_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_the_comparison_is_kept_and_the_description_becomes_an_interview_document():
    user_settings.add_document("interview", "cv.txt", CV)
    llm = StubLLM()

    result = asyncio.run(jobfit.analyze(JD, llm))

    assert result["summary"].startswith("A good Flutter fit")
    assert [m["asks"] for m in result["matches"]] == ["Own the Flutter application", "Fluent English"], \
        "a match the model could not point at in the CV is not a match"
    assert result["missing"][0] == "Five years of mobile experience"
    assert result["prepare"] and result["words"] == len(JD.split()) and result["cv"] == ["cv.txt"]

    stored = {name: text for name, text in user_settings.load_documents("interview")}
    assert stored[JOB_DESCRIPTION_NAME] == JD, "the call reads the description too"
    assert jobfit.load()["summary"] == result["summary"], "reading it again costs no request"

    sent = llm.seen[0].user
    assert JD in sent and CV in sent and sent.index(JD) < sent.index(CV)


def test_a_second_comparison_does_not_read_the_job_description_as_the_cv():
    """Stored as a document, the description would otherwise be compared with itself and match
    everything."""
    user_settings.add_document("interview", "cv.txt", CV)
    llm = StubLLM()
    asyncio.run(jobfit.analyze(JD, llm))
    asyncio.run(jobfit.analyze(JD, llm))

    assert jobfit.cv_documents() == [("cv.txt", CV)]
    assert JD not in llm.seen[1].user.split("The candidate's CV:")[1]


def test_it_refuses_a_line_of_text_and_an_empty_cv_rather_than_asking_the_model():
    llm = StubLLM()
    user_settings.add_document("interview", "cv.txt", CV)
    with pytest.raises(ValueError, match="at least"):
        asyncio.run(jobfit.analyze("Senior mobile engineer wanted", llm))

    user_settings.remove_document("interview")
    with pytest.raises(ValueError, match="load your CV first"):
        asyncio.run(jobfit.analyze(JD, llm))
    assert llm.seen == [], "neither is worth a request"


def test_a_model_that_fails_or_answers_with_nothing_usable_leaves_no_document_behind():
    user_settings.add_document("interview", "cv.txt", CV)
    with pytest.raises(ValueError, match="could not compare"):
        asyncio.run(jobfit.analyze(JD, StubLLM(error="429 rate limited")))
    with pytest.raises(ValueError, match="did not return a comparison"):
        asyncio.run(jobfit.analyze(JD, StubLLM(text="Sure! Here is the comparison:")))
    with pytest.raises(ValueError, match="not valid JSON"):
        asyncio.run(jobfit.analyze(JD, StubLLM(text='{summary: not really json}')))
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(jobfit.analyze(JD, StubLLM(text=json.dumps({"summary": "", "matches": []}))))

    assert JOB_DESCRIPTION_NAME not in dict(user_settings.load_documents("interview"))
    assert jobfit.load() is None


def test_the_interview_prompt_says_which_document_is_the_job_description():
    plain = ContextBuilder(mode="interview", documents=[("cv.txt", CV)])
    assert not plain.has_job_description and JOB_DESCRIPTION_NOTE not in plain.system(), \
        "an interview without one sends the rated prompt unchanged"

    with_jd = ContextBuilder(mode="interview", documents=[("cv.txt", CV), (JOB_DESCRIPTION_NAME, JD)])
    assert with_jd.has_job_description and JOB_DESCRIPTION_NOTE in with_jd.system()
    assert JD in with_jd.system(), "the description is still a document the call can use"

    meeting = ContextBuilder(mode="meeting", documents=[(JOB_DESCRIPTION_NAME, JD)])
    assert not meeting.has_job_description, "a meeting's documents are meeting material"


def test_clearing_forgets_the_comparison_and_the_document():
    user_settings.add_document("interview", "cv.txt", CV)
    asyncio.run(jobfit.analyze(JD, StubLLM()))

    jobfit.clear()

    assert jobfit.load() is None
    assert [name for name, _ in user_settings.load_documents("interview")] == ["cv.txt"], \
        "the CV stays; only the job description goes"
    jobfit.clear()          # a second time is not an error


def test_the_window_asks_for_it_reads_it_and_clears_it_over_the_protocol():
    from test_server import connect, make_server, recv_json, start, wait_for

    user_settings.add_document("interview", "cv.txt", CV)

    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await recv_json(ws)                                          # hello
            await ws.send(json.dumps({"cmd": "jobfit"}))                 # what the window asks on opening
            first = await wait_for(ws, lambda e: e.get("type") == "jobfit")
            assert first["jobfit"] is None

            jobfit.save({"summary": "kept from before", "matches": [], "missing": [], "prepare": []})
            await ws.send(json.dumps({"cmd": "jobfit"}))
            again = await wait_for(ws, lambda e: e.get("type") == "jobfit")
            assert again["jobfit"]["summary"] == "kept from before"

            await ws.send(json.dumps({"cmd": "jobfit", "description": "too short"}))
            err = await wait_for(ws, lambda e: e["type"] == "error")
            assert "at least" in err["text"]

            await ws.send(json.dumps({"cmd": "jobfit", "clear": True}))
            gone = await wait_for(ws, lambda e: e.get("type") == "jobfit")
            assert gone["jobfit"] is None and jobfit.load() is None
        task.cancel()

    asyncio.run(go())


def test_a_kept_comparison_says_when_its_document_was_removed_on_its_own():
    """The job description can be removed from the documents list like any other document; the
    comparison then stands as a record of something the next call will not see."""
    user_settings.add_document("interview", "cv.txt", CV)
    assert asyncio.run(jobfit.analyze(JD, StubLLM()))["stored"] is True
    assert jobfit.load()["stored"] is True

    user_settings.remove_document("interview", JOB_DESCRIPTION_NAME)

    assert jobfit.load()["stored"] is False, "the window has to stop saying it is used next time"
    assert jobfit.load()["summary"], "the comparison itself is not thrown away"


def test_a_description_that_would_not_fit_is_refused_before_the_model_is_asked():
    """It is stored as an interview document at the end, and that store is refused over the word
    budget. Asking first is the difference between being told now and being told after paying for a
    comparison that is then thrown away."""
    user_settings.add_document("interview", "portfolio.txt", " ".join(["word"] * 5980))
    llm = StubLLM()

    with pytest.raises(ValueError, match="the limit is 6000"):
        asyncio.run(jobfit.analyze(JD, llm))

    assert llm.seen == [], "the model was never asked"
    assert jobfit.load() is None


def test_clearing_during_a_call_reaches_the_running_engine():
    """Only the comparison is refused during a call; clearing is allowed, and it removes an interview
    document. A call that kept answering from a job description the user had just taken away would
    be using something they can no longer see."""
    from test_server import connect, make_server, recv_json, start, wait_for

    user_settings.add_document("interview", "cv.txt", CV)
    user_settings.add_document("interview", JOB_DESCRIPTION_NAME, JD)
    jobfit.save({"summary": "from before", "matches": [], "missing": [], "prepare": []})

    async def go():
        server = make_server()
        task = await start(server)
        async with connect(f"ws://127.0.0.1:{server.port}") as ws:
            await recv_json(ws)                                          # hello
            await ws.send(json.dumps({"cmd": "start", "settings": {"mode": "interview"}}))
            await wait_for(ws, lambda e: e.get("state") == "listening")

            given = []
            server.engine.mode = "interview"
            server.engine.set_documents = given.append
            await ws.send(json.dumps({"cmd": "jobfit", "clear": True}))
            await wait_for(ws, lambda e: e.get("type") == "jobfit")

            assert given and [name for name, _ in given[-1]] == ["cv.txt"], \
                "the running call was handed the documents without the job description"
            await ws.send(json.dumps({"cmd": "stop"}))
        task.cancel()

    asyncio.run(go())
