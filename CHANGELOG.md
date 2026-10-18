# Changelog

Each section is the release notes for that version: the release workflow puts it on the GitHub
release, and the app shows it next to **Update and restart**.

## 0.1.0 (first release)

The first public build of Fawa2ly.

- Listens to the call, catches each question, and writes 3 to 5 short lines to say, from your CV
  and your role.
- Interview and Meeting modes, with 60+ role presets, or describe your own role.
- Answer shapes: full lines, keywords, direction, or Dynamic (picks one per question).
- On demand: What should I say?, Recap, Questions to ask, Shorter, More, each with a hotkey.
- After the call: a summary, an optional saved transcript, a scored review, notes, and a
  job-description check before an interview.
- Speaking-pace meter, live feedback on your answers, and negotiation help for calls about pay.
- Bring your own keys for Groq, Gemini, Cerebras, OpenAI, Anthropic, OpenRouter, Mistral,
  DeepSeek, xAI, Together or Fireworks, or run locally with Ollama, LM Studio and others.
- Listening with Deepgram, or offline with local Whisper.
- The Fawa2ly look: the red stretch-toy mascot, whose face shows what the app is doing; four
  themes (Fawa2ly, Daylight, Teleprompter, High contrast); English and Arabic, right to left.
- Settings in five groups, with a title bar in the app's look.
- Updates: the app checks for a new version when it starts and every few hours, shows what is new,
  and installs it with one click. Every update is signed and checked before it runs.

Known limits

- Windows only for live audio.
- The installer is not code-signed, so Windows SmartScreen shows a warning ("More info", then
  "Run anyway"). Check the SHA-256 file next to the download.
- Offline listening (local Whisper) is included but has had little testing in the installed app.
