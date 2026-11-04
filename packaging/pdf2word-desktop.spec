# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: đóng gói app desktop pdf2word (one-folder).

Chạy từ thư mục gốc repo:
    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm packaging\pdf2word-desktop.spec

Kết quả: dist\pdf2word\pdf2word.exe
Tesseract 5 vẫn cần cài trên máy (hoặc copy vào dist\pdf2word\tesseract\,
xem packaging\build.ps1 -BundleTesseract).
"""

import os

ROOT = os.path.abspath(os.path.dirname(SPECPATH))

datas = [
    (os.path.join(ROOT, "models", "tessdata"), os.path.join("models", "tessdata")),
    (os.path.join(ROOT, "models", "spellfix.txt"), os.path.join("models", "spellfix.txt")),
    (os.path.join(ROOT, "models", "deepdoc"), os.path.join("models", "deepdoc")),
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["pdf2word.gui.app", "pdf2word.gui.preview", "pdf2word.gui.worker"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "reportlab"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pdf2word",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pdf2word",
)
