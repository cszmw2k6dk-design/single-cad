# -*- mode: python ; coding: utf-8 -*-
"""
build_app.spec -- 把「Single-CAD」打包成 Windows exe（PyInstaller）。

用法（在项目根目录）：
    python -m PyInstaller build_app.spec --noconfirm

产物：dist/Single-CAD/Single-CAD.exe
之后再跑 assemble_app.py 把 blocklib / templates / out / 使用说明 摆到 exe 旁边。

设计要点：
  * onefile（单文件 exe），业务数据 blocklib/templates 同时内嵌一份到 exe 里，
    运行时 runtime_paths.py 优先用 exe 旁边的同名目录 —— 用户改了块库就生效，
    只拷 exe 单独跑也不会缺块（走内嵌的那份只读副本）。
  * 排除 matplotlib/pandas/numpy 这些用不到的大件，缩体积、加快启动。
"""

import os

SRC = os.path.abspath(os.path.dirname(SPEC))

datas = [
    (os.path.join(SRC, "blocklib"), "blocklib"),
    (os.path.join(SRC, "templates"), "templates"),
]
for _doc in ("Barnett连线自动化_交接手册.docx", "图纸模板_块清单.txt", "version.json"):
    _p = os.path.join(SRC, _doc)
    if os.path.exists(_p):
        datas.append((_p, "."))

hiddenimports = [
    # 桌面窗口（pywebview + Edge WebView2，走 .NET/pythonnet）
    "webview", "webview.platforms.winforms", "webview.platforms.edgechromium",
    "webview.platforms.win32", "proxy_tools", "bottle",
    "clr_loader", "pythonnet", "clr", "System",
    # ZWCAD COM
    "win32com", "win32com.client", "win32com.client.dynamic",
    "pythoncom", "pywintypes", "win32timezone",
    "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont",
]

# 让 PyInstaller 把 pywebview / pythonnet 的原生依赖也一起收进来
try:
    from PyInstaller.utils.hooks import collect_all
    for _pkg in ("webview", "pythonnet", "clr_loader", "bottle"):
        try:
            _d, _b, _h = collect_all(_pkg)
            datas += _d
            binaries += _b
            hiddenimports += _h
        except Exception:
            pass
except Exception:
    pass

excludes = [
    "numpy", "pandas", "matplotlib", "scipy", "sympy",
    "tkinter", "test", "unittest", "pydoc_data",
    "docx", "openpyxl", "pdfplumber", "pdfminer", "ezdxf",
    "IPython", "jupyter", "notebook", "pyarrow", "sqlalchemy",
]

a = Analysis(
    ["run_app.py"],
    pathex=[SRC],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Single-CAD",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,             # 无控制台：双击只出桌面窗口，日志写到 exe 旁边的「启动日志.txt」
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
