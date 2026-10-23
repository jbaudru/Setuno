# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Setuno. Produces a Windows .exe when run on Windows, and
a macOS .app bundle when run on macOS (same spec, same command, both platforms).

Build with:  pyinstaller build.spec
"""
import sys
import runpy
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

version = runpy.run_path(str(Path(SPECPATH) / "playlistbro" / "__init__.py"))["__version__"]
windows_options = {}
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    version_tuple = tuple(int(part) for part in version.split(".")) + (0,)
    windows_options["version"] = VSVersionInfo(
        ffi=FixedFileInfo(filevers=version_tuple, prodvers=version_tuple),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "J. Baudru (Bonoob)"),
                StringStruct("FileDescription", "Setuno"),
                StringStruct("FileVersion", version),
                StringStruct("InternalName", "Setuno"),
                StringStruct("OriginalFilename", "Setuno.exe"),
                StringStruct("ProductName", "Setuno"),
                StringStruct("ProductVersion", version),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )

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
    **windows_options,
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
            "CFBundleShortVersionString": version,
            "NSHighResolutionCapable": True,
        },
    )
