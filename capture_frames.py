# -*- coding: utf-8 -*-
"""独立截屏程序：每隔一段时间抓一帧 DBD 窗口画面，存到 samples/raw/。

用法:
    python capture_frames.py --interval 4
    python capture_frames.py --interval 3 --max 800 --allow-bg

参数:
    --interval   抓帧间隔(秒)，默认 4
    --max        最多保存多少帧后自动停，默认 2000
    --allow-bg   允许 DBD 不在前台时也抓(不推荐：被遮挡时抓到的是别的窗口)

提示：请让 DBD 窗口在最前面运行本程序，抓到的才是游戏画面。
按 Ctrl+C 停止。抓完运行:  python label_icons.py
"""
import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dbdtimer.capture import FrameSource  # noqa: E402
from dbdtimer.config import load as load_cfg  # noqa: E402
from dbdtimer.gamewindow import WindowLocator  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=4.0)
    ap.add_argument("--max", type=int, default=2000)
    ap.add_argument("--allow-bg", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples", "raw")
    os.makedirs(out, exist_ok=True)

    loc = WindowLocator(cfg["game"]["window_title"])
    src = FrameSource(loc)
    n = 0
    print(f"开始抓帧: 每 {args.interval}s 一帧, 上限 {args.max}, 存到 {out}")
    print("（默认只在 DBD 前台时抓，避免存到被遮挡的画面）")
    no_win_hint = no_fg_hint = False
    try:
        while n < args.max:
            # 关键：先调用 rect() 触发窗口查找(带缓存)，否则 _hwnd 为空、
            # foreground() 恒为 False，永远抓不到。
            if loc.rect() is None:
                if not no_win_hint:
                    no_win_hint = True
                    print("未找到 DBD 窗口，正在后台重试（请确认 DBD 已启动）……")
                time.sleep(args.interval)
                continue
            no_win_hint = False
            if not args.allow_bg and not loc.foreground():
                if not no_fg_hint:
                    no_fg_hint = True
                    print("已找到 DBD 但不在前台，暂停抓帧（切回游戏窗口即自动继续）……")
                time.sleep(args.interval)
                continue
            no_fg_hint = False
            frame, rect = src.grab()
            if frame is not None and rect is not None:
                ts = int(time.time())
                fp = os.path.join(out, f"f_{ts:010d}_{n:04d}.png")
                cv2.imwrite(fp, frame)
                n += 1
                if n == 1 or n % 10 == 0:
                    print(f"  已保存 {n} 帧 -> {os.path.basename(fp)}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    print(f"停止，共保存 {n} 帧。下一步运行: python label_icons.py")


if __name__ == "__main__":
    main()
