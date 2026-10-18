# Releasing

The Windows installer is built by GitHub Actions on a Windows runner (`.github/workflows/release.yml`),
not by hand, so every release is built from a clean checkout after the tests pass. Each release is
also an update: installed copies of Fawa2ly find it on their own and offer to install it.

## How updates work

- The app (`app/src-tauri/src/update.rs`) downloads
  `https://github.com/Gasser-hafez/fawa2ly/releases/latest/download/latest.json` shortly after it
  starts and every six hours. If that names a newer version, both windows say so: a card on the
  setup screen (never during a call), a dot on the settings button, and Settings > Look, hotkeys,
  updates, with the release notes.
- Nothing is downloaded until the user presses **Update and restart**. The installer is then
  checked against the public key in `app/src-tauri/tauri.conf.json`; only an installer signed with
  the matching private key is run. The app stops its engine, runs the installer in passive mode
  (a progress bar, no questions) and reopens.
- `latest.json`, the installer's `.sig` and the release notes are written by the release workflow.
  The notes are this version's section of `CHANGELOG.md`.

## Once: the signing key

The private key that signs updates lives on the maintainer's machine in `%USERPROFILE%\.tauri\`
(`fawa2ly-updater.key`, and the public half in `fawa2ly-updater.key.pub`). Keep its password in a
password manager, not in a file beside the key: a copy of that folder alone must not be enough to
sign an update. **Back the key up somewhere safe**, apart from the password. If the key is lost, installed
copies can never verify another update and every user has to reinstall by hand. If it leaks,
anyone holding it could sign an update, so it never goes in the repository.

Give it to GitHub as two repository secrets (Settings > Secrets and variables > Actions > New
repository secret):

| Name | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | the whole contents of `fawa2ly-updater.key` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | the key's password, from your password manager |

The workflow stops with a message naming these secrets if they are missing.

## Every release

1. Raise the version in `app/src-tauri/tauri.conf.json`, `app/src-tauri/Cargo.toml` and
   `app/package.json` (they must match: `tests/test_release.py` checks), and add a section for it at
   the top of `CHANGELOG.md`. Users read that section in the app, so write it for them.
2. Push to `main` and wait for CI to pass.
3. Tag and push. The tag must be `v` plus that version, or the workflow refuses to publish:

   ```bash
   git tag v0.1.1
   git push origin v0.1.1
   ```

4. The workflow runs the tests, freezes the engine with PyInstaller, runs
   `fawa2ly-engine.exe doctor --offline` on the frozen build, builds and signs the NSIS installer,
   writes the SHA-256 file and `latest.json`, and publishes the release as the latest one. Within
   six hours every running copy offers the update, or at once from Settings > Check for updates.

To build without publishing, run the workflow by hand (Actions > Release > Run workflow) and
download the artifact.

## If a run fails

The error is in the job log. Likely places: a pinned dependency with no wheel for the runner's
Python, a library PyInstaller did not collect (the doctor step says which), a missing signing
secret, or a tag that does not match the version.
