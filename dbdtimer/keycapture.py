# -*- coding: utf-8 -*-
"""应用内重绑“手动计时”按键的对话框。

弹出后：提示按下新按键；程序先等所有候选键松开（避免误抓打开瞬间仍按着的键），
再捕获下一次“按下”。Esc 取消。结果以规范化名称(如 'F8'/'XBUTTON2'/'A')保存到
self.key_name。基于全局 GetAsyncKeyState 轮询，游戏窗口聚焦时也能捕获。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from .hotkey import key_candidates, key_down

_ESC_VK = 0x1B


class KeyCaptureDialog(QDialog):
    def __init__(self, current="", parent=None):
        super().__init__(parent)
        self.key_name = None       # 捕获到的规范化按键名；取消则为 None
        self._armed = False        # 是否已等到所有键松开、开始监听下一次按下
        self.setWindowTitle("设置手动计时按键")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        lay = QVBoxLayout(self)
        tip = QLabel(
            "请按下要用于「手动计时」的新按键。\n"
            "支持：字母 / 数字 / F1~F12 / 方向键 / 空格 / 鼠标侧键。\n"
            "按 Esc 可取消。"
        )
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self._cur = QLabel(f"当前按键：{current}")
        self._cur.setStyleSheet("color:#FFD54F;")
        lay.addWidget(self._cur)
        self._hint = QLabel("等待按键……（若按 Esc 则取消）")
        lay.addWidget(self._hint)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(30)

    def _poll(self):
        if not self._armed:
            # 先等所有候选键都松开，避免把对话框打开瞬间仍按着的键当成新键
            if not any(key_down(vk) for vk, _ in key_candidates()):
                self._armed = True
            return
        if key_down(_ESC_VK):
            self.key_name = None
            self.reject()
            return
        for vk, name in key_candidates():
            if key_down(vk):
                self.key_name = name
                self.accept()
                return
