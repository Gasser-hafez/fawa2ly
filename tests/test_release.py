"""Releases and updates (0.1.0). The installed app compares its own version with latest.json, which
the release workflow writes from the tag. If the versions in the app's config files drift apart, or
the tag names a version the app does not report, every install would offer itself the same update
forever. These checks run in CI, before anyone tags."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir, "packaging"))
from release_notes import notes  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), os.pardir)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def app_version():
    return json.loads(read("app", "src-tauri", "tauri.conf.json"))["version"]


def test_every_place_names_the_same_version():
    v = app_version()
    assert re.search(rf'^version = "{re.escape(v)}"', read("app", "src-tauri", "Cargo.toml"), re.M)
    assert json.loads(read("app", "package.json"))["version"] == v


def test_the_changelog_has_notes_for_this_version():
    assert notes(app_version(), read("CHANGELOG.md"))


def test_notes_are_one_section_only():
    text = "# Changelog\n\n## 0.1.10\n\n- ten\n\n## 0.1.1 (fix)\n\n- one\n- two\n\n## 0.1.0\n\n- first\n"
    assert notes("v0.1.1", text) == "- one\n- two"
    assert notes("0.1.0", text) == "- first"
    assert notes("0.1.10", text) == "- ten"


def test_the_app_only_installs_signed_updates_from_this_repository():
    updater = json.loads(read("app", "src-tauri", "tauri.conf.json"))["plugins"]["updater"]
    assert updater["pubkey"]
    assert updater["endpoints"] == ["https://github.com/Gasser-hafez/fawa2ly/releases/latest/download/latest.json"]
    assert json.loads(read("app", "src-tauri", "tauri.bundle.json"))["bundle"]["createUpdaterArtifacts"] is True


def test_the_workflows_leave_no_git_token_behind_and_ci_only_reads():
    """Audit F5 (safe subset): the release job runs npm, pip and the build after checkout, with the
    signing key in later steps; a write token left in .git/config is reachable by all of them."""
    import yaml
    release = yaml.safe_load(read(".github", "workflows", "release.yml"))
    ci = yaml.safe_load(read(".github", "workflows", "ci.yml"))
    for wf in (release, ci):
        for job in wf["jobs"].values():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout"):
                    assert step.get("with", {}).get("persist-credentials") is False
    assert ci["permissions"] == {"contents": "read"}


def test_notes_print_arabic_on_a_windows_console_code_page():
    import subprocess
    script = os.path.join(os.path.dirname(__file__), os.pardir, "packaging", "release_notes.py")
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    out = subprocess.run([sys.executable, script, app_version()], env=env, capture_output=True)
    assert out.returncode == 0, out.stderr.decode(errors="replace")
