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
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
