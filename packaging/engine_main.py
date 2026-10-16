"""
Entry point of the packaged engine.

PyInstaller freezes this file, with the `fawa2ly` package and the role presets, into
`dist/fawa2ly-engine/fawa2ly-engine.exe`; the Tauri app ships that folder as a resource and
starts it hidden when the overlay opens (app/src-tauri/src/lib.rs). The arguments are the
server's own (`python -m fawa2ly.server --help`); the app passes `--idle --sessions <folder>`.

`fawa2ly-engine.exe doctor --offline` runs the health check instead (fawa2ly/doctor.py). A
packaged build can break in ways no test on a developer's machine sees - a library whose binary
was not collected is the standing example - and a tester with an installer has no Python to run
the check with. The word is taken off the front and everything after it is the doctor's own
arguments; anything else is the server's, exactly as before.
"""

import sys

from fawa2ly.doctor import main as doctor_main
from fawa2ly.server import main as server_main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        sys.exit(doctor_main(sys.argv[2:]))
    sys.exit(server_main())
