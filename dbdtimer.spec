# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件 exe、无控制台。

构建：  .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean dbdtimer.spec
产物：  dist\DBD下钩计时助手.exe

要点：
- console=False：无黑色控制台窗口（托盘常驻工具，日志写到 exe 旁的 运行日志.log）。
- 数据：把 templates/icon_model.npz 打进 exe（_MEIPASS/templates/ 下），
  程序首次运行会自动解出到 exe 同目录 templates/ 并加载。
- 该 spec 由 打包成exe.bat 调用。
"""
import os

_here = os.path.abspath(os.getcwd())   # 以项目根为基准（bat 已 cd /d 过去）
_datas = []
_model = os.path.join(_here, "templates", "icon_model.npz")
if os.path.exists(_model):
    _datas.append((os.path.join("templates", "icon_model.npz"), "templates"))
else:
    print("警告: 未找到 templates/icon_model.npz，未内置图标模型。"
          "自动识别将不可用（需用户自行放置该文件）。")

a = Analysis(
    ["app.py"],
    pathex=[_here],
    binaries=[],
    datas=_datas,
    hiddenimports=["mss.windows"],        # mss 按平台惰性 import，显式收集
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "scipy", "PIL", "IPython", "jupyter"],
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
    name="DBD下钩计时助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # 不用 UPX：避免杀软误报与解压问题
    runtime_tmpdir=None,
    console=False,             # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
