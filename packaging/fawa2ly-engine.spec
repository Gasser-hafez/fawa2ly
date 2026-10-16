# -*- mode: python ; coding: utf-8 -*-
# The packaged engine. Generated with pyi-makespec 6.6.0, then every path
# anchored to this file's folder (SPECPATH), so the build does not depend on the directory it is
# run from. Build from the project folder:
#
#     python -m PyInstaller packaging/fawa2ly-engine.spec --noconfirm
#
# Output: dist/fawa2ly-engine/ (fawa2ly-engine.exe and _internal/, where the presets land and
# sys._MEIPASS points). The Tauri installer ships that folder as a resource named "engine".
# The imports listed as hidden are the ones the engine makes inside functions; PyInstaller finds
# most of those on its own, and naming them makes a missing one a build error, not a tester's.
#
# Offline transcription (faster-whisper, webrtcvad, onnxruntime) is imported inside functions, so
# it is named below. NOT YET VERIFIED in a frozen build: ctranslate2 ships native libraries that a
# PyInstaller build may not collect on its own. After any change here, run
# `fawa2ly-engine.exe doctor --offline` on the installed app, then a short call with
# --stt-vendor whisper.
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(SPECPATH, "engine_main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "presets"), "presets")],
    hiddenimports=["pyaudiowpatch", "pypdf", "docx", "dotenv", "yaml",
                   # offline transcription, imported inside functions
                   "faster_whisper", "webrtcvad", "onnxruntime"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fawa2ly-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX is not installed, and packing DLLs is a known way to break them
    console=True,               # the app starts it with no window; a console build keeps stdout/stderr valid
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="fawa2ly-engine",
)
