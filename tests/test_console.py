"""A model bullet with a non-ASCII character must never end a session (2026-09-08)."""

import io
import subprocess
import sys

from fawa2ly.console import CLEAR, safe_console


def test_clear_line_is_a_carriage_return_wipe():
    assert CLEAR.startswith("\r") and CLEAR.endswith("\r") and len(CLEAR) > 100


def test_safe_console_tolerates_streams_that_cannot_be_reconfigured(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())     # no .reconfigure at all
    safe_console()                                         # must not raise

    class Stubborn(io.StringIO):
        def reconfigure(self, **kw):
            raise ValueError("nope")

    monkeypatch.setattr(sys, "stdout", Stubborn())
    safe_console()


def test_printing_an_arrow_survives_a_legacy_code_page():
    """cp1256 cannot encode U+2192; without safe_console this raised and killed the run."""
    code = (
        "import sys\n"
        "sys.stdout.reconfigure(encoding='cp1256')\n"
        "from fawa2ly.console import safe_console\n"
        "safe_console()\n"
        "print('- p95 1.4s → 180ms')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "UnicodeEncodeError" not in r.stderr


def test_without_the_guard_the_same_print_fails():
    """Proves the test above is testing something real."""
    code = ("import sys\n"
            "sys.stdout.reconfigure(encoding='cp1256')\n"
            "print('- p95 1.4s → 180ms')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode != 0 and "UnicodeEncodeError" in r.stderr


def test_progress_flushes_so_long_runs_show_output(capsys):
    from fawa2ly.console import progress
    progress("half way")
    assert capsys.readouterr().out == "half way\n"
