# -*- mode: python ; coding: utf-8 -*-
"""Windows向けのポータブル版。

フォルダのまま配布する。一時フォルダへ展開する one-file 形式は使わない。
学校のパソコンでは Temp が制限されていることがある。
管理者権限は要求しない。
"""

from PyInstaller.utils.hooks import collect_submodules

hidden = []
for package in ("pyicloud_ipd", "foundation", "icloudpd", "keyring", "pillow_heif", "PIL", "srp"):
    try:
        hidden += collect_submodules(package)
    except Exception:
        pass

hidden += [
    "icloud_drop",
    "keyring.backends.Windows",
    "keyring.backends.null",
]

a = Analysis(
    ["src/icloud_drop/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IcloudDrop",
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
    uac_admin=False,
    uac_uiaccess=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="IcloudDrop",
)
