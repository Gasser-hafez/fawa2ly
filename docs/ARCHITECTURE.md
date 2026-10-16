# Architecture

```
 Windows audio ──► engine (Python) ◄── WebSocket, 127.0.0.1 only ──► window (Tauri + plain JS)
 loopback + mic     │
                    ├─ stt/        streaming speech-to-text: Deepgram, or local Whisper
                    ├─ pipeline    STT events → utterances → turns (interim "early fire")
                    ├─ turn/       the question detector
                    ├─ context/    prompt builder (role + documents), output guardrail
                    ├─ llm/        model providers behind one interface, prompts
                    └─ assist      question → streamed lines → records
```

The window holds no logic of its own. Everything on screen arrives from the engine as JSON events
and the window sends commands back (`fawa2ly/server.py` documents the protocol at the top of the
file). The same engine also runs with a console front end (`live_transcribe.py`), in replay
(`--replay`), and under the tests with fakes.

## One data path

1. Audio from the speakers (the other side) and, optionally, the microphone (you) is resampled to
   16 kHz mono and sent to a speech-to-text provider as two separate streams.
2. The provider returns interim and final results. `pipeline.py` turns them into utterances,
   holds a turn open for about a second of silence, and asks `turn/detector.py` whether the other
   side's text is a question or a request.
3. `context/builder.py` builds the request. Everything stable (the prompt, the role, your
   documents) is the system prompt so a provider's cache can match it; the last few turns and the
   question go in the user message.
4. `llm/` streams the reply. `assist.py` passes each finished line through `context/guardrail.py`
   as it arrives, so a line is never on screen and then dropped from the record.
5. `engine.py` emits `question`, `line` and `assist` events. Every stage is timestamped; the
   product metric is the time from the end of the question to the first line on screen.

## Interfaces and fakes

STT (`stt/base.py`), the model (`llm/base.py`) have a base class and a fake (`stt/fake.py`,
`llm/fake.py`). Everything is testable offline, and a recorded `<session>.raw.jsonl` replays through
the real pipeline at its original pace. Adding a provider is one file plus a row in the vendor table
(`llm/openai_compat.py`) or a class next to `stt/deepgram.py`.

## Where things live

| | |
|---|---|
| `fawa2ly/limits.py` | The product rules (line length, line count). The prompt and the guardrail both read it. |
| `fawa2ly/llm/prompts.py` | The prompts, with a note for each change and why. |
| `fawa2ly/settings.py` | What is saved: settings, documents, keys, transcripts, hotkeys, themes. |
| `fawa2ly/summary.py`, `review.py`, `jobfit.py`, `feedback.py`, `pace.py` | After-call summary and review, job-description gap analysis, live feedback, speaking pace. |
| `presets/` | Role presets (YAML). |
| `app/src/` | The window: `index.html`/`main.js` (overlay), `settings.html`/`settings.js`, `i18n.js`. |
| `app/src-tauri/` | The Rust shell: window, global hotkeys, starting the packaged engine. |
| `packaging/` | PyInstaller spec for the engine, and its entry point with the `doctor` command. |
| `docs/eval/` | The question set, scripts for synthetic calls, and the labeling rule. |

## Measuring

`fawa2ly.telemetry` prints p50/p95 per stage from the logs; `fawa2ly.eval` scores the detector on
labeled recordings; `fawa2ly.relevance` runs the question set through a model and records ratings;
`fawa2ly.bench` and `fawa2ly.speedtest` compare models on time to first line.
