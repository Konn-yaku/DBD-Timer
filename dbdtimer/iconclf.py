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

    判别区 mask（每类各自一个）：某状态类(上钩/献祭)与 normal 的差异区 = 该状态的
    固定图标区。预测时对**该类**只在它自己的 mask 内算相关，排除脸/背景干扰。
    注意上钩(钩形)与献祭(骷髅)图标区不同，必须分别给每类一个 mask。

    由于 'other' 是开放集，用“最相似类的相关度 < threshold => other”实现。

    阈值可分别指定（thr_hooked / thr_sacrificed）：上钩门槛调高能滤掉
    “受伤/被救后的人脸”与钩形在判别区 ~0.55~0.6 的误相关（否则会一直误判成
    钩上 -> 下钩永远漏报）。缺省时两者都用 threshold(兼容旧模型/训练扫描)。
    """

    def __init__(self, protos, threshold=0.70, mask=None,
                 thr_hooked=None, thr_sacrificed=None):
        # protos: {label: 1d float 均值}，label ∈ {'hooked','sacrificed'} (other 不建原型)
        # mask: dict{label: bool}（推荐，每类一个判别区）；也可传单一数组(兼容旧模型)。
        self.protos = protos
        self.threshold = float(threshold)
        self.thr_hooked = float(thr_hooked) if thr_hooked is not None else self.threshold
        self.thr_sacrificed = (float(thr_sacrificed) if thr_sacrificed is not None
                               else self.threshold)
        if isinstance(mask, dict):
            self.masks = {k: np.asarray(m, dtype=bool).ravel()
                          for k, m in mask.items()}
        elif mask is not None:
            m = np.asarray(mask, dtype=bool).ravel()
            self.masks = {"hooked": m, "sacrificed": m}   # 旧单一 mask 对两类共用
        else:
            self.masks = None

    # 只有“钩上/献祭”是可判定的正类；其余(正常/受伤/倒地/任何角色)一律 other。
    # 关键：不为 normal 建“脸原型”——角色头像成百上千、训练集只见过几个，
    # 一旦把 normal 当正类参与竞争，没见过的新角色容易被误判成正类。
    _POSITIVE = ("hooked", "sacrificed")

    def _sim(self, x, p, label):
        """归一化相关；若该类有自己的判别 mask，只在 mask 内像素计算。"""
        m = None if self.masks is None else self.masks.get(label)
        if m is not None:
            if m.sum() < 4:
                return 0.0
            x = x[m]
            p = p[m]
        if x.std() < 1e-6 or p.std() < 1e-6:
            return 0.0
        return float(np.corrcoef(x, p)[0, 1])

    def predict(self, patch_bgr):
        """返回 'hooked'/'sacrificed'/'other'（钩上/献祭用各自阈值）。"""
        x = _prep(patch_bgr).ravel()
        if x.std() < 1e-6:
            return "other"
        # 阈值未分别设置时保持旧的“单一阈值最近正类”语义
        if (abs(self.thr_hooked - self.threshold) < 1e-9
                and abs(self.thr_sacrificed - self.threshold) < 1e-9):
            best = None
            best_corr = self.threshold
            for label in self._POSITIVE:
                p = self.protos.get(label)
                if p is None or p.std() < 1e-6:
                    continue
                v = self._sim(x, p, label)
                if v > best_corr:
                    best_corr = v
                    best = label
            return best if best is not None else "other"
        # 分开阈值：献祭(死亡)优先(死人不会下钩)；其余看钩上是否够高。
        hc = self._sim(x, self.protos["hooked"], "hooked") if "hooked" in self.protos else 0.0
        sc = self._sim(x, self.protos["sacrificed"], "sacrificed")\
            if "sacrificed" in self.protos else 0.0
        if sc >= self.thr_sacrificed and sc >= hc:
            return "sacrificed"
        if hc >= self.thr_hooked:
            return "hooked"
        if sc >= self.thr_sacrificed:
            return "sacrificed"
        return "other"


def load_model(path=MODEL_FILE):
    """从 npz 加载模型(hooked/sacrificed 原型 + 每类判别区 mask)；损坏返回 None。"""
    try:
        if not os.path.exists(path):
            return None
        data = np.load(path)
        thr = float(data["threshold"]) if "threshold" in data.files else 0.70
        # 每类独立 mask（新格式），否则回退旧的单一 mask
        if "mask_hooked" in data.files or "mask_sacrificed" in data.files:
            mask = {k: data["mask_" + k].astype(bool)
                    for k in ("hooked", "sacrificed")
                    if ("mask_" + k) in data.files}
        elif "mask" in data.files:
            mask = data["mask"].astype(bool)
        else:
            mask = None
        protos = {}
        for k in ("hooked", "sacrificed"):
            if k in data.files:
                protos[k] = data[k].astype(np.float32)
        return PrototypeIconClassifier(protos, threshold=thr, mask=mask)
    except Exception:
        return None


def make_classifier(cfg=None):
    """构建当前可用的分类器：有模型用原型分类器，否则用占位。

    运行时按配置对“钩上/献祭”施加各自阈值（比训练单一阈值更能容忍人脸误相关）：
      - icon_hook_thr: 判定“钩上”所需 masked 相关(默认 0.65)；
        受伤/被救后的人脸对钩形判别区常只有 ~0.55~0.60，抬高后不会被误判成钩上。
      - 献祭阈值保持模型训练阈值(self.threshold)，默认 0.55。
    """
    model = load_model()
    if model is None:
        return DummyIconClassifier()
    if cfg is not None:
        d = cfg.get("detect", {})
        if isinstance(model, PrototypeIconClassifier):
            model.thr_hooked = float(d.get("icon_hook_thr", 0.65))
            # 献祭沿用模型自带阈值(训练时按 normal-误判率扫出)，可单独覆盖：
            model.thr_sacrificed = float(d.get("icon_sac_thr", model.threshold))
    return model
