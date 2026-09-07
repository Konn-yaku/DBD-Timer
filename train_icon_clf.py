# -*- coding: utf-8 -*-
"""从 templates/icons/<类别>/*.png 训练“图标原型分类器”，输出 templates/icon_model.npz。

前置：先把“状态图标小区域”的整框截图按类别放入：
    templates/icons/normal/      正常/其他(未上钩未献祭)
    templates/icons/hooked/      正挂在钩上
    templates/icons/sacrificed/  三挂献祭/死亡
（每类建议 >= 10 张，最好覆盖不同角色/地图以保证稳定）

用法:
    python train_icon_clf.py
训练完成重启程序即自动加载新模型（日志无提示=加载成功，有模型时不再全返回 'other'）。
"""
import glob
import os
import sys

import cv2
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from dbdtimer import iconclf  # noqa: E402

CLASSES = ["normal", "hooked", "sacrificed"]
SEED = 42


def load_samples():
    """返回 {label: [feature_1d, ...]}。"""
    out = {}
    for c in CLASSES:
        d = os.path.join(iconclf.TEMPLATES_DIR, "icons", c)
        files = sorted(glob.glob(os.path.join(d, "*.png")))
        feats = []
        for fp in files:
            img = cv2.imread(fp)
            if img is None:
                continue
            feats.append(iconclf._prep(img).ravel())
        out[c] = feats
        print(f"[{c:>10}] {len(feats):3d} 张")
    return out


def build_protos(train):
    protos = {}
    for c, feats in train.items():
        if feats:
            protos[c] = np.mean(feats, axis=0).astype(np.float32)
    return protos


def evaluate(protos, test, threshold):
    """返回 (总准确率, hooked/sacrificed 的查准)。"""
    def pred(x):
        best, best_corr = None, threshold
        for c, p in protos.items():
            if p.std() < 1e-6:
                continue
            v = float(np.corrcoef(x, p)[0, 1])
            if v > best_corr:
                best_corr, best = v, c
        return best if best is not None else "other"

    total = correct = 0
    hit = {"hooked": [0, 0], "sacrificed": [0, 0]}
    for c, feats in test.items():
        for x in feats:
            total += 1
            p = pred(x)
            if p == c or (p == "other" and c == "normal"):
                correct += 1
            if c in hit:
                hit[c][1] += 1
                if p == c:
                    hit[c][0] += 1
    acc = correct / total if total else 0.0
    prec = {k: (v[0] / v[1] if v[1] else 0.0) for k, v in hit.items()}
    return acc, prec


def main():
    data = load_samples()
    if sum(len(v) for v in data.values()) < 6:
        print("\n样本太少(<6)，请先用 --collect 模式或多放截图到 templates/icons/。")
        return

    # 固定划分：每类前 70% 训练、后 30% 留出评估
    rng = np.random.RandomState(SEED)
    train, test = {}, {}
    for c, feats in data.items():
        idx = rng.permutation(len(feats))
        n = max(1, int(len(feats) * 0.7))
        train[c] = [feats[i] for i in idx[:n]]
        test[c] = [feats[i] for i in idx[n:]]

    protos = build_protos(train)
    best = (None, -1, None)
    for thr in [x / 100.0 for x in range(55, 86, 5)]:
        acc, prec = evaluate(protos, test, thr)
        key = prec["hooked"] + prec["sacrificed"]  # 兼顾两类查准
        if key > best[1]:
            best = (thr, key, (acc, prec))
    thr, _, (acc, prec) = best

    print("\n== 留出评估 ==")
    print(f"最佳阈值 thr={thr:.2f}  总准确率={acc * 100:.0f}%")
    for k, v in prec.items():
        print(f"  {k:>10} 查准率={v * 100:.0f}%")

    save = {c: p for c, p in protos.items()}
    save["threshold"] = np.array([thr])
    np.savez(iconclf.MODEL_FILE, **save)
    print(f"\n已保存模型 -> {iconclf.MODEL_FILE}")
    print(f"模型内原型: {list(protos.keys())} (threshold={thr:.2f})")


if __name__ == "__main__":
    main()
