# -*- coding: utf-8 -*-
"""图标分类器：把某槽“状态图标小区域”分成 钩上/献祭/其他 三类。

先不训练：默认用 DummyIconClassifier（恒返回 'other'，供联调主流程）。
等收集到样本后，用 tools/train_icon_clf.py 生成 templates/icon_model.npz，
程序启动时会自动加载 PrototypeIconClassifier（最近原型匹配，纯 numpy，无额外依赖）。

类别语义：
    'hooked'    幸存者正处于钩上（图标固定样式）
    'sacrificed' 三挂献祭/死亡（图标固定样式）
    'other'     其余一切（正常/受伤/倒地/图标空位……开放集）
"""
import os

import cv2
import numpy as np

from .config import TEMPLATES_DIR

MODEL_FILE = os.path.join(TEMPLATES_DIR, "icon_model.npz")

PATCH = 24  # 分类器内部统一缩放的边长


def _prep(patch_bgr):
    """把任意 BGR 裁剪缩放到 PATCHxPATCH 灰度并归一，作为分类器输入。"""
    g = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (PATCH, PATCH), interpolation=cv2.INTER_AREA)
    return g.astype(np.float32) / 255.0


class IconClassifier:
    """分类器基类：predict(patch_bgr) -> 'hooked' | 'sacrificed' | 'other'"""

    def predict(self, patch_bgr):  # pragma: no cover - abstract
        raise NotImplementedError


class DummyIconClassifier(IconClassifier):
    """占位：未训练时使用，恒判 'other'（不触发任何下钩）。"""

    def predict(self, patch_bgr):
        return "other"


class PrototypeIconClassifier(IconClassifier):
    """最近原型分类器：每类存一个均值特征(灰度patch)，按归一化相关最近类判定。

    由于 'other' 是开放集，用“最相似类的相关度 < threshold => other”实现。
    """

    def __init__(self, protos, threshold=0.70):
        # protos: {label: 1d float 均值}，label ∈ {'hooked','sacrificed'} (other 不建原型)
        self.protos = protos
        self.threshold = float(threshold)

    def predict(self, patch_bgr):
        x = _prep(patch_bgr).ravel()
        if x.std() < 1e-6:
            return "other"
        best = None
        best_corr = self.threshold
        for label, p in self.protos.items():
            if p.std() < 1e-6:
                continue
            v = float(np.corrcoef(x, p)[0, 1])
            if v > best_corr:
                best_corr = v
                best = label
        if best is None:
            return "other"
        if best == "normal":
            return "other"    # normal 原型只是“开放集/正常”的代表
        return best


def load_model(path=MODEL_FILE):
    """从 npz 加载原型模型；不存在或损坏返回 None。"""
    try:
        if not os.path.exists(path):
            return None
        data = np.load(path)
        protos = {str(k): data[k].astype(np.float32) for k in data.files}
        thr = float(data.get("threshold", 0.70)) if "threshold" in data.files else 0.70
        return PrototypeIconClassifier(protos, threshold=thr)
    except Exception:
        return None


def make_classifier(cfg=None):
    """构建当前可用的分类器：有模型用原型分类器，否则用占位。"""
    model = load_model()
    if model is not None:
        return model
    return DummyIconClassifier()
