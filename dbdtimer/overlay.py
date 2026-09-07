# -*- coding: utf-8 -*-
"""悬浮窗：透明置顶、可拖动、4 行黄/白双阶段一位小数计时（纯显示，平时鼠标穿透）。

与 4 名幸存者一一对应：
- 自动识别模式：4 行计时器分别锚定到 4 个头像框的“左侧”（由校正框 boxes +
  游戏窗口矩形实时换算，行距 = 头像间距，无需手动逐行对齐）。
- 手动兜底模式（无校正框/未找到游戏窗口时）：退化为自由纵向排布，可整体拖动。

鼠标穿透 / 锁（由托盘图标 + 热键控制）：
- 锁定态(默认)：整窗 WA_TransparentForInput 鼠标穿透，完全不挡游戏点击；
  此时不显示任何按钮（点了也没用）。
- 解锁态(拖动微调)：关闭穿透、可点击；窗口顶部显示一个“锁图标”，
  点击即可重新锁定；也可用托盘/Ctrl+Alt+L。
- 解锁后若配置 auto_relock_s>0，超时未操作会自动回到锁定(穿透)。
- 数字一位小数正数到 60：0~10s 黄(下钩保护)，10~60s 白(果断反击)，到 60 释放。
"""
import time
import winsound

from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QGuiApplication
from PySide6.QtWidgets import QWidget, QToolButton

from .config import save as _save_cfg
from .timers import TimerBank

# 数字右缘与头像左缘之间的留白(屏幕逻辑像素)
_RIGHT_GAP = 8
# 顶部留白(容纳解锁时的小锁图标)高度与其下方到第 0 行的间距
_CTRL_H = 24
_CTRL_GAP = 4
_MAX_TEXT = "60.0"   # 一位小数最长 4 字符，用于固定行宽避免抖动
# 半透明深色背景在最后一个头像下沿再往下多延伸的高度(逻辑像素)，
# 让面板底缘与头像列下沿看齐、不“卡”在最后一个头像中间。
_BOTTOM_PAD = 12
# 字号随分辨率自适应：font_px 是“参考 1920 宽(1080p)”时的字号；
# 2K/4K 下头像列与左侧空隙等比变大，字号按窗口宽度比例放大以保持一致观感。
_REF_WIDTH = 1920.0
_FONT_SCALE_MIN = 0.7
_FONT_SCALE_MAX = 3.0


def _hex_color(hex_str, fallback="#FFFFFF"):
    c = QColor(hex_str)
    return c if c.isValid() else QColor(fallback)


class DigitLabel(QWidget):
    """右对齐绘制带黑色描边的一行数字（锚定模式下右缘贴着头像左侧）。"""

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

    def measure_width(self):
        """固定行宽：容纳最长文本 + 描边余量，避免数字增减时整行晃动。"""
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(self.font())
        return int(fm.horizontalAdvance(_MAX_TEXT)) + 8

    def paintEvent(self, _event):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self.font())
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(self._text)
        h = fm.height()
        # 右对齐：文本右缘固定在 宽度-inset(3)，保证始终贴头像左侧
        inset = 3
        x = (self.width() - w) - inset
        y = (self.height() - h) / 2.0 + fm.ascent()
        # 黑色描边，保证亮/暗背景下都可读
        p.setPen(QColor(0, 0, 0, 220))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1)):
            p.drawText(int(x + dx), int(y + dy), self._text)
        p.setPen(self._color)
        p.drawText(int(x), int(y), self._text)
        p.end()


class OverlayWindow(QWidget):
    # 锁定状态变化(True=已锁定/鼠标穿透)。供 app 刷新托盘、自动回锁等。
    lock_changed = Signal(bool)

    def __init__(self, cfg, bank: TimerBank, boxes=None, rect_provider=None, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._bank = bank
        self._boxes = list(boxes) if boxes else []
        self._rect_provider = rect_provider
        self._locked = bool(cfg["overlay"]["locked"])
        self._drag_off: QPoint | None = None
        self._dragging = False
        self._last_anchor = None      # (winX, winY) 上次锚定位置，用于拖动归算偏移
        self._beep = bool(cfg["overlay"]["beep"])
        self._beep_p = []             # 每行是否已提示过“保护期结束”
        self._free_laid_out = False   # 自由模式只排一次版

        o = cfg["overlay"]
        self._c_prot = _hex_color(o.get("color_protection", "#FFD600"), "#FFD600")
        self._c_ds = _hex_color(o.get("color_ds", "#FFFFFF"), "#FFFFFF")
        self._font_px = int(o.get("font_px", 24))
        # dx/dy：锚定模式下相对“头像列左侧贴齐点”的用户偏移（可拖动微调，可负）
        self._dx = float(o.get("dx", 0.0))
        self._dy = float(o.get("dy", 0.0))

        # 行数：有校正框则与其一一对应，否则(手动模式)按计时槽数 4
        self._rows = len(self._boxes) if self._boxes else len(self._bank.slots)

        # 窗口属性：无边框 + 置顶 + 工具窗(不进Alt+Tab) + 透明背景 + 不抢焦点
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # ---- 锁定按钮：仅“未锁定(可拖动/非穿透)”时显示，点击即锁定 ----
        self._lock_btn = QToolButton(self)
        self._lock_btn.setFixedSize(18, 18)
        self._lock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lock_btn.setStyleSheet(
            "QToolButton{border:none;background:rgba(10,10,12,130);"
            "border-radius:9px;font-size:12px;color:white;}"
        )
        self._lock_btn.setToolTip("点击锁定并开启鼠标穿透（也可用托盘图标 / Ctrl+Alt+L）")
        self._lock_btn.clicked.connect(self.toggle_lock)

        # ---- 4 行数字 ----
        self._labels = []
        for _ in range(self._rows):
            lab = DigitLabel(self._font_px, self)
            lab.set_value("")
            self._labels.append(lab)
        self._beep_p = [False] * self._rows
        self._font_applied = self._font_px   # 当前实际应用的字号
        # 刷新循环
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)

        # 先定几何再 show，避免未定尺寸时闪现
        self._place_initial()
        self._update_geometry()
        self._apply_lock()
        self._refresh_lock_ui()

    # =========================================================
    # 几何：锚定到 4 个头像框左侧 / 或自由排布
    # =========================================================
    def _game_rect(self):
        try:
            return self._rect_provider() if self._rect_provider else None
        except Exception:
            return None

    def _apply_res_font(self, win_w_logical):
        """按游戏窗口逻辑宽度自动缩放字号，保证 1080p/2K/4K 观感一致。

        font_px 为 1080p(宽约1920逻辑像素)下的字号；更高分辨率按宽度比例放大。
        """
        if win_w_logical <= 0:
            return
        scale = min(max(win_w_logical / _REF_WIDTH, _FONT_SCALE_MIN), _FONT_SCALE_MAX)
        px = max(8, int(round(self._font_px * scale)))
        if px == self._font_applied:
            return
        self._font_applied = px
        for lab in self._labels:
            font = QFont("Consolas")
            font.setPixelSize(px)
            font.setBold(True)
            lab.setFont(font)

    @property
    def _label_font_px(self):
        """当前生效字号(标签字体已按分辨率设置过)。"""
        return self._font_applied

    def _place_initial(self):
        """自由模式首次定位：用配置 x,y，否则放左侧中段（靠近头像列常驻位置）。"""
        x = self._cfg["overlay"].get("x", -1.0)
        y = self._cfg["overlay"].get("y", -1.0)
        if x >= 0 and y >= 0:
            self.move(int(x), int(y))
            return
        # 无记录时，大致放到头像列左侧区域（左侧 4%~8%、纵向 38%~70%）
        rect = self._game_rect()
        if rect:
            l, t, r, b = rect
            dpr = self.devicePixelRatioF() or 1.0
            self.move(int(l / dpr + (r - l) * 0.02 / dpr),
                      int(t / dpr + (b - t) * 0.40 / dpr))
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _compute_anchor(self):
        """由游戏窗口矩形 + 头像框算出每行标签(窗口内)几何；无矩形返回 None。

        返回 (winX, winY, winW, winH, label_rects)
          label_rects: list[QRect] 每行标签在窗口内的位置。
        """
        rect = self._game_rect()
        if rect is None or not self._boxes:
            return None
        l, t, r, b = rect
        dpr = self.devicePixelRatioF() or 1.0
        # Win32 矩形是物理像素；Qt 窗口坐标是逻辑像素 -> 先统一换算到逻辑坐标
        l = l / dpr
        t = t / dpr
        W = (r / dpr) - l
        H = (b / dpr) - t
        # 字号随分辨率自动缩放(先按实际宽度调整，measure_width 依赖字体)
        self._apply_res_font(W)
        font_px = self._label_font_px
        # 头像列左侧统一右缘：所有行取最靠左头像的左边(减留白) -> 数字成整齐一列
        leftmost = min(box[0] for box in self._boxes)
        right_edge = l + leftmost * W - _RIGHT_GAP + self._dx
        centers = []
        for box in self._boxes:
            cy = t + ((box[1] + box[3]) / 2.0) * H + self._dy
            centers.append(cy)
        label_w = self._labels[0].measure_width()
        label_h = font_px + 10
        row_top_margin = _CTRL_H + _CTRL_GAP
        y0 = centers[0]
        win_y = y0 - label_h / 2.0 - row_top_margin
        win_x = right_edge - label_w
        # 高度需容纳：顶部控制条 + 第0行~最后一行数字。
        win_h = row_top_margin + (centers[-1] - y0) + label_h
        # 背景下缘再往下延：至少对齐到最后一个头像的下沿(+余量)，避免“卡”在头像中间
        last_avatar_bottom = t + self._boxes[-1][3] * H + self._dy
        win_h = max(win_h, int(last_avatar_bottom - win_y) + _BOTTOM_PAD)
        win_w = max(label_w, _CTRL_H * 3 + 8)
        rects = []
        for i, cy in enumerate(centers):
            # 标签在窗口内的 y：使其屏幕中心恰为 cy(头像中心)
            y_in_win = row_top_margin + (cy - y0)
            rects.append(QRect(0, int(y_in_win), int(label_w), int(label_h)))
        return (int(win_x), int(win_y), int(win_w), int(win_h), rects)

    def _apply_anchor(self, geo):
        win_x, win_y, win_w, win_h, rects = geo
        # 解除自由模式可能留下的固定尺寸约束，让窗口能按 4 行高度自由伸缩
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        if (win_x, win_y, win_w, win_h) != (self.x(), self.y(), self.width(), self.height()):
            self.setGeometry(win_x, win_y, win_w, win_h)
        for lab, r in zip(self._labels, rects):
            lab.setGeometry(r)
        self._last_anchor = (win_x, win_y)

    def _apply_free_layout(self):
        """自由模式(无校正框/未找到游戏窗口)：顶部留白(解锁时的小锁) + 各行均匀下排。"""
        if self._free_laid_out:
            return
        self._free_laid_out = True
        self._apply_res_font(_REF_WIDTH)   # 无窗口时按参考字号显示
        font_px = self._label_font_px
        label_w = self._labels[0].measure_width()
        label_h = font_px + 12
        row_step = label_h + 8
        row_top_margin = _CTRL_H + _CTRL_GAP
        win_w = max(label_w, _CTRL_H * 3 + 8)
        win_h = row_top_margin + self._rows * row_step + 4
        for i, lab in enumerate(self._labels):
            lab.setGeometry(QRect(0, row_top_margin + i * row_step, label_w, label_h))
        self.setFixedSize(win_w, win_h)
        self.setFixedHeight(win_h)

    def _layout_lock_btn(self, win_w):
        self._lock_btn.setGeometry(QRect(4, 3, 18, 18))

    def _update_geometry(self):
        if self._dragging:
            return
        if self._boxes and self._rect_provider:
            geo = self._compute_anchor()
            if geo is not None:
                self._free_laid_out = False
                self._apply_anchor(geo)
                self._layout_lock_btn(geo[2])
                return
        # 自由模式
        self._last_anchor = None
        self._apply_free_layout()
        self._layout_lock_btn(self.width())

    # ---------- 运行中更换头像框（校准后即用） ----------
    def set_boxes(self, boxes):
        """热更新头像框(校准保存后调用)：行数随框数增减，随后重新锚定。

        - 行数=框数；框数为 0 时回落到自由排布(4 行=计时槽)。
        - 只增删标签 widget 数量，不改计时槽(bank)本身。
        """
        self._boxes = list(boxes) if boxes else []
        self._rows = len(self._boxes) if self._boxes else len(self._bank.slots)
        while len(self._labels) < self._rows:
            lab = DigitLabel(self._font_px, self)
            lab.set_value("")
            self._labels.append(lab)
            lab.show()
        for i, lab in enumerate(self._labels):
            lab.setVisible(i < self._rows)
        self._beep_p = [False] * self._rows
        self._font_applied = 0        # 下次几何计算强制按新行宽重设字号
        self._free_laid_out = False   # 允许重新自由排版
        self._update_geometry()
        self._layout_lock_btn(self.width())

    # ---------- 位置 / 拖动 ----------
    def _save_pos(self):
        if self._boxes and self._rect_provider and self._last_anchor is not None:
            # 锚定模式：存 dx/dy 偏移
            self._cfg["overlay"]["dx"] = float(self._dx)
            self._cfg["overlay"]["dy"] = float(self._dy)
        else:
            self._cfg["overlay"]["x"] = float(self.x())
            self._cfg["overlay"]["y"] = float(self.y())
        _save_cfg(self._cfg)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton) and not self._locked:
            self._drag_off = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            self._dragging = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_off is not None and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_off)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_off is not None:
            self._dragging = False
            # 锚定模式：把拖动产生的位移折算进 dx/dy，松开后仍贴齐
            if self._last_anchor is not None:
                self._dx += (self.x() - self._last_anchor[0])
                self._dy += (self.y() - self._last_anchor[1])
            self._drag_off = None
            self._save_pos()
            self._update_geometry()
        super().mouseReleaseEvent(event)

    # ---------- 锁定 / 鼠标穿透 ----------
    @property
    def is_locked(self):
        return self._locked

    @property
    def is_dragging(self):
        return self._dragging

    def _refresh_lock_ui(self):
        """未锁定(可拖动/非穿透)时显示锁图标；已锁定(穿透)时隐藏(点了也没用)。"""
        self._lock_btn.setText("\U0001F512" if self._locked else "\U0001F513")
        self._lock_btn.setVisible(not self._locked)

    def set_locked(self, locked):
        locked = bool(locked)
        if locked == self._locked:
            self._refresh_lock_ui()
            return
        self._locked = locked
        self._cfg["overlay"]["locked"] = self._locked
        self._apply_lock()
        self._refresh_lock_ui()
        self._save_pos()
        self.lock_changed.emit(self._locked)

    def toggle_lock(self):
        self.set_locked(not self._locked)

    def _apply_lock(self):
        # 设计：锁定 = 整窗鼠标穿透(完全不挡游戏)；解锁 = 可点击/拖动微调。
        # setWindowFlag 会自动隐藏窗口，需再 show() 使其可见。
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self._locked)
        self.show()

    # ---------- 计时入口 / 刷新 ----------
    def manual_start(self):
        """手动兜底：启动第一个空闲槽(不知道对应哪位逃生者)。返回启动槽号或 None。"""
        idx = self._bank.manual_trigger()
        if idx is not None and idx < len(self._labels):
            self._beep_p[idx] = False
            self._labels[idx].set_value("0.0")
            self.raise_()
        return idx

    def start_slot(self, idx):
        """自动识别：启动/重启指定幸存者槽 idx 的计时器(槽=计时器一一对应)。
        返回启动的槽号或 None。"""
        got = self._bank.start_slot(idx)
        if got is not None and idx < len(self._labels):
            self._beep_p[idx] = False
            self._labels[idx].set_value("0.0")
            self.raise_()
        return got

    def _fmt(self, elapsed):
        return f"{min(elapsed, self._bank.duration):.1f}"

    def _tick(self):
        # 先维持几何(锚定/拖动状态下的贴齐)
        self._update_geometry()
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
            lab.set_value(self._fmt(r["elapsed"]))
            lab.set_color(self._c_prot if r["protection"] else self._c_ds)
            if not r["protection"] and not self._beep_p[i]:
                self._beep_p[i] = True
                if self._beep:
                    self._beep_phase()
        # 保底：无活动槽时也刷新空标签
        for i, lab in enumerate(self._labels):
            if i not in active:
                lab.set_value("")

    # ---------- 提示音 ----------
    @staticmethod
    def _beep_phase():
        try:
            winsound.Beep(880, 90)
        except Exception:
            pass

    @staticmethod
    def _beep_finish():
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
        p.setBrush(QColor(10, 10, 12, 70))  # 半透明深色底板提升数字可读性(极淡)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)
        p.end()
        super().paintEvent(event)
