# -*- coding: utf-8 -*-
"""悬浮窗：透明置顶、可拖动、带锁按钮、黄/白双阶段数字计时。

交互规则：
- 解锁态：整窗可拖动（按住任意空白/数字拖动），点锁按钮或热键可锁定。
- 锁定态(默认)：仅禁止拖动、固定位置；锁按钮仍可点击，无需快捷键即可解锁。
- 可选 passthrough_on_lock=True：锁定同时鼠标穿透(不挡游戏)，此时只能靠热键解锁。
- 数字从 0 正数到 60：0~10s 黄(下钩保护)，10~60s 白(果断反击)，到 60 槽位释放。
"""
import time
import winsound

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton,
)

from .config import load as _load_cfg, save as _save_cfg
from .timers import TimerBank


def _hex_color(hex_str, fallback="#FFFFFF"):
    c = QColor(hex_str)
    return c if c.isValid() else QColor(fallback)


class DigitLabel(QWidget):
    """居中绘制带黑色描边的大号数字。"""

    def __init__(self, font_px, parent=None):
        super().__init__(parent)
        self._font_px = font_px
        self._text = ""
        self._color = QColor("#FFFFFF")
        font = QFont("Consolas")
        font.setPixelSize(font_px)
        font.setBold(True)
        self.setFont(font)

    def set_value(self, text):
        if text != self._text:
            self._text = text
            self.update()

    def set_color(self, color: QColor):
        if color != self._color:
            self._color = QColor(color)
            self.update()

    def paintEvent(self, _event):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.font())
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(self._text)
        h = fm.height()
        x = (self.width() - w) / 2.0
        y = (self.height() - h) / 2.0 + fm.ascent()
        # 黑色描边，保证亮/暗背景下都可读
        p.setPen(QColor(0, 0, 0, 220))
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)):
            p.drawText(int(x + dx), int(y + dy), self._text)
        p.setPen(self._color)
        p.drawText(int(x), int(y), self._text)
        p.end()


class OverlayWindow(QWidget):
    def __init__(self, cfg, bank: TimerBank, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._bank = bank
        self._locked = bool(cfg["overlay"]["locked"])
        # 可选：锁定是否同时“鼠标穿透”。默认 False=仅禁止拖动(可用锁按钮解锁)。
        self._passthrough_lock = bool(cfg["overlay"].get("passthrough_on_lock", False))
        self._drag_off: QPoint | None = None
        self._beep = bool(cfg["overlay"]["beep"])
        self._beep_p = [False, False]  # 是否已提示过“保护期结束”

        o = cfg["overlay"]
        self._c_prot = _hex_color(o.get("color_protection", "#FFD600"), "#FFD600")
        self._c_ds = _hex_color(o.get("color_ds", "#FFFFFF"), "#FFFFFF")
        font_px = int(o.get("font_px", 52))

        # 窗口属性：无边框 + 置顶 + 工具窗(不进Alt+Tab) + 透明背景 + 不抢焦点
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # ---- UI 结构 ----
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 8)
        root.setSpacing(2)

        ctrl_row = QHBoxLayout()
        self._lock_btn = QToolButton(self)
        self._lock_btn.setFixedSize(22, 22)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.setStyleSheet(
            "QToolButton{border:none;background:transparent;font-size:14px;color:white;}"
        )
        self._lock_btn.setToolTip("锁定/解锁位置（锁定后固定，点此或 Ctrl+Alt+L 解锁）")
        self._lock_btn.clicked.connect(self.toggle_lock)
        ctrl_row.addWidget(self._lock_btn)
        ctrl_row.addStretch(1)
        root.addLayout(ctrl_row)

        self._labels = []
        for _ in range(2):
            lab = DigitLabel(font_px)
            lab.setFixedWidth(int(font_px * 2.4))
            lab.setFixedHeight(font_px + 14)
            root.addWidget(lab, alignment=Qt.AlignmentFlag.AlignHCenter)
            self._labels.append(lab)
        self._labels[0].set_value("")
        self._labels[1].set_value("")

        # 固定窗口尺寸，避免槽位增减导致窗口抖动
        self.setFixedSize(int(font_px * 2.4) + 22, 28 + 2 * (font_px + 14) + 14)

        # 刷新循环
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

        self._apply_lock()
        self._place_initial()
        self._update_lock_icon()

    # ---------- 位置 / 拖动 ----------
    def _place_initial(self):
        x = self._cfg["overlay"].get("x", -1.0)
        y = self._cfg["overlay"].get("y", -1.0)
        if x >= 0 and y >= 0:
            self.move(int(x), int(y))
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _save_pos(self):
        self._cfg["overlay"]["x"] = float(self.x())
        self._cfg["overlay"]["y"] = float(self.y())
        _save_cfg(self._cfg)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton) and not self._locked:
            self._drag_off = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_off is not None:
            self._drag_off = None
            self._save_pos()
        super().mouseReleaseEvent(event)

    # ---------- 锁定 ----------
    def _update_lock_icon(self):
        self._lock_btn.setText("\U0001F512" if self._locked else "\U0001F513")

    def set_locked(self, locked):
        self._locked = bool(locked)
        self._cfg["overlay"]["locked"] = self._locked
        self._apply_lock()
        self._update_lock_icon()
        self._save_pos()

    def toggle_lock(self):
        self.set_locked(not self._locked)

    def _apply_lock(self):
        # 默认：锁定 = 仅禁止拖动(鼠标事件已在上层 handler 里被拦下)，锁按钮仍可点击解锁。
        # 可选(passthrough_on_lock=True)：锁定额外启用系统级鼠标穿透，此时只能靠热键解锁。
        if self._passthrough_lock:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self._locked)
            self.show()

    # ---------- 计时入口 / 刷新 ----------
    def slot_start(self):
        """外部(手动热键/自动识别)触发一次下钩计时。返回是否成功分配了槽位。"""
        idx = self._bank.trigger()
        if idx is not None:
            self._beep_p[idx] = False
            self._labels[idx].set_value("0")
            self.raise_()   # 置顶悬浮窗，避免被游戏画面遮挡时看不到数字
        return idx is not None

    def _tick(self):
        results = self._bank.sample()
        active = {r["idx"]: r for r in results}
        for i, lab in enumerate(self._labels):
            r = active.get(i)
            if r is None:
                lab.set_value("")
                continue
            if r["finished"]:
                if self._beep:
                    self._beep_finish()
                lab.set_value("")
                continue
            val = min(int(r["elapsed"]), int(self._bank.duration))
            lab.set_value(str(val))
            lab.set_color(self._c_prot if r["protection"] else self._c_ds)
            if not r["protection"] and not self._beep_p[i]:
                self._beep_p[i] = True
                if self._beep:
                    self._beep_phase()
        # 保底：无活动槽时也刷新一下空标签
        for i, lab in enumerate(self._labels):
            if i not in active:
                lab.set_value("")

    # ---------- 提示音 ----------
    @staticmethod
    def _beep_phase():
        # 保护期(10s)结束：一声短促低音
        try:
            winsound.Beep(880, 90)
        except Exception:
            pass

    @staticmethod
    def _beep_finish():
        # 60s 到期：两声提示
        try:
            winsound.Beep(1100, 110)
            time.sleep(0.05)
            winsound.Beep(1100, 110)
        except Exception:
            pass

    # ---------- 背景 ----------
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 10, 12, 90))  # 半透明深色底板提升数字可读性
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        p.end()
        super().paintEvent(event)
