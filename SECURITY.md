# Security

## Reporting a problem

Please report vulnerabilities privately, through GitHub's "Report a vulnerability" (Security tab of
this repository), not in a public issue. Include what you did, what you expected and what happened.
You should get an answer within a week.

## How the app is built, so you can judge it

- The engine is a local process that serves a WebSocket on `127.0.0.1:8765` to the overlay and the
  settings window. It refuses to bind to any other address.
- **Web pages cannot talk to it.** A browser lets any page open a WebSocket to localhost, so the
  engine checks the `Origin` of every connection and accepts only the app's own windows (the Tauri
  webview and the local dev server) plus connections that send no `Origin` at all, which a browser
  page cannot do. It also checks that the `Host` header is a loopback name, which stops DNS
  rebinding. Extra origins must be named explicitly (`--allow-origin`). Tests:
  `tests/test_server_security.py`.
- **Other programs on the same machine** can still connect, because there is no token. A process
  running as you can already read the app folder, the keys in it and the transcripts, so this adds
  little, but treat the machine as trusted.
- API keys are stored in plain text in `.env` inside the app folder (`%APPDATA%\Fawa2ly`). The
  settings window is only ever told whether a key is set, never its value.
- Documents and, if you enable saving, transcripts are stored unencrypted in the app folder.
- Call audio is never written to disk.

## Out of scope

Anything that needs an attacker who already controls your user account, and findings about the
model providers' own services.
