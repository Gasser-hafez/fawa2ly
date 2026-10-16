"""
Live capture -> Deepgram streaming STT -> Pipeline (speaker split + question detector)
-> printed and logged utterances.

This is the console front end. The wiring lives in `fawa2ly/engine.py`, so the desktop UI
(M3, `fawa2ly.server`) drives the same engine instead of a copy of it.

Streams:
  other  system audio (WASAPI loopback): whatever Zoom / Meet / YouTube plays. The detector runs here.
  user   your microphone (--user-mic): transcribed for context only, never judged as a question.

Setup (Python 3.10+):
    pip install pyaudiowpatch numpy websockets scipy python-dotenv
    put DEEPGRAM_API_KEY=your_key in a file named .env next to this script
Run:
    python live_transcribe.py --lang en --log interview7.jsonl
        # writes interview7.jsonl (utterances) + interview7.raw.jsonl (every Deepgram message)
        # replay offline: python -m fawa2ly.replay interview7.raw.jsonl
    python live_transcribe.py --lang en --log call1.jsonl --user-mic
        # adds your mic as a second stream: + call1.user.raw.jsonl, utterances tagged stream="user"
    python live_transcribe.py --mic            # transcribe ONLY your mic (A/B tests)
    python live_transcribe.py --lang ar        # force Arabic instead of auto multi
    python live_transcribe.py --seconds 10     # stop by itself (smoke tests)
    python live_transcribe.py --suggest --preset mobile --cv cv.txt   # + lines to say
"""

import argparse
import asyncio
import sys

from fawa2ly.assist import format_assist
from fawa2ly.engine import Engine, EngineConfig

try:  # read DEEPGRAM_API_KEY from a .env file in the project folder if present
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def to_console(event: dict) -> None:
    """The only front-end-specific code left: turn engine events into console text."""
    if event["type"] == "log":
        print(event["text"], end=event.get("end", "\n"), flush=True)
    elif event["type"] == "assist":
        from fawa2ly.assist import Assist
        print(format_assist(Assist(**event["assist"])), flush=True)


def config_from_args(a) -> EngineConfig:
    return EngineConfig(lang=a.lang, log_path=a.log, raw_path=a.raw, mic=a.mic,
                        user_mic=a.user_mic, seconds=a.seconds, suggest=a.suggest,
                        vendor=a.vendor, llm_model=a.llm_model, preset=a.preset, cv=a.cv,
                        fake_llm=a.fake_llm, fake_delay=a.fake_delay,
                        stt_vendor=a.stt_vendor, whisper_model=a.whisper_model)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mic", action="store_true", help="use microphone instead of system loopback (single stream)")
    ap.add_argument("--user-mic", action="store_true", help="add your microphone as a second 'user' stream")
    ap.add_argument("--lang", default="multi", help="multi | ar | en")
    ap.add_argument("--log", default="session.jsonl", help="JSONL file to append utterances to")
    ap.add_argument("--raw", default=None,
                    help="JSONL file to append every raw Deepgram message to (default: <log stem>.raw.jsonl)")
    ap.add_argument("--seconds", type=float, default=0, help="stop automatically after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--suggest", action="store_true", help="show lines to say for each detected question")
    ap.add_argument("--vendor", default="gemini", help="suggestion provider (with --suggest)")
    ap.add_argument("--llm-model", default=None, help="override the provider's model")
    ap.add_argument("--stt-vendor", default="deepgram", choices=("deepgram", "whisper"),
                    help="whisper: transcribe locally (roadmap item 26), no Deepgram key needed")
    ap.add_argument("--whisper-model", default=None,
                    help="faster-whisper size with --stt-vendor whisper (default: small)")
    ap.add_argument("--preset", default=None, help="role preset, e.g. swe / hr / meeting-standup")
    ap.add_argument("--cv", default=None, help="background document for the suggestions")
    ap.add_argument("--fake-llm", action="store_true", help="with --suggest, canned bullets, no network")
    ap.add_argument("--fake-delay", type=float, default=0.0,
                    help="with --fake-llm, seconds before the first line (to see the waiting state)")
    a = ap.parse_args()
    try:
        asyncio.run(Engine(config_from_args(a), on_event=to_console).run())
    except KeyboardInterrupt:
        pass
    except (RuntimeError, ValueError) as e:
        sys.exit(str(e))
