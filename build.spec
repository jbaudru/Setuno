# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Setuno. Produces a Windows .exe when run on Windows, and
a macOS .app bundle when run on macOS (same spec, same command, both platforms).

Build with:  pyinstaller build.spec
"""
import sys

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
    onefile=sys.platform != "darwin",  # macOS .app bundles ship as a folder, not one file
    icon="assets/icon.ico" if sys.platform != "darwin" else "assets/icon.icns",
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Setuno.app",
        icon="assets/icon.icns",
        bundle_identifier="com.bonoob.setuno",
        info_plist={
            "CFBundleName": "Setuno",
            "CFBundleDisplayName": "Setuno",
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": True,
        },
    )

