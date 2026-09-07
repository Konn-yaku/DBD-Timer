# -*- coding: utf-8 -*-
"""校准界面：在 DBD 画面上框出 4 个幸存者状态图标（头像框）。

用途：
1. 鼠标拖出矩形框住 4 个幸存者状态图标（最多 4 个，多画会顶掉最早）。
2. “保存并退出”把框以归一化坐标写入 config.json(hud.boxes)，供图标识别引擎裁剪。

画面可暂停，方便精确对框。
"""
import cv2
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from .config import save as _save_cfg


class _Preview(QWidget):
    """画面预览：绘制当前帧 + 已框选框(绿)/选中框(红) + 拖拽中的框(白虚线)。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setMinimumSize(720, 420)
        self.setMouseTracking(True)
        self._drag_start = None   # 帧坐标
        self._drag_end = None
        self._cursor = None       # 帧坐标(悬停)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

    # ---- 坐标换算 ----
    def _scale(self):
        pm = self.owner._pixmap
        if pm is None or pm.isNull():
            return 1.0
        fw, fh = self.owner._frame_size
        if not fw or not fh:
            return 1.0
        return min(self.width() / fw, self.height() / fh)

    def _origin(self):
        pm = self.owner._pixmap
        if pm is None or pm.isNull():
            return 0, 0
        fw, fh = self.owner._frame_size
        s = self._scale()
        return (self.width() - fw * s) / 2.0, (self.height() - fh * s) / 2.0

    def to_norm(self, widget_pt):
        """widget 坐标 -> 归一化坐标(0~1, 相对游戏画面)。与框存储单位一致。"""
        pm = self.owner._pixmap
        fw, fh = self.owner._frame_size
        if pm is None or pm.isNull() or not fw or not fh:
            return (0.0, 0.0)
        s = self._scale()
        ox, oy = self._origin()
        x = (widget_pt.x() - ox) / s / fw
        y = (widget_pt.y() - oy) / s / fh
        return (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        pm = self.owner._pixmap
        if pm is not None and not pm.isNull():
            fw, fh = self.owner._frame_size
            s = self._scale()
            ox, oy = self._origin()
            p.drawPixmap(int(ox), int(oy), int(fw * s), int(fh * s), pm)
            # 已框的头像框：绿=已框，红+半透明=当前选中
            for i, b in enumerate(self.owner.boxes):
                x0, y0, x1, y1 = b
                sel = (i == self.owner.selected)
                rx = int(ox + x0 * fw * s)
                ry = int(oy + y0 * fh * s)
                rw = int((x1 - x0) * fw * s)
                rh = int((y1 - y0) * fh * s)
                if sel:
                    p.setBrush(QColor(255, 60, 60, 45))
                    p.drawRect(QRect(rx, ry, rw, rh))
                p.setPen(QPen(QColor(255, 60, 60) if sel else QColor(0, 255, 120), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRect(rx, ry, rw, rh))
                # 序号（左上角，带黑描边便于看清）
                num = str(i + 1)
                p.setPen(QColor(0, 0, 0))
                p.drawText(rx + 3, ry + 13, num)
                p.setPen(QColor(255, 255, 255))
                p.drawText(rx + 2, ry + 12, num)
            # 拖拽中的框（白色虚线，实时跟随）
            if self._drag_start is not None and self._drag_end is not None:
                x0, y0 = self._drag_start
                x1, y1 = self._drag_end
                p.setPen(QPen(QColor(255, 255, 255), 1, Qt.PenStyle.DashLine))
                p.drawRect(QRect(int(ox + x0 * fw * s), int(oy + y0 * fh * s),
                                 int((x1 - x0) * fw * s), int((y1 - y0) * fh * s)))
        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        nx, ny = self.to_norm(event.position().toPoint())
        # 先看是否点中了某个已框的头像框（选中）
        for i, b in enumerate(self.owner.boxes):
            if b[0] <= nx <= b[2] and b[1] <= ny <= b[3]:
                self.owner.selected = i
                self.owner._refresh_hint()
                self.update()
                return
        # 否则开始画新框
        self._drag_start = (nx, ny)
        self._drag_end = (nx, ny)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None:
            self._drag_end = self.to_norm(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag_start is not None and self._drag_end is not None:
            x0, y0 = self._drag_start
            x1, y1 = self._drag_end
            if abs(x1 - x0) > 0.01 and abs(y1 - y0) > 0.01:
                box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
                owner = self.owner
                if len(owner.boxes) < 4:
                    owner.boxes.append(box)
                else:  # 已满 4 个：顶掉最早画的
                    owner.boxes.pop(0)
                    owner.boxes.append(box)
                owner.selected = len(owner.boxes) - 1
                owner._refresh_hint()
        self._drag_start = None
        self._drag_end = None
        self.update()


class CalibrationDialog(QDialog):
    def __init__(self, cfg, locator, frame_source, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._locator = locator
        self._source = frame_source
        # 只载入合法框(归一化 0~1 且宽高>0)，过滤掉历史坏数据
        self.boxes = [
            [float(v) for v in b]
            for b in cfg["hud"]["boxes"]
            if len(b) == 4 and all(0.0 <= v <= 1.0 for v in b)
            and b[2] > b[0] and b[3] > b[1]
        ]
        self.selected = -1
        self._pixmap = None
        self._frame = None
        self._frame_size = (0, 0)
        self._paused = False

        self.setWindowTitle("DBD 计时助手 - 校准")
        self.resize(980, 720)
        root = QVBoxLayout(self)

        tip = QLabel(
            "① 在左侧幸存者状态列上把 4 个图标各拖一个框：绿色=已框，左上角有 1~4 序号。\n"
            "② 点中某个框可选中（变红）查看其归一化坐标。\n"
            "③ 框好后点“保存并退出”。\n"
            "（建议在自定对局/机器人局操作；画面可“暂停/继续”）"
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        self._preview = _Preview(self)
        root.addWidget(self._preview, 1)

        self._hint = QLabel("未找到 DBD 窗口……")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color:#FFD54F;font-weight:bold;")
        root.addWidget(self._hint)

        row1 = QHBoxLayout()
        for txt, fn in [
            ("清空框", self._clear_boxes),
            ("暂停/继续", self._toggle_pause),
        ]:
            b = QPushButton(txt)
            b.clicked.connect(fn)
            row1.addWidget(b)
        row1.addStretch(1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        save_btn = QPushButton("保存并退出")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("放弃退出")
        cancel_btn.clicked.connect(self.reject)
        row2.addStretch(1)
        row2.addWidget(save_btn)
        row2.addWidget(cancel_btn)
        root.addLayout(row2)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(100)
        self._refresh_hint()

    # ---- 数据 ----
    def _refresh_hint(self):
        fw, fh = self._frame_size
        info = f"窗口画面 {fw}x{fh} | 已框 {len(self.boxes)}/4 个"
        if self.boxes:
            w = self.boxes[self.selected if 0 <= self.selected < len(self.boxes) else -1]
            info += f" | 选中框归一化: {[round(v, 3) for v in w]}"
        self._hint.setText(info)

    def _clear_boxes(self):
        self.boxes.clear()
        self.selected = -1
        self._refresh_hint()
        self._preview.update()

    def _toggle_pause(self):
        self._paused = not self._paused

    def _refresh(self):
        if self._paused:
            return
        frame, rect = self._source.grab()
        if frame is None:
            self._hint.setText("未找到 DBD 窗口，请先启动游戏……")
            return
        self._frame = frame
        self._frame_size = (frame.shape[1], frame.shape[0])
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, rgb.shape[1], rgb.shape[0],
                      3 * rgb.shape[1], QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg.copy())
        self._preview.update()
        self._refresh_hint()

    # ---- 收尾 ----
    def accept(self):
        self.cfg["hud"]["boxes"] = [list(map(float, b)) for b in self.boxes]
        _save_cfg(self.cfg)
        super().accept()
