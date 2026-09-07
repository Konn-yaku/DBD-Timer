# -*- coding: utf-8 -*-
"""训练“上钩/献祭”原型分类器，含自动定位的“图标判别区 mask”。

设计要点：
- other/normal 是开放集(任何角色头像/受伤/倒地…)，**不建脸原型**。
- 只对 hooked/sacrificed 建均值原型。
- **判别区 mask**：hooked 均值 与 normal 均值差异大的像素 = 固定的上钩/献祭图标区。
  预测时只在该区域算相关，把随角色/地图变化的脸与背景排除，
  → 换没见过的角色也不会因“脸不同”而漏判/误判。
- normal 样本仅用于：(a) 计算 mask；(b) 校验“正常帧误判率”并挑选阈值。

用法: python train_icon_clf.py
输出: templates/icon_model.npz (hooked/sacrificed 原型 + mask + threshold)
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
POSITIVE = ("hooked", "sacrificed")
SEED = 42
MAX_NORMAL_FP = 0.02      # 允许正常帧被误判成正类的上限
MASK_PCT = 85             # mask=差异图第 MASK_PCT 分位以上像素


def load_images():
    out = {}
    for c in CLASSES:
        d = os.path.join(iconclf.TEMPLATES_DIR, "icons", c)
        imgs = []
        for fp in sorted(glob.glob(os.path.join(d, "*.png"))):
            img = cv2.imread(fp)
            if img is not None:
                imgs.append(img)
        out[c] = imgs
        print(f"[{c:>10}] {len(imgs):3d} 张")
    return out


def split_indices(n):
    idx = np.random.RandomState(SEED).permutation(n)
    k = max(1, int(n * 0.7))
    return idx[:k], idx[k:]


def main():
    data = load_images()
    if sum(len(v) for v in data.values()) < 6:
        print("\n样本太少(<6)，请先用 capture_frames + label_icons 采集。")
        return

    # ---- 判别区 mask：hooked 与 normal 均值差异大的像素(固定图标区) ----
    mh = np.mean([iconclf._prep(x) for x in data["hooked"]], axis=0)
    mn = np.mean([iconclf._prep(x) for x in data["normal"]], axis=0)
    diff = np.abs(mh - mn)
    mask = (diff > float(np.percentile(diff, MASK_PCT))).ravel()
    print(f"\n判别区 mask 像素占比={mask.mean() * 100:.0f}%（图标固定区）")

    # ---- 划分训练/留出 ----
    tri, tei = {}, {}
    for c in CLASSES:
        a, b = split_indices(len(data[c]))
        tri[c], tei[c] = a, b

    # ---- 正类均值原型(只用训练部分) ----
    protos = {}
    for c in POSITIVE:
        arr = [iconclf._prep(data[c][i]).ravel() for i in tri[c]]
        protos[c] = np.mean(arr, axis=0).astype(np.float32)

    # ---- 阈值扫描：normal 误判 <= 上限 下最大化两正类查准 ----
    best = (None, -1, None)
    for thr100 in range(55, 91, 5):
        thr = thr100 / 100.0
        clf = iconclf.PrototypeIconClassifier(protos, threshold=thr, mask=mask)
        pos_acc = {}
        for c in POSITIVE:
            ok = sum(1 for i in tei[c] if clf.predict(data[c][i]) == c)
            pos_acc[c] = ok / max(1, len(tei[c]))
        nfp = sum(1 for i in tei["normal"]
                  if clf.predict(data["normal"][i]) in POSITIVE) / max(1, len(tei["normal"]))
        if nfp <= MAX_NORMAL_FP:
            score = (pos_acc["hooked"] + pos_acc["sacrificed"]) / 2
            if score > best[1]:
                best = (thr, score, (pos_acc, nfp))

    if best[0] is None:
        print("\n各阈值下 normal 误判都 >2%，请清理 normal 样本或降低 MAX_NORMAL_FP。")
        return
    thr, _, (pos_acc, nfp) = best

    print("\n== 留出评估(阈值=%.2f, 带判别区mask) ==" % thr)
    for c in POSITIVE:
        print(f"  {c:>10} 查准率={pos_acc[c] * 100:.0f}%")
    print(f"  {'normal':>10} 误判成正类比例={nfp * 100:.1f}%")

    save = {c: protos[c] for c in POSITIVE}
    save["threshold"] = np.array([thr])
    save["mask"] = mask
    np.savez(iconclf.MODEL_FILE, **save)
    print(f"\n已保存模型 -> {iconclf.MODEL_FILE}  (threshold={thr:.2f}, 含判别区 mask)")


if __name__ == "__main__":
    main()
