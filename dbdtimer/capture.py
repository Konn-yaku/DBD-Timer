# -*- coding: utf-8 -*-
"""截屏源：抓取 DBD 游戏窗口区域画面。

优先 dxcam（DXGI 桌面复制，快、兼容独占全屏），失败自动降级 mss。
只抓窗口矩形对应区域，返回 BGR numpy 帧。
"""
import numpy as np

try:
    import dxcam
except Exception:  # pragma: no cover
    dxcam = None

import mss


class FrameSource:
    """从 WindowLocator 提供的矩形持续取帧。"""

    def __init__(self, locator, output_color="BGR"):
        self._locator = locator
        self._out_color = output_color
        self._dxcam = None
        self._dxcam_failed = False
        self._mss = None

    # ---- 后端 ----
    def _grab_dxcam(self, rect):
        if self._dxcam is None:
            self._dxcam = dxcam.create(output_color=self._out_color)
        frame = self._dxcam.grab(region=rect)
        if frame is None:
            return None
        return np.ascontiguousarray(frame)

    def _grab_mss(self, rect):
        if self._mss is None:
            self._mss = mss.mss()
        left, top, right, bottom = (int(v) for v in rect)
        mon = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        raw = self._mss.grab(mon)
        arr = np.asarray(raw)          # BGRA
        bgr = arr[:, :, :3]            # 取 BGR 三通道
        if self._out_color == "BGR":
            return np.ascontiguousarray(bgr)
        return np.ascontiguousarray(bgr[:, :, ::-1])  # RGB

    def grab(self):
        """返回 (frame, rect)；找不到窗口或失败返回 (None, None)。"""
        rect = self._locator.rect()
        if rect is None:
            return None, None
        if not (rect[2] - rect[0] > 0 and rect[3] - rect[1] > 0):
            return None, None

        frame = None
        if dxcam is not None and not self._dxcam_failed:
            try:
                frame = self._grab_dxcam(rect)
            except Exception:
                self._dxcam_failed = True  # 永久降级，避免反复试错
        if frame is None:
            try:
                frame = self._grab_mss(rect)
            except Exception:
                return None, rect
        return frame, rect

    def close(self):
        try:
            if self._dxcam is not None:
                self._dxcam.stop()
        except Exception:
            pass
