# Contributing

Thanks for looking. A few things first, because they decide whether a change fits.

## Project policy

These are decisions, not gaps to be filled. A change that crosses them will be declined whatever
else it does.

1. **No stealth.** The overlay is never hidden from screen sharing, screen recording, proctoring or
   monitoring software, the taskbar, Alt+Tab or Task Manager (for example by setting content
   protection or display affinity on the window). Its only purpose would be to keep the other person
   from seeing the assistant while they are looking.
2. **No hidden second listener.** Nothing streams the call, or the other person's voice or words,
   to someone who does not know they are listening.
3. **No audio is stored, and no transcript is kept unless the user turned that on.**
4. **No telemetry** that leaves the machine without an explicit opt-in per item.
5. **Answers stay speakable.** The overlay shows lines a person can say in one breath. The numbers
   are in `fawa2ly/limits.py` and the prompt and the guardrail both read them.
6. **No third-party recordings or transcripts in the repository.** Test and eval data is written for
   the project or recorded with everyone's consent.

## Setup

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -m fawa2ly.doctor --offline
```

The suite needs no key, network or audio device. Keep it that way: external services sit behind an
interface (`stt/base.py`, `llm/base.py`) with a fake beside it, and a test uses the fake.

## Making a change

- Run the whole suite before you open a pull request, and add a test for the behaviour you changed.
  A bug fix starts with a test that fails.
- **Prompt changes need evidence.** `python -m fawa2ly.relevance` runs the question set
  (`docs/eval/questions.yaml`) and a rating pass compares versions; say what the mean did.
  Detector changes are scored with `python -m fawa2ly.eval` on labeled recordings (see
  `docs/eval/README.md`).
- Latency is a feature. If a change adds work before the first line appears, say how much.
- Match the code around you. Comments explain why, not what; a decision that was wrong once gets a
  sentence saying so.
- UI text is English in the pages, translated in `app/src/i18n.js`; `tests/test_ui_language.py`
  fails when a string has no Arabic entry.

## Good first contributions

- A macOS or Linux audio capture backend (see the README's platform section).
- More role presets in `presets/` (copy one; `tests/test_role_catalog.py` checks the shape).
- Testing the Windows installer and reporting what breaks.
- Translations for the detector's question rules in other languages.
