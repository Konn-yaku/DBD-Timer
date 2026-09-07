# -*- coding: utf-8 -*-
"""全局热键（基于 GetAsyncKeyState 轮询，无需管理员权限）。

供两类用途：
1. manual_start：手动兜底“下钩计时”（默认鼠标下侧键 XBUTTON1）。
2. toggle_lock：悬浮窗锁定/解锁（默认 Ctrl+Alt+L，也可直接点悬浮窗锁按钮）。
   仅当用户开启 passthrough_on_lock（锁定=鼠标穿透）时，才必须靠此热键解锁，
   因此按全局热键实现，保证任何情况下都可用。
"""
import ctypes

from PySide6.QtCore import QObject, QTimer

user32 = ctypes.windll.user32

_KEY_MAP = {
    "CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B,
    "ESC": 0x1B, "SPACE": 0x20, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDN": 0x22, "INS": 0x2D, "DEL": 0x2E,
    "ENTER": 0x0D, "TAB": 0x09, "BACKSPACE": 0x08,
    # 鼠标侧键：XBUTTON1=下方/后退键，XBUTTON2=上方/前进键（别名 MOUSE4/MOUSE5）
    "XBUTTON1": 0x05, "XBUTTON2": 0x06,
    "MOUSE4": 0x05, "MOUSE5": 0x06,
    "MBACK": 0x05, "MFORWARD": 0x06,
}
for _i in range(1, 13):
    _KEY_MAP[f"F{_i}"] = 0x6F + _i
for _i in range(10):
    _KEY_MAP[str(_i)] = 0x30 + _i
for _i in range(26):
    _KEY_MAP[chr(ord("A") + _i)] = 0x41 + _i


def to_vk(name: str):
    """把按键名(XBUTTON1/F8/Ctrl/A/1...)转成虚拟键码；无法识别返回 None。"""
    name = name.strip()
    if name.upper() in _KEY_MAP:
        return _KEY_MAP[name.upper()]
    if len(name) == 1 and name.isalpha():
        return ord(name.upper())
    if len(name) == 1 and name.isdigit():
        return 0x30 + int(name)
    return None


# ---- 单键候选与规范化命名（用于应用内“按下新按键即重绑”） ----
_CANON = {}


def _fill_canon():
    for i in range(26):
        _CANON[0x41 + i] = chr(ord("A") + i)
    for i in range(10):
        _CANON[0x30 + i] = str(i)
    for i in range(1, 13):
        _CANON[0x6F + i] = f"F{i}"
    _CANON[0x05] = "XBUTTON1"
    _CANON[0x06] = "XBUTTON2"
    _CANON[0x25] = "Left"
    _CANON[0x26] = "Up"
    _CANON[0x27] = "Right"
    _CANON[0x28] = "Down"
    _CANON[0x20] = "Space"


_fill_canon()


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def key_candidates():
    """返回 [(vk, 规范化名称), ...]，供“按键捕获”轮询。"""
    return list(_CANON.items())


def canonical_name(vk: int):
    """虚拟键码 -> 规范化名称（如 0x41 -> 'A'，0x05 -> 'XBUTTON1'）。"""
    return _CANON.get(vk)


class KeyWatcher(QObject):
    """轮询一组组合键，主键上升沿触发回调。需在 Qt 事件循环内使用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []      # (id, mods:list[vk], main:vk, callback)
        self._prev_down = {}  # id -> bool
        self._armed = False   # 是否已“武装”：等所有主键松开一次后才允许触发
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.setInterval(30)

    def add(self, combo, callback):
        """combo: ["Ctrl","Alt","L"] 组合列表，或单个按键字符串如 "F8"/"XBUTTON2"。"""
        if isinstance(combo, str):
            combo = [combo]
        vks = [to_vk(k) for k in combo]
        if any(v is None for v in vks):
            raise ValueError(f"无法识别的按键组合: {combo}")
        mods, main = vks[:-1], vks[-1]
        item_id = id(callback)
        self._items.append((item_id, mods, main, callback))
        self._prev_down[item_id] = False

    def start(self):
        self._armed = False   # 重启后重新武装：避免把启动瞬间按着的键误当一次触发
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def clear(self):
        """清空所有已注册按键，便于应用内重绑后重建。"""
        self._items.clear()
        self._prev_down.clear()

    def _poll(self):
        # 武装门：启动(或重绑恢复)后，先等所有主键都松开一次，才允许触发
        if not self._armed:
            any_down = False
            for item_id, _mods, main, _cb in self._items:
                if _is_down(main):
                    any_down = True
                    self._prev_down[item_id] = True
                else:
                    self._prev_down[item_id] = False
            if any_down:
                return
            self._armed = True
        for item_id, mods, main, cb in self._items:
            if not _is_down(main):
                self._prev_down[item_id] = False
                continue
            mods_ok = all(_is_down(m) for m in mods)
            if not mods_ok:
                self._prev_down[item_id] = True  # 主键按下但修饰键不齐，不触发
                continue
            if not self._prev_down[item_id]:
                try:
                    cb()
                except Exception:
                    pass
            self._prev_down[item_id] = True


def _is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
