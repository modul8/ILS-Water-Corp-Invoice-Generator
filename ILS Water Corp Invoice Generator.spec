# -*- mode: python ; coding: utf-8 -*-


import os
import sys
from PyInstaller.utils.hooks import collect_all

root = os.getcwd()
sys.path.insert(0, root)

ui_datas, ui_binaries, ui_hidden = collect_all('ui')
svc_datas, svc_binaries, svc_hidden = collect_all('services')

hidden = list(set(ui_hidden + svc_hidden + [
    'ui.invoice_screen',
    'ui.shell',
    'ui.home_screen',
    'ui.drain_screen',
    'ui.work_sheet_screen',
    'ui.wizard',
    'ui.placeholder_screens',
]))

a = Analysis(
    ['app.py'],
    pathex=[root],
    binaries=ui_binaries + svc_binaries,
    datas=ui_datas + svc_datas + [
        ('assets\\ILS_WC.png', 'assets'),
        ('assets\\gps.png', 'assets'),
        ('assets\\ILS LOGO.png', 'assets'),
        ('assets\\RealTVSoftware.png', 'assets'),
    ],
    hiddenimports=hidden,
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
    name='ILS Water Corp Invoice Generator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\ILS_WC.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ILS Water Corp Invoice Generator',
)
