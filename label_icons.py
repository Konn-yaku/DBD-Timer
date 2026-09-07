# -*- coding: utf-8 -*-
"""标注工具：把 samples/raw/ 截屏里的 4 个幸存者整框，归类为训练样本。

用法:
    python label_icons.py

窗口显示截屏 + 4 个框（标 0-3）。对每一帧，依次为 4 个槽按状态键：
    1 = 上钩(hooked)      2 = 献祭(sacrificed)     3 = 其他/正常(normal)
快捷:
    空格 = 本帧 4 人全部“正常”（最常见的帧，一键跳过归档）
    Backspace = 撤销上一个输入
    a = 跳过本帧(不归档)
    q / Esc = 退出

归档位置: templates/icons/{normal,hooked,sacrificed}/<帧名>_slot<i>.png
攒够样本后运行: python train_icon_clf.py
"""
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dbdtimer.config import load as load_cfg  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE, "samples", "raw")
ICONS_DIR = os.path.join(BASE, "templates", "icons")
DONE_FILE = os.path.join(BASE, "samples", "label_done.txt")

# 按键(ASCII) -> 类别名
KEY2LABEL = {
    ord("1"): "hooked",
    ord("2"): "sacrificed",
    ord("3"): "normal",
}
# 类别名 -> BGR 显示色
LABEL2COLOR = {
    "hooked": (0, 0, 255),       # 红
    "sacrificed": (0, 120, 255), # 橙
    "normal": (0, 255, 0),       # 绿
}
KEY_HELP = "1=上钩  2=献祭  3=正常   空格=全正常  a=跳过本帧  q=退出"


def _crop_patch(frame, box):
    H, W = frame.shape[:2]
    x0 = max(0, int(box[0] * W)); y0 = max(0, int(box[1] * H))
    x1 = min(W, int(box[2] * W)); y1 = min(H, int(box[3] * H))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return frame[y0:y1, x0:x1].copy()


def _save_patch(patch, label, base, slot, counts):
    d = os.path.join(ICONS_DIR, label)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, f"{base}_slot{slot}.png")
    if cv2.imwrite(fp, patch):
        counts[label] = counts.get(label, 0) + 1


def _annotate_frame(img, boxes, picked, cursor):
    """返回显示用帧(含框、槽号、已选颜色)。picked: list of label/None, cursor: int。"""
    vis = img.copy()
    H, W = vis.shape[:2]
    for i, (box, lab) in enumerate(zip(boxes, picked)):
        x0 = int(box[0] * W); y0 = int(box[1] * H)
        x1 = int(box[2] * W); y1 = int(box[3] * H)
        col = LABEL2COLOR.get(lab, (255, 255, 255)) if lab else (255, 255, 255)
        if i == cursor:
            col = (0, 255, 255)  # 当前待输入槽：黄
        cv2.rectangle(vis, (x0, y0), (x1, y1), col, 3)
        cv2.putText(vis, f"{i}", (x0, max(10, y0 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    return vis


def main():
    cfg = load_cfg()
    boxes = cfg["hud"]["boxes"]
    if not boxes:
        print("config.json 里没有校准框，请先运行校准。")
        return
    if not os.path.isdir(RAW_DIR):
        print(f"找不到 {RAW_DIR}，请先运行 capture_frames.py 抓帧。")
        return

    done = set()
    if os.path.exists(DONE_FILE):
        with open(DONE_FILE, "r", encoding="utf-8") as f:
            done = {ln.strip() for ln in f if ln.strip()}

    frames = sorted(glob.glob(os.path.join(RAW_DIR, "*.png")))
    todo = [fp for fp in frames if os.path.basename(fp) not in done]
    counts = {}
    print(f"共 {len(frames)} 帧，待标注 {len(todo)} 帧\n{KEY_HELP}\n")

    cv2.namedWindow("label_icons", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("label_icons", 1200, 700)

    try:
        for fp in todo:
            img = cv2.imread(fp)
            if img is None:
                continue
            base = os.path.splitext(os.path.basename(fp))[0]
            picked = [None] * len(boxes)
            cursor = 0

            while True:
                vis = _annotate_frame(img, boxes, picked, cursor)
                tip = f"[{todo.index(fp) + 1}/{len(todo)}] {base}  槽{cursor}: {KEY_HELP}"
                cv2.setWindowTitle("label_icons", tip)
                cv2.imshow("label_icons", vis)
                key = cv2.waitKey(0) & 0xFF

                if key in KEY2LABEL:
                    picked[cursor] = KEY2LABEL[key]
                    cursor += 1
                    if cursor >= len(boxes):
                        break
                elif key == ord(" "):       # 全部正常
                    picked = ["normal"] * len(boxes)
                    break
                elif key == 8:              # Backspace 撤销
                    if cursor > 0:
                        cursor -= 1
                        picked[cursor] = None
                elif key in (ord("a"), ord("A")):
                    picked = None           # 跳过本帧
                    break
                elif key in (ord("q"), ord("Q"), 27):
                    print(f"退出（当前帧未归档）。各类计数: {counts}")
                    return

            if picked is None:
                continue    # 跳帧
            # 归档本帧 4 个 patch
            for i, lab in enumerate(picked):
                patch = _crop_patch(img, boxes[i])
                if patch is not None:
                    _save_patch(patch, lab, base, i, counts)
            with open(DONE_FILE, "a", encoding="utf-8") as f:
                f.write(os.path.basename(fp) + "\n")
            print(f"已归档 {base} -> {counts}")

    finally:
        cv2.destroyAllWindows()
    print("\n完成。下一步: python train_icon_clf.py")
    print(f"各类计数: {counts}")


if __name__ == "__main__":
    main()
