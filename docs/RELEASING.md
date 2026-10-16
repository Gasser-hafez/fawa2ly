# Releasing

The Windows installer is built by GitHub Actions on a Windows runner (`.github/workflows/release.yml`),
not by hand, so every release is built from a clean checkout after the tests pass.

1. Update `CHANGELOG.md` (the release notes are taken from it) and, if the version changes, the
   version in `app/src-tauri/tauri.conf.json` and `app/src-tauri/Cargo.toml`.
2. Push to `main` and wait for CI to pass on Ubuntu and Windows.
3. Tag and push:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. The workflow runs the tests, freezes the engine with PyInstaller, runs
   `fawa2ly-engine.exe doctor --offline` on the frozen build, builds the NSIS installer with Tauri,
   writes a SHA-256 file and publishes both as a **pre-release**. Edit the release on GitHub to
   mark it as the latest once someone other than you has installed it and made a call.

To build without publishing, run the workflow by hand (Actions > Release > Run workflow) and
download the artifact.

## If the first run fails

The workflow has never run, so expect to fix something. Likely places: a pinned dependency with no
wheel for the runner's Python (change the pin or `python-version`), a library PyInstaller did not
collect (the doctor step says which), or the Tauri bundle config. The error is in the job log.
