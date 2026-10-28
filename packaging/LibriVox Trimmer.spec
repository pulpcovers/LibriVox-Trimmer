# -*- mode: python ; coding: utf-8 -*-
# Build with packaging/build_release.ps1 (run from anywhere); paths are relative to this file.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
SOURCE = ROOT / "src"

datas = []
binaries = []
hiddenimports = []
for package in (
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "imageio_ffmpeg",
    "av",
    "tokenizers",
    "huggingface_hub",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(SOURCE / "librivox_trimmer" / "__main__.py")],
    pathex=[str(SOURCE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# UPX stays off: compressed executables are flagged by antivirus far more often.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LibriVox Trimmer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name="LibriVox Trimmer",
)
