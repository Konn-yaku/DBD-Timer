# -*- coding: utf-8 -*-
"""校准界面：在 DBD 画面上框出 4 个幸存者头像框，并可拍摄三态参考模板。

用途：
1. 框选头像：鼠标拖出矩形（最多 4 个，多画会顶掉最早的）。框存成归一化坐标。
2. 拍模板：先点击选中某个框（变红），再点“记为上钩/记倒地/记正常”。
   建议在自定对局(机器人)里操作：把人类挂上钩时拍“上钩”，砍倒时拍“倒地”。
3. “保存并退出”把框写入 config.json。

画面可暂停，方便精确对框/拍摄。
"""
import glob
import os
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, QPoint, QRect
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from .config import (
    load as _load_cfg, save as _save_cfg, tpl_dir,
    TPL_ALIVE, TPL_HOOKED_DIFF, TPL_DOWNED_DIFF,
)


def _count(category):
    return len(glob.glob(os.path.join(tpl_dir(category), "*.png")))


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
            "① 在底部把 4 个幸存者头像各拖一个框：绿色=已框，左上角有 1~4 序号。\n"
            "② 先给每个框各拍一张【记为正常】（记录该槽正常外观）——点中某框(变红)→记为正常。\n"
            "③ 出现状态时再点【记为上钩/记为倒地】：程序会存成“差异图”(去掉脸和背景、只留图标)。\n"
            "④ 拍完点“保存并退出”。（建议在自定对局/机器人局操作；画面可“暂停/继续”）"
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
        self._btn_alive = QPushButton(f"记为正常（{_count(TPL_ALIVE)}）")
        self._btn_hooked = QPushButton(f"记为上钩（{_count(TPL_HOOKED_DIFF)}）")
        self._btn_downed = QPushButton(f"记为倒地（{_count(TPL_DOWNED_DIFF)}）")
        self._btn_alive.clicked.connect(self._capture_alive)
        self._btn_hooked.clicked.connect(lambda: self._capture(TPL_HOOKED_DIFF))
        self._btn_downed.clicked.connect(lambda: self._capture(TPL_DOWNED_DIFF))
        row2.addWidget(self._btn_alive)
        row2.addWidget(self._btn_hooked)
        row2.addWidget(self._btn_downed)
        row2.addStretch(1)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        save_btn = QPushButton("保存并退出")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("放弃退出")
        cancel_btn.clicked.connect(self.reject)
        row3.addStretch(1)
        row3.addWidget(save_btn)
        row3.addWidget(cancel_btn)
        root.addLayout(row3)

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

    def _selected_crop(self):
        """返回选中框在当前帧的 BGR 裁剪图；不满足条件时给提示并返回 None。"""
        if self._frame is None:
            self._hint.setText("当前没有画面：请确认已启动 DBD（窗口标题含 DeadByDaylight）。")
            return None
        if not (0 <= self.selected < len(self.boxes)):
            self._hint.setText("还没有选中头像框：请先用鼠标点中画面里的某个头像框（会变红），再点按钮。")
            return None
        b = self.boxes[self.selected]
        fw, fh = self._frame_size
        x0 = max(0, int(b[0] * fw)); y0 = max(0, int(b[1] * fh))
        x1 = min(fw, int(b[2] * fw)); y1 = min(fh, int(b[3] * fh))
        if x1 - x0 < 4 or y1 - y0 < 4:
            self._hint.setText("选中框太小，请重新框大一点。")
            return None
        return self._frame[y0:y1, x0:x1].copy()

    def _refresh_counts(self):
        self._btn_alive.setText(f"记为正常（{_count(TPL_ALIVE)}）")
        self._btn_hooked.setText(f"记为上钩（{_count(TPL_HOOKED_DIFF)}）")
        self._btn_downed.setText(f"记为倒地（{_count(TPL_DOWNED_DIFF)}）")

    def _capture_alive(self):
        """为选中槽位记录“正常”外观，作为后续差异图计算的减数(基线)。"""
        crop = self._selected_crop()
        if crop is None:
            return
        slot = self.selected
        name = f"slot{slot}.png"
        ok = cv2.imwrite(os.path.join(tpl_dir(TPL_ALIVE), name), crop)
        if ok:
            self._refresh_counts()
            self._hint.setText(f"✓ 已把第 {slot + 1} 个框记为【正常】→ templates/alive/{name}")
        else:
            self._hint.setText("保存失败：请检查 templates/ 目录是否可写。")

    def _capture(self, category):
        """拍 上钩/倒地：保存“该状态图 - 该槽正常图”的差异图（只留图标，去掉脸/背景）。"""
        crop = self._selected_crop()
        if crop is None:
            return
        slot = self.selected
        normal_path = os.path.join(tpl_dir(TPL_ALIVE), f"slot{slot}.png")
        if not os.path.exists(normal_path):
            self._hint.setText(
                f"第 {slot + 1} 个框还没有“记为正常”。请先（在该框为正常状态时）点【记为正常】，再拍 上钩/倒地。")
            return
        normal = cv2.imread(normal_path, cv2.IMREAD_GRAYSCALE)
        if normal is None:
            self._hint.setText("读取“正常”参考失败，请重新为该框点一次【记为正常】。")
            return
        cur = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        cur = cv2.resize(cur, (32, 32), interpolation=cv2.INTER_AREA)
        norm = cv2.resize(normal, (32, 32), interpolation=cv2.INTER_AREA)
        diff = np.abs(cur.astype(np.float32) - norm.astype(np.float32))
        diff_u8 = np.clip(diff, 0, 255).astype(np.uint8)
        label = "上钩" if category == TPL_HOOKED_DIFF else "倒地"
        name = f"{int(time.time() * 1000)}_{label}.png"
        ok = cv2.imwrite(os.path.join(tpl_dir(category), name), diff_u8)
        if ok:
            self._refresh_counts()
            self._hint.setText(
                f"✓ 已保存第 {slot + 1} 框的「{label}」差异图 → templates/{category}/{name}（可多拍几张）")
        else:
            self._hint.setText("保存失败：请检查 templates/ 目录是否可写。")

    # ---- 收尾 ----
    def accept(self):
        self.cfg["hud"]["boxes"] = [list(map(float, b)) for b in self.boxes]
        _save_cfg(self.cfg)
        super().accept()
