# Fawa2ly overlay

The window the user actually looks at. It holds no logic of its own: everything on screen arrives
from the Python engine over the WebSocket in `fawa2ly/server.py`.

- `src/` is the whole UI: one HTML file, one stylesheet, one JS file, no build step and no
  framework. Any browser pointed at it runs the same page Tauri loads.
- `src-tauri/` is the window: always on top, transparent, no frame, dragged by its header bar.
  `src/lib.rs` is a bare Tauri builder on purpose. Logic that lands there cannot be tested by the
  Python suite and cannot run in a browser, which is where this UI is developed.

## Run it

Start the engine in idle mode and let the window's setup screen choose and start the call:

```bash
python -m fawa2ly.server --idle
```

Choices and documents live in `%APPDATA%/Fawa2ly` (`settings.json`, and `documents/<mode>/` for
the interview and meeting documents), so the second call is one click. Each document's remove button
in the setup screen or the settings window deletes it.

For UI work, a recorded session needs no microphone, no Deepgram key and no model spend:

```bash
python -m fawa2ly.server --replay interview7.raw.jsonl --suggest --fake-llm
```

Then either open the page in a browser, which is the fast loop:

```bash
python -m http.server 5173 --directory app/src     # http://127.0.0.1:5173/index.html
```

or build the desktop window, which takes minutes the first time:

```bash
cd app && npm install && npm run tauri dev
```

Use `?port=9000` in the URL if the engine is not on the default 8765.

The engine only accepts pages from the app's own origins (the Tauri window and `http://127.0.0.1:5173` /
`http://localhost:5173`, the port above). Serving the UI from another port needs
`python -m fawa2ly.server --idle --allow-origin http://localhost:8080`.

## Permissions

`src-tauri/capabilities/default.json` grants `core:window:allow-start-dragging` and
`core:window:allow-close` on top of `core:default`. Neither is included by default, and without
them the header bar cannot drag the frameless window and the X cannot close it - silently, with
nothing logged. If either stops working, check that file first.

## Keyboard

Global hotkeys work from any application, including while a call has the focus. Every one can be
changed or cleared in Settings, under Hotkeys (click a box, press the new combination):

| Action | Default |
|---|---|
| Show and hide | `Ctrl+Shift+P` (cannot be cleared: it is the only way back to a hidden window) |
| Click-through | `Ctrl+Shift+L` |
| Pause and resume | `Ctrl+Alt+Space` |
| Previous / next question | `Ctrl+Alt+Left` / `Ctrl+Alt+Right` |
| More lines | `Ctrl+Alt+M` |
| Retry | `Ctrl+Alt+R` |

How it fits together: the engine stores the combinations and refuses a bad one (no modifier, or
one used twice); the overlay hands them to `set_hotkeys` in `src-tauri/src/lib.rs`, which
registers them and reports any the OS refused, usually because another application owns it; the
settings window shows that under the box. Show/hide and click-through are carried out in Rust;
the other actions reach the overlay as a `hotkey` event and do exactly what the button would (a
disabled or hidden button means the hotkey does nothing). Until the overlay connects to the
engine, only the two defaults above are registered. A combination another application holds is
tried again every 15 seconds until it can be taken, and while a new show/hide combination cannot
be taken the previous one keeps working.

A registered combination is taken away from every other application while Fawa2ly runs: with
the defaults, VS Code's command palette on `Ctrl+Shift+P` does not open. Change it in Settings if
that matters. On keyboard layouts where `Ctrl+Alt` acts as AltGr, the `Ctrl+Alt` defaults can
also get in the way of typing characters.

Click-through: the overlay stays visible but the mouse goes to the window underneath, and a
"Click-through" badge shows in the status strip. It is a hotkey because nothing in a window that
ignores the mouse can be clicked, and it is off at every launch.

The background opacity is a slider in Settings, under Display. Only the panel fades, never the
text. The theme is picked there too: Dark, Light, and the six from the design canvas (Aurora,
Teleprompter, Paper, Terminal, Frost, High contrast), each with its own colours and its own way
of showing a new line. All the colours are tokens at the top of `styles.css`; a theme is a block
at the end that redefines them.

## Settings window

The gear opens a wide window (1040 x 720) with one section at a time: call and answers, roles
(including describing your own, typed or spoken), model and speed (with a speed test across every
provider that has a key), documents, listening, API keys, hotkeys, display, and token usage. It is
a second client of the same engine as the overlay, so both always show the same saved choices.

Roles are chosen only here. The 79 shipped roles are listed in sections (each preset's `category`;
roles you described are a section of their own) with a search that matches every typed word against
the title, the role line and the section. The overlay's setup screen names the current role, and its
"Change in Settings" opens this window on Roles. `tests/test_role_catalog.py` loads every preset.

## App language

Display holds the app language: English, or Arabic, which lays both windows and every theme out
right to left. The pages stay written in English; `src/i18n.js` translates what they show from an
English-keyed dictionary, watching the page for changes, with patterns for sentences built around a
number or a name. The question, the suggested lines and role names are `translate="no"`. A new
string in either page needs an entry there, or `tests/test_ui_language.py` fails; a string a script
builds at run time needs one too, but only the browser shows it missing. Messages written by the
engine stay English.

Both windows behave like a desktop app rather than a web page (`src/chrome.js`): no browser context
menu except in text boxes, no reload, print, zoom or developer-tools shortcuts, and the overlay
cannot be maximized (`"maximizable": false` in `tauri.conf.json`).

## Meetings

"Meeting" is the second kind of call, next to Interview. The lines are for taking part in
a work meeting: answer what was asked, with project facts only from the meeting material you load (an
agenda, project notes) or from what was said, and never a promise you have not made. Roles for it
are Team standup, Client meeting and One-to-one with a manager. The buttons at the top of a call
(What should I say?, Recap, Questions to ask) and "Write notes" after it are the parts a meeting
uses most.

## How answers are shaped

Under Answers (the setup screen, or Settings > Call and answers) a call is set to one of four:

- **Full lines to say** - complete sentences you can say word for word, at most 20 words each.
- **Keywords only** - cues of a few words to glance at and say in your own words.
- **Direction** - two or three short moves: what to open with, which example, what to land on.
- **Dynamic** - the model picks one of the three for each question, from what was just asked.

Dynamic is for a call where the questions are not all alike. It leans on full and only reaches for
the others when they clearly fit: keywords when the answer is plainly yours to tell and you need the
points and their order, direction when how you answer matters more than the words, or when your
documents have nothing on what was asked. The card says which shape it chose, next to "Asked", and
it costs no extra wait: the choice is made inside the same answer, not by asking the model twice.

A shape belongs to the card, not the call: More adds to a card in the shape it already has, and
going back to an earlier question brings that question's shape with it. Retry lets the model place
the answer again.

## Actions during a call

Four things you can ask for without waiting to be asked something, each with a hotkey so you never
reach for the mouse mid-call:

| Action | Hotkey | What it gives you |
| --- | --- | --- |
| What should I say? | Ctrl+Alt+S | Lines to say next, from where the conversation is now |
| Recap | Ctrl+Alt+C | The call so far in a few lines, to catch up after looking away |
| Questions to ask | Ctrl+Alt+Q | Questions to put to the other side, to move things forward |
| Shorter | Ctrl+Alt+H | The lines on screen again, in fewer and shorter ones |

They open a card of their own, labelled "On demand" rather than "Asked", and Retry, More and the
card list work on them like any other. Every hotkey can be changed under Settings > Hotkeys.

## Negotiation help

For a call where money comes up, turn on "Negotiation help" under Settings > Call and answers. The
lines then cover the negotiation itself: who should name a figure first, how to counter, and what to
trade when the number cannot move — the start date, the term, the scope, a review in six months.

Two things it will not do. It never invents a market figure, a going rate or what other people are
paid or charged: numbers come only from your documents (the company brief, above, among them) or
from what was said on the call, and when there is no number the line is the move to make instead.
And it never has you claim an offer, a competing price or a deadline you do not have. It also does
not accept or refuse for you — the lines get you the room to decide.

Turn it off for ordinary calls: it applies from the next call, and while it is off the prompt is
exactly the one every other call uses.

## Job description gap analysis

Before an interview, paste the job description under Settings > Documents and press "Compare with my
CV". The model reads it against the documents you have loaded and answers three things: what the job
asks for that your CV supports, with the words of your CV that support it; what it asks for that your
CV does not show; and what to have ready to say, including how to handle each gap honestly. A match
it cannot point at in your CV is dropped before you see it.

The comparison is kept, so reopening Settings shows it again without another request, and the
description itself becomes an interview document, so the call knows what the job asks for. "Remove
it" takes both away. It runs between calls and counts toward usage.

## Speaking pace

With "Also transcribe my microphone" on, a badge in the status strip shows your pace over your last
40 words and, on English calls, your filler words so far ("128 wpm · 1 filler"). It counts talking
time only, so pauses do not make you slow, and it appears after 12 words. On English calls it is
tinted under 110 or over 170 words a minute. Turn it off under Settings > Display ("Show my speaking
pace while I talk"). Filler words come from Deepgram's `filler_words` option, which is English only
and is requested for your microphone alone (`fawa2ly/pace.py`, `engine.stt_params`).

## Live feedback

With "Live feedback on my answers" on (Settings > Call and answers, off by default) and your
microphone transcribed, each of your answers gets a verdict once the other side speaks again: On
point, Missed the point, Ran long or Drifted, with a short note. It shows on that question's card
and, since the next question has usually arrived by then, next to it in the Earlier list. It costs
one request per answer, at most 20 a call, and waits while an answer is being written
(`fawa2ly/feedback.py`).

## Actions on demand

During a call a row of buttons sits at the top of the overlay: "What should I say?", "Recap" and
"Questions to ask". Each opens its own card from the recent conversation, without waiting for a
question to be heard, and works in every mode. "Shorter", next to "More on this", redoes the card on
screen in fewer, shorter lines. Action cards are not counted as questions in the call summary or
judged by live feedback (`Suggester.action`).

## After a call

When a call ends the overlay shows its summary: how long it was, the questions heard and answered,
the median time from the end of a question to its first line, and every question with its own
time. Click a question to see its lines again, and "Call summary" to come back. The engine builds
it from the same events the overlay draws (`fawa2ly/summary.py`) and sends it as one `summary`
event just before `stopped`.

A transcript is kept only when "Save a transcript of each call on this computer" is on (Settings >
Call and answers, off by default): one JSON file per call in `%APPDATA%\Fawa2ly\transcripts`, with
the summary and what both sides said, never audio. Settings shows how many there are and deletes
them. While transcript saving is on, the call's diagnostic logs are also kept in `%APPDATA%\Fawa2ly\sessions`; while it is off, nothing about a call is written to disk.

A saved call can be reviewed: Settings > Calls lists them, and "Review this call" on the ended screen
opens the call there. The saved model reads what both sides said and returns a score out of 10, a
verdict for each question with what was missing, what went well and what to do next time
(`fawa2ly/review.py`). The review is stored in the call's file, so it is paid for once. A call with
none of your own words (microphone not transcribed) is not reviewed, because there is nothing of
yours to judge.

"Write notes" on the same call asks the saved model for notes of what was said: a short summary,
decisions, action items with whoever was named for them, and open questions. They do not need your
microphone, are kept in the call's file like the review, and "Copy as Markdown" puts them on the
clipboard for a document or a CRM.

## Windows prerequisite

Tauri needs the **MSVC** Rust toolchain, not the GNU one. On a `x86_64-pc-windows-gnu` default,
the build fails partway through with `error calling dlltool 'dlltool.exe': program not found`.
Install the Visual Studio Build Tools with the "Desktop development with C++" workload, then:

```bash
rustup toolchain install stable-x86_64-pc-windows-msvc
rustup default stable-x86_64-pc-windows-msvc
```

WebView2 ships with Windows 11, so nothing extra is needed for it here.

## Building the installer

The installed app starts its own engine, so a user needs no Python and no terminal. Build it
from the project folder, engine first:

```bash
python -m pip install -r requirements.txt
```

```bash
python -m PyInstaller packaging/fawa2ly-engine.spec --noconfirm
```

```bash
cd app && npx tauri build --config src-tauri/tauri.bundle.json
```

The first command packages the engine into `dist/fawa2ly-engine/` (about 150 MB). The second
builds the window in release mode and the NSIS installer around it, shipping that folder as the
resource `engine/`; the setup file is `src-tauri/target/release/bundle/nsis/Fawa2ly_0.1.0_x64-setup.exe`
(about 43 MB, with the engine compressed inside). The
installer settings live in `tauri.bundle.json` rather than `tauri.conf.json`, so `cargo build`
and `tauri dev` keep working without `dist/`. The first installer build downloads Tauri's NSIS
toolchain.

What a user needs to know:

- **Windows SmartScreen.** The installer is not code-signed yet (the certificate is deferred), so
  Windows shows "Windows protected your PC". Click **More info**, then **Run anyway**. It installs
  for the current user and needs no administrator rights.
- **Keys.** Open Settings (the gear), paste a Deepgram key and one model provider's key under
  API keys, and save. They stay on that computer, in `%APPDATA%\Fawa2ly\.env`.
- **Devices.** If the meeting app plays through a headset rather than the Windows default, choose
  it under Listening.
- **Logs.** The engine's own output is in `%APPDATA%\Fawa2ly\engine.log`: ask for that file first when nothing
  connects.
- **The health check.** From the install folder, `engine\fawa2ly-engine.exe doctor --offline`
  prints a line per check and needs no key and no network. Ask for it when anything behaves oddly,
  and after any change to the engine's packaging. Without `--offline` it also makes one small
  request to every provider with a key.

When an engine is already listening on port 8765 (one started from a terminal), the app uses it
and does not start its own.
