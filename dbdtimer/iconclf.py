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

    关键增强 mask：训练时自动定位“上钩/献祭固定图标区”(对比 hooked/normal 均值差异)，
    预测时**只在该区域内算相关**，把随角色/地图变化的脸与背景排除 → 换任何角色都稳。

    由于 'other' 是开放集，用“最相似类的相关度 < threshold => other”实现。
    """

    def __init__(self, protos, threshold=0.70, mask=None):
        # protos: {label: 1d float 均值}，label ∈ {'hooked','sacrificed'} (other 不建原型)
        self.protos = protos
        self.threshold = float(threshold)
        self.mask = (np.asarray(mask, dtype=bool).ravel()
                     if mask is not None else None)

    # 只有“钩上/献祭”是可判定的正类；其余(正常/受伤/倒地/任何角色)一律 other。
    # 关键：不为 normal 建“脸原型”——角色头像成百上千、训练集只见过几个，
    # 一旦把 normal 当正类参与竞争，没见过的新角色容易被误判成正类。
    _POSITIVE = ("hooked", "sacrificed")

    def _sim(self, x, p):
        """归一化相关；若给定了判别区 mask，只在 mask 内像素计算。"""
        if self.mask is not None:
            m = self.mask
            if m.sum() < 4:
                return 0.0
            x = x[m]
            p = p[m]
        if x.std() < 1e-6 or p.std() < 1e-6:
            return 0.0
        return float(np.corrcoef(x, p)[0, 1])

    def predict(self, patch_bgr):
        x = _prep(patch_bgr).ravel()
        if x.std() < 1e-6:
            return "other"
        best = None
        best_corr = self.threshold
        for label, p in self.protos.items():
            if label not in self._POSITIVE:   # normal 等开放集样本不参与竞争
                continue
            if p.std() < 1e-6:
                continue
            v = self._sim(x, p)
            if v > best_corr:
                best_corr = v
                best = label
        return best if best is not None else "other"


def load_model(path=MODEL_FILE):
    """从 npz 加载模型(hooked/sacrificed 原型 + 可选判别区 mask)；损坏返回 None。"""
    try:
        if not os.path.exists(path):
            return None
        data = np.load(path)
        thr = float(data["threshold"]) if "threshold" in data.files else 0.70
        mask = data["mask"].astype(bool) if "mask" in data.files else None
        protos = {}
        for k in ("hooked", "sacrificed"):
            if k in data.files:
                protos[k] = data[k].astype(np.float32)
        return PrototypeIconClassifier(protos, threshold=thr, mask=mask)
    except Exception:
        return None


def make_classifier(cfg=None):
    """构建当前可用的分类器：有模型用原型分类器，否则用占位。"""
    model = load_model()
    if model is not None:
        return model
    return DummyIconClassifier()
