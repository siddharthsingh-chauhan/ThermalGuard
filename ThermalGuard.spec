# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build: pyinstaller ThermalGuard.spec  (build.ps1 does this after the tests)
#
# One folder, not --onefile, and no UPX: a self-extracting, packed, unsigned
# exe is the classic antivirus false-positive pattern. The installer ships the
# whole dist\ThermalGuard folder.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                  VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH)
VERSION = re.search(r'"(.+)"', (ROOT / "version.py").read_text()).group(1)
nums = tuple(int(x) for x in VERSION.split("."))
nums += (0,) * (4 - len(nums))

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", "ThermalGuard"),
            StringStruct("FileDescription", "ThermalGuard"),
            StringStruct("FileVersion", VERSION),
            StringStruct("ProductVersion", VERSION),
            StringStruct("OriginalFilename", "ThermalGuard.exe"),
            StringStruct("InternalName", "ThermalGuard"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ["app.py"],
    hiddenimports=["pystray._win32"],
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ThermalGuard",
    console=False,
    upx=False,
    icon=str(ROOT / "assets" / "ThermalGuard.ico"),
    version=version_info,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="ThermalGuard")
