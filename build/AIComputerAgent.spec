# PyInstaller spec file.
# Build (on Windows, from the project root):
#   pyinstaller build/AIComputerAgent.spec
#
# Output: dist/AIComputerAgent/AIComputerAgent.exe (onedir - recommended,
# starts fast) or edit EXE(..., onefile=True) below for a single .exe.

# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

block_cipher = None
# NOTE: .spec files are exec()'d by PyInstaller, not imported as a normal
# module - `__file__` is NOT defined in that context (this used to be a
# bug here that broke the build with "NameError: name '__file__' is not
# defined"). PyInstaller instead injects `SPECPATH`, the directory
# containing this .spec file, into the exec namespace - that's the
# correct way to find the project root from inside a spec file.
PROJECT_ROOT = Path(SPECPATH).resolve().parent

hidden_imports = [
    "pyttsx3.drivers",
    "pyttsx3.drivers.sapi5",
    "win32timezone",
    "speech_recognition",
]

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AIComputerAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,   # no console window - this is a GUI app
    icon=None,       # put an .ico path here if you have one, e.g. "assets/app.ico"
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="AIComputerAgent",
)
