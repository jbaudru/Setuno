# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Setuno.
Build with:  pyinstaller build.spec
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = (
    collect_submodules("miniaudio")
    + collect_submodules("mutagen")
)
datas = [("playlistbro/ui/icons", "ui/icons")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Setuno",
    debug=False,
    strip=False,
    upx=True,
    console=False,
    onefile=True,
    icon="assets/icon.ico",
)
