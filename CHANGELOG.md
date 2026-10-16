# Changelog

## 0.1.0 (first release, pre-release)

First public build.

- Live overlay: listens to the call, detects questions, shows lines you can say out loud.
- Interview and Meeting modes, 60+ roles, documents (txt, Markdown, PDF, DOCX).
- Answer shapes: full lines, keywords, direction, or Dynamic.
- On-demand help (what to say, recap, questions to ask, shorter, more), with hotkeys.
- After the call: summary, optional saved transcript, review, notes, job-description gap analysis.
- Speaking-pace meter and optional live feedback.
- English and Arabic (right to left) interface, 8 themes.
- Bring your own keys: Deepgram for listening; Gemini, Groq, Cerebras or Ollama for the lines.
- Local web pages cannot reach the engine: connections are accepted only from the app's own
  window, and the engine listens on 127.0.0.1 only.
- Nothing is written about a call unless "Save a transcript" is on.

Known limits

- Windows only for live audio.
- The installer is not code-signed, so Windows SmartScreen shows a warning ("More info", then
  "Run anyway"). Check the SHA-256 file next to the download.
- Offline mode (local Whisper) can be chosen in Settings > Listening, but its libraries are not
  packaged into the installer yet, so the choice only works when running from source. Settings
  warns when they are missing.
- The installer was first built by the release workflow and has had little testing.
