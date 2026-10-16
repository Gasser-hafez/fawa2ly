"""
Replay a raw Deepgram log through the pipeline, offline.

    python -m fawa2ly.replay interview6.raw.jsonl                 # print utterances
    python -m fawa2ly.replay interview6.raw.jsonl --out u.jsonl   # also write them (for telemetry)
    python -m fawa2ly.replay interview6.raw.jsonl --realtime      # original pacing
    python -m fawa2ly.replay interview6.raw.jsonl --no-interim    # finals only (pre-2026-09-08 behaviour)
    python -m fawa2ly.replay interview7.raw.jsonl --suggest       # + suggested lines per question (FakeLLM)
    python -m fawa2ly.replay interview7.raw.jsonl --suggest --live --vendor gemini \
        --preset swe --cv docs/sample_cv.txt                       # + a real model

Same code path as live_transcribe.py minus audio and network, so this is how
detector changes get scored against past sessions without spending credits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Optional

from .assist import Suggester, format_assist
from .context import ContextBuilder, Preset, load_cv
from .console import safe_console
from .llm import FakeLLM, OpenAICompatibleLLM, mute_asyncgen_close_noise
from .pipeline import Pipeline, Utterance
from .stt.fake import FakeSTT

try:
    from dotenv import load_dotenv
    load_dotenv(".env")
except ImportError:
    pass


def format_utterance(u: Utterance) -> str:
    src = "@interim" if u.source == "interim" else ""
    q = f", q->fire {u.question_end_to_fire_ms} ms" if u.question_end_to_fire_ms is not None else ""
    who = f"spk{u.speaker}" if u.stream == "other" else f"{u.stream}"
    return (f">> [{u.n}] {who} ({u.trigger}{src}, {u.segments} seg, +{u.gap_ms} ms, "
            f"speech->text {u.speech_to_text_ms} ms{q}) {u.text}")


def assist_path_for(out_path: Optional[str], raw_path) -> str:
    """<out>.assist.jsonl, named after whichever file names this run."""
    stem = out_path or str(raw_path)
    for suffix in (".raw.jsonl", ".jsonl"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem + ".assist.jsonl"


async def replay(path, out_path: Optional[str] = None, realtime: bool = False,
                 quiet: bool = False, interim_detection: bool = True,
                 suggester: Optional[Suggester] = None, turns: bool = True) -> list[Utterance]:
    pipe = Pipeline(on_piece=None if quiet else (lambda spk, piece: print(f"   . spk{spk}: {piece}")),
                    interim_detection=interim_detection, turns=turns)
    out = open(out_path, "w", encoding="utf-8") if out_path else None
    utts: list[Utterance] = []
    try:
        async for ev in FakeSTT(path, realtime=realtime).events():
            if ev.kind == "error" and not quiet:
                print("[stt error]", ev.text)
            for u in pipe.feed(ev):
                if u.speculative:
                    if suggester is not None:
                        await suggester.handle(u)   # asked early, as live; not a logged decision
                    continue
                utts.append(u)
                if not quiet:
                    print(format_utterance(u))
                if out is not None:
                    out.write(json.dumps(u.to_json(), ensure_ascii=False) + "\n")
                if suggester is not None:
                    # awaited inline: replay is not real time, and the model's own latency is
                    # measured separately as ttft_ms, so blocking here changes no reported number
                    await suggester.handle(u)
    finally:
        if out is not None:
            out.close()
    return utts


async def _run(a) -> int:
    safe_console()
    mute_asyncgen_close_noise()
    suggester = assist_log = provider = None
    if a.suggest:
        provider = (OpenAICompatibleLLM.from_env(a.vendor, a.model) if a.live
                    else FakeLLM(first_token_delay=0.05, token_delay=0.002))
        builder = ContextBuilder(preset=Preset.find(a.preset) if a.preset else None,
                                 cv_text=load_cv(a.cv) if a.cv else "")
        assist_log = open(a.assist_out or assist_path_for(a.out, a.raw), "w", encoding="utf-8")
        suggester = Suggester(provider, builder, log=assist_log,
                              on_result=lambda x: print(format_assist(x)))
    try:
        utts = await replay(a.raw, a.out, a.realtime, interim_detection=not a.no_interim,
                            suggester=suggester, turns=not a.sentences)
    finally:
        if provider is not None:
            await provider.close()
        if assist_log is not None:
            assist_log.close()
    print(f"[replay] {len(utts)} utterances" + (f" -> {a.out}" if a.out else ""))
    if suggester is not None:
        print(f"[assist] {suggester.n} question(s) -> {assist_log.name}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("raw", help="raw Deepgram log (<session>.raw.jsonl)")
    ap.add_argument("--out", default=None, help="write utterances as JSONL (same schema as live)")
    ap.add_argument("--realtime", action="store_true", help="sleep between messages like the original")
    ap.add_argument("--no-interim", action="store_true", help="disable early detection on interim results")
    ap.add_argument("--sentences", action="store_true",
                    help="fire on sentences as before 2026-09-10 instead of whole turns (single-stream videos, A/B)")
    ap.add_argument("--suggest", action="store_true", help="ask for lines to say on every detected question")
    ap.add_argument("--live", action="store_true", help="with --suggest, use a real provider not FakeLLM")
    ap.add_argument("--vendor", default="gemini")
    ap.add_argument("--model", default=None)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--cv", default=None)
    ap.add_argument("--assist-out", default=None, help="where to write the assist JSONL")
    return asyncio.run(_run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
