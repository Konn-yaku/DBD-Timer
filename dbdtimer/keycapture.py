# -*- coding: utf-8 -*-
"""应用内按键设置。

- KeyCaptureDialog：旧版“单键捕获”（历史兼容）。
- ComboCaptureDialog：捕获“修饰键(Ctrl/Alt/Shift/Win) + 主键”组合，
  结果存为列表，如 ['Ctrl','Alt','L'] 或 ['XBUTTON2']。
- ShortcutSettingsDialog：一个页面集中编辑全部快捷键（手动/锁定/退出…），
  带重复绑定与“退出需带修饰键”的校验，确认后返回新绑定字典。
基于全局 GetAsyncKeyState 轮询，游戏窗口聚焦时也能捕获。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from .hotkey import key_candidates, key_down, mods_down, to_vk

_ESC_VK = 0x1B
_MOD_DISPLAY = {"CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift", "WIN": "Win"}
_MOD_NAMES = tuple(_MOD_DISPLAY)


def binding_label(binding):
    """把绑定(列表)显示成 'Ctrl+Alt+L'；空返回 '(未设置)'。"""
    if not binding:
        return "(未设置)"
    return "+".join(str(x) for x in binding)


def _wait_release_and_capture(parent, title, current_label, main_only=False):
    """公共捕获：先等所有候选/修饰键松开，再捕获“下一次按下”。

    返回 (accepted:bool, binding:list|None)
      main_only=True 时忽略修饰键(旧行为，捕获单个主键)。
    """
    dlg = _BaseCapture(parent, title, current_label)
    dlg.main_only = bool(main_only)
    if dlg.exec() == QDialog.DialogCode.Accepted and dlg.binding:
        return True, dlg.binding
    return False, None


class _BaseCapture(QDialog):
    def __init__(self, parent, title, current_label):
        super().__init__(parent)
        self.binding = None       # 捕获到的组合(list)或单键名(str, main_only)
        self.main_only = False
        self._armed = False
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        lay = QVBoxLayout(self)
        tip = QLabel(
            "请按下新的组合键：可先按住 Ctrl / Alt / Shift / Win，再按一个主键。\n"
            "主键支持：字母 / 数字 / F1~F12 / 方向键 / 空格 / 鼠标侧键。\n"
            "按 Esc 可取消。"
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self._cur = QLabel(f"当前：{current_label}")
        self._cur.setStyleSheet("color:#FFD54F;")
        lay.addWidget(self._cur)
        self._hint = QLabel("等待按键……（若按 Esc 则取消）")
        lay.addWidget(self._hint)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(30)

    def _poll(self):
        if not self._armed:
            # 等所有候选键与修饰键都松开，避免把对话框打开瞬间仍按着的键当成新键
            busy = [vk for vk, _ in key_candidates()]
            busy += [to_vk(m) for m in _MOD_NAMES]
            if not any(key_down(vk) for vk in busy if vk is not None):
                self._armed = True
            return
        if key_down(_ESC_VK):
            self.binding = None
            self.reject()
            return
        for vk, name in key_candidates():
            if key_down(vk):
                if self.main_only:
                    self.binding = name
                else:
                    mods = [_MOD_DISPLAY.get(m, m) for m in mods_down()]
                    self.binding = mods + [name]
                self.accept()
                return


class KeyCaptureDialog(_BaseCapture):
    """旧版：单键捕获（history 兼容），self.key_name 为单键名。"""

    def __init__(self, current="", parent=None):
        super().__init__(parent, "设置手动计时按键", current)
        self.key_name = None
        self.main_only = True

    def _poll(self):
        super()._poll()
        self.key_name = self.binding


class ComboCaptureDialog(_BaseCapture):
    """捕获“修饰键 + 主键”组合；self.binding 为列表。"""

    def __init__(self, current_label="", title="设置快捷键", parent=None):
        super().__init__(parent, title, current_label)
        self.main_only = False


class ShortcutSettingsDialog(QDialog):
    """集中编辑多个快捷键的页面。

    rows: [(id, 中文名称, 当前绑定list), ...]；确认后 self.result_bindings =
    {id: 新绑定list}（重复/退出无修饰键会被拦截）。
    """

    def __init__(self, parent, rows):
        super().__init__(parent)
        self.result_bindings = None
        self._rows = {}          # id -> 当前绑定(list)
        self._titles = {}        # id -> 中文名称
        self._btns = {}          # id -> 修改按钮
        for kid, title, _binding in rows:
            self._titles[kid] = title
        self.setWindowTitle("快捷键设置")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        lay = QVBoxLayout(self)
        head = QLabel("为每个功能设置快捷键：\n"
                      "· 建议“锁定/退出”至少带一个 Ctrl/Alt/Shift/Win 修饰键，避免误触；\n"
                      "· 各功能不能设成同一个键；退出会立即结束程序，务必谨慎。")
        head.setWordWrap(True)
        lay.addWidget(head)

        grid = QGridLayout()
        for idx, (kid, title, binding) in enumerate(rows):
            self._rows[kid] = list(binding or [])
            name_lab = QLabel(title)
            btn = QPushButton(binding_label(self._rows[kid]))
            btn.setMinimumWidth(220)
            btn.setStyleSheet("QPushButton{text-align:center;}")
            btn.clicked.connect(lambda _c=False, k=kid: self._edit(k))
            self._btns[kid] = btn
            grid.addWidget(name_lab, idx, 0, Qt.AlignmentFlag.AlignLeft)
            grid.addWidget(btn, idx, 1)
        lay.addLayout(grid)

        self._err = QLabel("")
        self._err.setStyleSheet("color:#FF5252;")
        self._err.setWordWrap(True)
        lay.addWidget(self._err)

        btns = QHBoxLayout()
        save_btn = QPushButton("保存并应用")
        save_btn.clicked.connect(self._on_save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(save_btn)
        lay.addLayout(btns)
        self.setMinimumWidth(460)

    def _edit(self, kid):
        title = {k: self._row_title(kid)}.get(kid, "")
        dlg = ComboCaptureDialog(current_label=binding_label(self._rows[kid]),
                                 title=f"修改：{title}", parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.binding:
            self._rows[kid] = dlg.binding
            self._btns[kid].setText(binding_label(dlg.binding))
            self._err.setText("")

    def _row_title(self, kid):
        # rows 名称保存在 _titles
        return self._titles.get(kid, kid)

    def _on_save(self):
        # 重复绑定检测
        seen = {}
        for kid, b in self._rows.items():
            key = tuple(b)
            if key in seen:
                self._err.setText(f"“{self._row_title(kid)}”与“{self._row_title(seen[key])}”"
                                  f"使用了相同的快捷键 {binding_label(b)}，请修改。")
                return
            seen[key] = kid
        # “退出”必须带修饰键，防止误触退出
        q = self._rows.get("quit")
        if q is not None and len(q) < 2:
            self._err.setText("“退出”至少需要一个 Ctrl/Alt/Shift/Win 修饰键，防止误触退出。")
            return
        self.result_bindings = {k: list(v) for k, v in self._rows.items()}
        self.accept()
