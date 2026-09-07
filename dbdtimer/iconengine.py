# -*- coding: utf-8 -*-
"""图标识别引擎：把“整帧 + 4 个整框”变成“下钩事件”的顶层封装。

对外接口与旧 Detector 兼容(process/reset)，方便 app 在主循环里无缝切换：
    每框 crop → iconclf 分类(hooked/sacrificed/other)
             → iconstate 状态机(钩上→离开：献祭不触发，否则触发)
"""
import glob
import os
import time

import cv2

from .config import DEBUG_DIR
from .iconclf import DummyIconClassifier, make_classifier
from .iconstate import IconHookDetector

_CAT_COLOR = {
    "hooked": (0, 0, 255),       # 红
    "sacrificed": (0, 140, 255), # 橙
    "other": (0, 255, 0),        # 绿
}
_STAT = {"idle": "I", "hooked": "H", "pending": "P", "dead": "D"}


def _crop(frame, box):
    H, W = frame.shape[:2]
    x0 = max(0, int(box[0] * W)); y0 = max(0, int(box[1] * H))
    x1 = min(W, int(box[2] * W)); y1 = min(H, int(box[3] * H))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return frame[y0:y1, x0:x1].copy()


class IconEngine:
    """基于“固定状态图标分类 + 状态机”的下钩检测器（图标方案）。"""

    def __init__(self, cfg, on_unhook, n=4):
        self.clf = make_classifier(cfg)
        d = cfg["detect"]
        self.state = IconHookDetector(
            on_unhook=on_unhook,
            n=int(n),
            confirm=int(d.get("icon_confirm", 4)),
            post_window_s=float(d.get("icon_post_window_s", 1.2)),
            dead_idle_s=float(d.get("icon_dead_idle_s", 60.0)),
        )
        self.debug_on = bool(d.get("debug_frames", False))
        self._next_debug = 0.0
        self._dbg_seq = 0
        self._dbg_reported = False   # 首次成功写帧后打印实际目录
        self._t0 = time.monotonic()   # 日志用相对运行时间，便于阅读
        if self.debug_on:
            print(f"[icon] 调试帧已开启 → 将写入目录: {DEBUG_DIR}")

    @property
    def ready(self):
        """是否有已训练模型；无模型时分类器为占位(恒 other，不触发)。"""
        return not isinstance(self.clf, DummyIconClassifier)

    def process(self, frame, boxes, now=None):
        if now is None:
            now = time.monotonic()
        cats = []
        for box in boxes:
            p = _crop(frame, box)
            cats.append(self.clf.predict(p) if p is not None else "other")
        before = [sl.state for sl in self.state.slots]
        events = self.state.process(cats, now)
        if self.debug_on:
            for i, sl in enumerate(self.state.slots):
                if sl.state != before[i]:
                    print(f"[icon] t+{now - self._t0:6.1f}s 槽{i}: "
                          f"{before[i]}→{sl.state} "
                          f"分类={cats[i] if i < len(cats) else '?'}")
        if self.debug_on and now >= self._next_debug:
            self._next_debug = now + 4.0
            self._save_debug(frame, boxes, cats, now)
        return events

    def reset(self):
        self.state.reset()

    # ---- 调试帧：画出每槽 预测类别/状态机状态 ----
    def _save_debug(self, frame, boxes, cats, now):
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            vis = frame.copy()
            H, W = vis.shape[:2]
            for i, (box, cat) in enumerate(zip(boxes, cats)):
                x0, y0 = int(box[0] * W), int(box[1] * H)
                x1, y1 = int(box[2] * W), int(box[3] * H)
                col = _CAT_COLOR.get(cat, (255, 255, 255))
                st = self.state.slots[i].state if i < len(self.state.slots) else "?"
                cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
                cv2.putText(vis, f"{cat[:1]}{_STAT.get(st, '?')}",
                            (x0, max(0, y0 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
            self._dbg_seq += 1
            name = os.path.join(DEBUG_DIR,
                                f"dbg_icon_{int(now)}_{self._dbg_seq:03d}.png")
            ok = cv2.imwrite(name, vis)
            if not self._dbg_reported:
                self._dbg_reported = True
                print(f"[icon] 调试帧写盘: {'成功' if ok else '失败(cv2.imwrite返回False)'} "
                      f"-> {name}（目录 {DEBUG_DIR}）")
            files = sorted(glob.glob(os.path.join(DEBUG_DIR, "dbg_icon_*.png")))
            for fp in files[:-80]:
                try:
                    os.remove(fp)
                except OSError:
                    pass
        except Exception as exc:   # 不再静默吞掉，便于定位写帧失败
            print(f"[icon] 调试帧写入失败: {exc}（目标目录 {DEBUG_DIR}）")
