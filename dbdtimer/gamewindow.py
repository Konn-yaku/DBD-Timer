# -*- coding: utf-8 -*-
"""定位 DBD 游戏窗口并读取其矩形（纯 Win32，不触碰游戏进程内部）。

- 通过窗口标题子串匹配（默认 DeadByDaylight，无边框窗口模式）。
- 用 DWM DWMWA_EXTENDED_FRAME_BOUNDS 取精确可视矩形（含无边框窗口阴影修正）。
- WindowLocator 带 1 秒缓存，避免每帧都枚举窗口。
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

# DWMWA_EXTENDED_FRAME_BOUNDS
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def _enumerate_hwnds():
    out = []

    @_WNDENUMPROC
    def _cb(hwnd, _lparam):
        out.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return out


def _title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def pattern_matches(title: str, pattern: str) -> bool:
    """标题匹配：忽略大小写与所有空白(空格等)，做子串判断。

    例：pattern="DeadByDaylight" 能匹配标题 "Dead by Daylight" / "DEAD BY DAYLIGHT"。
    """
    flat = lambda s: "".join(s.lower().split())
    return flat(pattern) in flat(title)


def find_hwnd(title_part):
    """按标题子串(忽略大小写/空格)找可见窗口。返回 hwnd 或 None。"""
    for hwnd in _enumerate_hwnds():
        if not user32.IsWindowVisible(hwnd):
            continue
        if pattern_matches(_title(hwnd), title_part):
            return hwnd
    return None


def hwnd_rect(hwnd):
    """取窗口精确可视矩形 (left, top, right, bottom)，失败返回 None。"""
    if hwnd is None:
        return None
    try:
        r = RECT()
        if dwmapi.DwmGetWindowAttribute(
                hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(r), ctypes.sizeof(r)) == 0:
            if r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    try:
        r = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return (r.left, r.top, r.right, r.bottom)
    except Exception:
        pass
    return None


class WindowLocator:
    """带缓存与自动重试的窗口定位器。"""

    def __init__(self, title_part="DeadByDaylight", cache_seconds=1.0):
        self.title = title_part
        self._cache = cache_seconds
        self._hwnd = None
        self._next_search = 0.0
        self._last_rect = None
        self._next_rect = 0.0

    def rect(self):
        """返回当前窗口矩形；未找到返回 None。每约 1s 重扫窗口/重读矩形。"""
        now = time.monotonic()
        if now >= self._next_search:
            self._next_search = now + self._cache
            if self._hwnd is None:
                self._hwnd = find_hwnd(self.title)
            elif user32.IsWindow(self._hwnd) == 0:
                self._hwnd = None
        if self._hwnd is None:
            self._last_rect = None
            return None
        if now >= self._next_rect:
            self._next_rect = now + self._cache
            self._last_rect = hwnd_rect(self._hwnd)
        return self._last_rect

    @property
    def found(self):
        return self._hwnd is not None

    def foreground(self):
        """DBD 窗口是否当前在前台且未最小化。

        窗口被遮挡/最小化时，抓屏抓到的是遮挡物而不是游戏画面，
        识别结果不可信，调用方应据此暂停识别。
        """
        if self._hwnd is None:
            return False
        try:
            if user32.IsIconic(self._hwnd):
                return False
            return bool(user32.GetForegroundWindow() == self._hwnd)
        except Exception:
            return False
