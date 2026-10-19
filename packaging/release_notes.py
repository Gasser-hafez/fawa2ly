"""
Prints the CHANGELOG.md section for one version. The release workflow uses it twice: as the notes
on the GitHub release, and inside latest.json, where the app shows them next to Update.

    python packaging/release_notes.py 0.1.0
"""

import os
import re
import sys

CHANGELOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "CHANGELOG.md")


def notes(version: str, text: str) -> str:
    version = version.lstrip("v")
    m = re.search(rf"^## {re.escape(version)}(?![\w.]).*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip()


if __name__ == "__main__":
    # The notes may hold Arabic; a Windows runner's console code page (cp1252) cannot print it.
    sys.stdout.reconfigure(encoding="utf-8")
    with open(CHANGELOG, encoding="utf-8") as f:
        print(notes(sys.argv[1], f.read()))
