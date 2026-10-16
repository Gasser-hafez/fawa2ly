# Fawa2ly

A desktop overlay that listens to a live call, notices when the other side asks you something, and
shows a few lines you can say out loud, within a couple of seconds. It runs on your own API keys,
keeps your documents on your machine, and records nothing unless you ask it to.

> **Status: early.** The engine and its 400+ tests are solid, and the overlay has been used on
> real calls, but the Windows installer has not yet been tried by anyone outside the author, and
> offline mode has not been benchmarked on a CPU-only machine. Expect rough edges.

## What it does

- **Hears the call.** Windows system audio (WASAPI loopback) is the other side; your microphone is
  optional context. Each side is its own stream, so there is no guessing who spoke.
- **Notices questions.** A heuristic detector closes a turn after about a second of silence (and can
  ask the model early, while the question is still being finished). English and Egyptian/Modern
  Standard Arabic are supported.
- **Writes what you could say.** 3 to 5 lines of at most 20 words, ordered so that read in sequence
  they form one answer. Pick the shape: full lines, keywords, direction, or let the model choose per
  question. A guardrail drops anything too long, duplicated, or shaped like a CV heading.
- **Grounded in your documents.** CVs, portfolios, agendas (txt, Markdown, PDF, DOCX). Without a
  document it says so, and writes how to approach the question instead of inventing facts.
- **Two kinds of call.** Interview and Meeting, each with its own prompt and 60+ role presets (add
  your own by describing it in Settings).
- **On demand.** What should I say now, Recap, Questions to ask, Shorter, More; each with a hotkey.
- **After the call.** A summary, an optional saved transcript, a scored review, notes with decisions
  and action items, and a job-description gap analysis before an interview.
- **Offline mode.** Local Whisper transcription (no Deepgram key) plus a local model through Ollama:
  nothing leaves the machine. Choose it in Settings > Listening (Transcription, and a model size) or with
  `--stt-vendor whisper`. It is slower than Deepgram by design (a question is judged only once you
  stop speaking) and is not yet in the installer build; from source, install the requirements first.
- **Overlay.** Always on top, adjustable opacity, click-through, 8 themes, English and Arabic (right
  to left), global hotkeys.

## Privacy, stated plainly

| What | Where it goes |
|---|---|
| Call audio | Streamed to Deepgram **only** if you use Deepgram for transcription. Never stored. With offline mode it stays on the machine. |
| What was said, your documents | Sent to the model provider you chose (Gemini, Groq, Cerebras, or your own Ollama) to write the lines. |
| Settings, documents, API keys | Plain files in `%APPDATA%\Fawa2ly` (`~/.fawa2ly` elsewhere). Keys are in `.env` there. |
| Transcripts and call logs | Written **only** if you turn on "Save a transcript of each call". Off by default. |
| First use of offline mode | The Whisper model downloads from Hugging Face (about 480 MB). |
| Telemetry | None. The app does not phone home. |

The engine listens on `127.0.0.1` only, accepts connections only from its own window, and has no
authentication beyond that: see [SECURITY.md](SECURITY.md).

## What it will not do

Fawa2ly shows its overlay to you, and the person you are talking to can be told it is there. It
will not hide itself from screen sharing, recordings, proctoring software or the taskbar, and it will
not stream the call to a hidden second listener. Those are project policy, not missing features:
see [CONTRIBUTING.md](CONTRIBUTING.md). Recording and assistance rules differ by country, employer
and interview; know the ones that apply to you before you use it.

## Run it from source

You need Python 3.11+ and, for the desktop window, Rust (MSVC toolchain on Windows) and Node.

```bash
python -m pip install -r requirements.txt
python -m fawa2ly.doctor --offline          # checks the setup with no key and no network
```

Put keys in a `.env` file in the project folder, or paste them into Settings later:

```
DEEPGRAM_API_KEY=...        # listening (skip it with offline mode)
GEMINI_API_KEY=...          # or GROQ_API_KEY / CEREBRAS_API_KEY; Ollama needs no key
```

Start the engine and the window:

```bash
python -m fawa2ly.server --idle             # waits for the window to start a call
cd app && npm install && npm run tauri dev
```

The UI is plain HTML, CSS and JS, so for UI work you can skip Tauri entirely and use a recorded
call with no microphone, key or model spend:

```bash
python -m fawa2ly.synth docs/eval/scripts/flutter-junior-en.yaml -o demo.raw.jsonl
python -m fawa2ly.server --replay demo.raw.jsonl --suggest --fake-llm
python -m http.server 5173 --directory app/src      # then open http://127.0.0.1:5173/index.html
```

More on the window, hotkeys and the installer: [app/README.md](app/README.md). How the pieces fit:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```bash
python -m pytest -q
```

About 420 tests, none needing a network, a key or an audio device. Off Windows, the one test that
needs the Windows audio library is skipped.

## Platform support

Windows 10/11 for live audio. The engine, the tests and the replay tools run anywhere Python does.
A macOS or Linux capture backend is the most useful contribution anyone could make: the capture code
is in `fawa2ly/engine.py` and is not yet behind an interface.

## Terms and license

Using the app is covered by the [Terms of Use](TERMS.md) (a draft that needs review and filling in).
No source-code license has been chosen yet, so until one is added the code is visible but no
permission to copy, modify or redistribute it is granted.

## Installing

Download the latest `Fawa2ly_*_x64-setup.exe` from the Releases page, with its `.sha256` file to
check it. The installer is not code-signed, so Windows shows "Windows protected your PC": choose
**More info**, then **Run anyway**. It installs for the current user and needs no administrator
rights. Releases are built by `.github/workflows/release.yml`; see [docs/RELEASING.md](docs/RELEASING.md).
