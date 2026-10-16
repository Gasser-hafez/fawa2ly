import asyncio
import json

import pytest

from fawa2ly.stt import FakeSTT, parse_message
from fawa2ly.stt.deepgram import build_url
from fawa2ly.stt.whisper_local import LocalWhisperSTT


def results(transcript, words, is_final=True):
    return {"type": "Results", "is_final": is_final,
            "channel": {"alternatives": [{"transcript": transcript, "words": words}]}}


def test_parse_final_with_words():
    d = results("Hello there.", [
        {"word": "hello", "punctuated_word": "Hello", "start": 1.0, "end": 1.3, "speaker": 0},
        {"word": "there", "punctuated_word": "there.", "start": 1.4, "end": 1.8, "speaker": 0},
    ])
    ev = parse_message(d, t_recv=100.0, t_rel=2.5)
    assert ev.kind == "final"
    assert ev.text == "Hello there."
    assert [w.text for w in ev.words] == ["Hello", "there."]
    assert ev.words[0].speaker == 0
    assert ev.audio_end == 1.8
    assert (ev.t_recv, ev.t_rel) == (100.0, 2.5)


def test_parse_interim_and_missing_optionals():
    ev = parse_message(results("hel", [{"word": "hel", "start": 1.0, "end": 1.2}], is_final=False), 1.0, None)
    assert ev.kind == "interim"
    assert ev.words[0].text == "hel"          # falls back to plain word
    assert ev.words[0].speaker is None
    assert ev.t_rel is None


def test_parse_ignores_empty_and_unknown():
    assert parse_message(results("", []), 1.0, 1.0) is None
    assert parse_message({"type": "Metadata", "request_id": "x"}, 1.0, 1.0) is None
    assert parse_message({"type": "SpeechStarted"}, 1.0, 1.0) is None


def test_parse_utterance_end_and_error():
    ue = parse_message({"type": "UtteranceEnd", "last_word_end": 4.2}, 10.0, 5.0)
    assert ue.kind == "utterance_end" and ue.words == [] and ue.audio_end is None
    err = parse_message({"type": "Error", "description": "bad"}, 10.0, 5.0)
    assert err.kind == "error" and "bad" in err.text


def test_build_url_defaults_and_override():
    url = build_url("en")
    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert "model=nova-3" in url and "language=en" in url and "endpointing=300" in url
    assert "endpointing=150" in build_url("en", endpointing=150)


def test_fake_stt_replays_raw_log_in_order(tmp_path):
    p = tmp_path / "s.raw.jsonl"
    lines = [
        {"t_recv": 1.0, "t_rel": None, "msg": {"type": "Metadata"}},
        {"t_recv": 2.0, "t_rel": 0.5, "msg": results("hi", [{"word": "hi", "start": 0.1, "end": 0.3}], False)},
        {"t_recv": 2.5, "t_rel": 1.0, "msg": results("Hi.", [{"word": "hi", "punctuated_word": "Hi.", "start": 0.1, "end": 0.3}])},
        {"t_recv": 3.5, "t_rel": 2.0, "msg": {"type": "UtteranceEnd", "last_word_end": 0.3}},
    ]
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n\n", encoding="utf-8")

    async def collect():
        return [ev async for ev in FakeSTT(p).events()]

    evs = asyncio.run(collect())
    assert [e.kind for e in evs] == ["interim", "final", "utterance_end"]
    assert [e.t_recv for e in evs] == [2.0, 2.5, 3.5]
    assert evs[1].t_rel == 1.0 and evs[1].text == "Hi."


class FakeWs:
    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def test_audio_clock_follows_audio_sent_not_wall_time():
    from fawa2ly.stt.deepgram import DeepgramSTT
    stt = DeepgramSTT("key", "en")
    stt._ws = FakeWs()
    chunk = bytes(1600)                                  # 50 ms of 16 kHz int16 mono
    assert stt.audio_clock(100.0) is None
    asyncio.run(stt.send_audio(chunk, t_capture=100.0))
    assert stt.t_stream_start == 100.0
    assert stt.audio_clock(100.35) == pytest.approx(0.35)   # 0.05 s sent + 0.30 s elapsed
    # capture stalls for 2 s (nothing playing), then resumes: the clock must NOT jump by 2 s
    asyncio.run(stt.send_audio(chunk, t_capture=102.0))
    assert stt.audio_clock(102.45) == pytest.approx(0.50)   # 0.10 s sent + 0.40 s elapsed
    assert len(stt._ws.sent) == 2


def test_audio_clock_logs_anchor_lines_every_5s(tmp_path):
    import io
    from fawa2ly.stt.deepgram import DeepgramSTT
    raw = io.StringIO()
    stt = DeepgramSTT("key", "en", raw_log=raw)
    stt._ws = FakeWs()
    for i in range(0, 220):                              # 11 s of audio
        asyncio.run(stt.send_audio(bytes(1600), t_capture=100.0 + i * 0.05))
    lines = [json.loads(l) for l in raw.getvalue().splitlines()]
    assert [l["msg"]["type"] for l in lines] == ["AudioClock"] * 3
    assert [l["msg"]["audio_s"] for l in lines] == [pytest.approx(x, abs=0.06) for x in (0.05, 5.05, 10.05)]
    assert parse_message(lines[0]["msg"], 1.0, 1.0) is None   # ignored by the pipeline


# ---- LocalWhisperSTT --------------------------------
# webrtcvad and faster-whisper are never imported here: a fake VAD (non-zero bytes = speech) and a
# fake model (canned segments) exercise the state machine - framing, endpointing, utterance_end,
# the Deepgram-shaped raw log - with no download and no C extension required to run the suite.

FRAME_BYTES = 960   # 30 ms at 16 kHz mono int16
SPEECH_FRAME = bytes([1, 0]) * (FRAME_BYTES // 2)
SILENCE_FRAME = bytes(FRAME_BYTES)


class ByteVad:
    def is_speech(self, frame, rate):
        return any(frame)


class Seg:
    def __init__(self, text, words):
        self.text = text
        self.words = words


class Wd:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class CannedModel:
    calls = 0

    def transcribe(self, audio, **kw):
        CannedModel.calls += 1
        return [Seg("Hi there.", [Wd("Hi", 0.0, 0.3), Wd("there.", 0.3, 0.6)])], None


async def _feed(stt, frames):
    for f in frames:
        await stt.send_audio(f)


def test_whisper_fires_final_on_endpointing_then_utterance_end():
    CannedModel.calls = 0
    model = CannedModel()

    async def go():
        async with LocalWhisperSTT("en", vad=ByteVad(), model=model,
                                   endpointing_ms=60, utterance_end_ms=150) as stt:
            # 8 speech frames (240 ms, over MIN_SPEECH_MS once trailing silence is added), then
            # silence: 2 frames close the segment (endpointing_ms), 3 more reach utterance_end_ms
            # (150 ms total, from the same last-speech point).
            await _feed(stt, [SPEECH_FRAME] * 8 + [SILENCE_FRAME] * 5)
            gen = stt.events()
            final = await asyncio.wait_for(gen.__anext__(), timeout=1)
            ended = await asyncio.wait_for(gen.__anext__(), timeout=1)
        return final, ended

    final, ended = asyncio.run(go())
    assert final.kind == "final" and final.text == "Hi there." and final.endpointed
    assert [w.text for w in final.words] == ["Hi", "there."]
    assert (final.words[0].start, final.words[0].end) == (pytest.approx(0.0), pytest.approx(0.3))
    assert ended.kind == "utterance_end"
    assert CannedModel.calls == 1


def test_whisper_drops_a_blip_shorter_than_min_speech():
    model = CannedModel()
    model.calls = 0

    async def go():
        async with LocalWhisperSTT("en", vad=ByteVad(), model=model,
                                   endpointing_ms=60, utterance_end_ms=999) as stt:
            # one 30 ms speech frame is well under MIN_SPEECH_MS: closed, never transcribed.
            await _feed(stt, [SPEECH_FRAME] + [SILENCE_FRAME] * 3)
            await asyncio.sleep(0)   # let any queued work run
        return stt

    asyncio.run(go())
    assert model.calls == 0


def test_whisper_raw_log_replays_through_fakestt():
    import io
    raw = io.StringIO()
    model = CannedModel()
    model.calls = 0

    async def go():
        async with LocalWhisperSTT("en", raw_log=raw, vad=ByteVad(), model=model,
                                   endpointing_ms=60, utterance_end_ms=150) as stt:
            await _feed(stt, [SPEECH_FRAME] * 8 + [SILENCE_FRAME] * 5)
            gen = stt.events()
            await asyncio.wait_for(gen.__anext__(), timeout=1)
            await asyncio.wait_for(gen.__anext__(), timeout=1)

    asyncio.run(go())
    lines = [json.loads(l) for l in raw.getvalue().splitlines() if l]
    assert [l["msg"]["type"] for l in lines] == ["Results", "UtteranceEnd"]

    path = _write_tmp(raw.getvalue())

    async def replay():
        return [ev async for ev in FakeSTT(path).events()]

    replayed = asyncio.run(replay())
    assert [ev.kind for ev in replayed] == ["final", "utterance_end"]
    assert replayed[0].text == "Hi there."


def _write_tmp(text: str) -> str:
    import tempfile
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".raw.jsonl", delete=False, encoding="utf-8")
    f.write(text)
    f.close()
    return f.name
