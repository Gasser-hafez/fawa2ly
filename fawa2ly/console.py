"""
Console output that cannot kill a session.

Windows consoles run a legacy code page (cp1256 on this machine, an Arabic Windows), and
`print` raises UnicodeEncodeError on any character outside it. Models emit such characters
routinely: a bullet reading "p95 1.4s -> 180ms" came back with a real arrow, U+2192, and the
exception propagated out of the suggestion callback, through the replay loop, and ended the
run at question 9 of 27 with exit code 0 and no visible error (2026-09-08).

Mid-interview that is the worst possible failure: the assistant dies silently because a model
chose a nicer dash. Logs are written separately with `encoding="utf-8"`, so nothing is lost by
degrading only what is drawn on screen.
"""

from __future__ import annotations

import sys

CLEAR = "\r" + " " * 120 + "\r"


def progress(message: str = "") -> None:
    """print() that flushes.

    A long sweep writing to a redirected stdout shows nothing until it exits, which reads as a
    hang: two healthy runs were killed on that mistake (2026-09-08, 2026-09-09).
    """
    print(message, flush=True)


def safe_console() -> None:
    """Make stdout and stderr replace unencodable characters instead of raising.

    Call once at the start of any command that prints model output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass          # a redirected or wrapped stream that cannot be reconfigured
