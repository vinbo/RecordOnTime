# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for RecordOnTime."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, get_module_file_attribute

ROOT = Path(SPECPATH)
APP_NAME = "RecordOnTime"

pa_datas, pa_binaries, pa_hiddenimports = collect_all("pyaudiowpatch")
portaudio = get_module_file_attribute("_portaudiowpatch")

datas = [
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "vendor" / "FFMPEG_NOTICE.txt"), "vendor"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "README.md"), "."),
]
binaries = [
    (str(ROOT / "vendor" / "ffmpeg.exe"), "vendor"),
    (portaudio, "."),
]
binaries.extend(pa_binaries)
datas.extend(pa_datas)

a = Analysis(
    ["app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(pa_hiddenimports) + ["_portaudiowpatch"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["imageio_ffmpeg", "PIL", "pytest", "pip", "setuptools"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
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
    icon=str(ROOT / "assets" / "app.ico"),
    version=str(ROOT / "assets" / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
