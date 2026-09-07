# -*- coding: utf-8 -*-
"""图标识别引擎：把“整帧 + 4 个整框”变成“下钩事件”的顶层封装。

对外接口与旧 Detector 兼容(process/reset)，方便 app 在主循环里无缝切换：
    每框 crop → iconclf 分类(hooked/sacrificed/other)
             → iconstate 状态机(钩上→离开：献祭不触发，否则触发)
"""
from .iconclf import DummyIconClassifier, make_classifier
from .iconstate import IconHookDetector


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
            confirm=int(d.get("icon_confirm", 2)),
            post_window_s=float(d.get("icon_post_window_s", 1.2)),
            dead_idle_s=float(d.get("icon_dead_idle_s", 120.0)),
        )

    @property
    def ready(self):
        """是否有已训练模型；无模型时分类器为占位(恒 other，不触发)。"""
        return not isinstance(self.clf, DummyIconClassifier)

    def process(self, frame, boxes, now=None):
        cats = []
        for box in boxes:
            p = _crop(frame, box)
            cats.append(self.clf.predict(p) if p is not None else "other")
        return self.state.process(cats, now)

    def reset(self):
        self.state.reset()
