# -*- coding: utf-8 -*-
"""冒烟测试：计时内核 / 配置 / 悬浮窗(离屏) / 检测器(合成帧)。

运行：
    .\\.venv\\Scripts\\python.exe tests_smoke.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # 无窗口跑 GUI 相关测试

import numpy as np

from dbdtimer.config import load as load_cfg
from dbdtimer.timers import TimerBank

PASS = []


def check(name, cond, extra=""):
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}  {extra}")
        raise SystemExit(1)


def test_timers():
    print("== TimerBank 双槽规则 ==")
    b = TimerBank()
    t0 = 1000.0
    assert b.trigger(t0) == 0
    assert b.trigger(t0 + 1) == 1
    assert b.trigger(t0 + 2) is None          # 第三个忽略
    assert len(b.active_slots()) == 2

    r = b.sample(t0 + 5)
    by_idx = {x["idx"]: x for x in r}
    check("槽0 elapsed=5", abs(by_idx[0]["elapsed"] - 5.0) < 1e-6)
    check("槽0 保护期=True", by_idx[0]["protection"] is True)
    check("槽0 finished=False", by_idx[0]["finished"] is False)

    r = b.sample(t0 + 61)
    check("61s 时两槽同时 finished",
          len(r) == 2 and all(x["finished"] for x in r))
    check("60s 后无活动槽", len(b.active_slots()) == 0)
    assert b.trigger(t0 + 62) == 0            # 释放后可复用
    check("释放后可再次使用槽0", True)

    # 颜色阶段由 overlay 依据 protection 决定；此处验证保护期边界定义
    b2 = TimerBank()
    b2.trigger(t0)
    s = {x["idx"]: x for x in b2.sample(t0 + 9.99)}[0]
    check("9.99s 仍在保护期", s["protection"] is True)
    s = {x["idx"]: x for x in b2.sample(t0 + 10.01)}[0]
    check("10.01s 保护期结束", s["protection"] is False)


def test_config():
    print("== 配置结构 ==")
    cfg = load_cfg()
    check("配置为 dict 且含关键段",
          isinstance(cfg, dict) and all(k in cfg for k in ("game", "hud", "keys", "overlay", "detect")))
    check("hud.boxes 为列表", isinstance(cfg["hud"]["boxes"], list))
    check("overlay 含计时颜色", "color_protection" in cfg["overlay"] and "color_ds" in cfg["overlay"])
    check("手动键可被识别", cfg["keys"]["manual_start"] in ("XBUTTON1", "XBUTTON2", "F8"))


def test_hotkey_map():
    print("== 热键虚拟键码 ==")
    from dbdtimer.hotkey import to_vk
    check("XBUTTON1 -> 0x05", to_vk("XBUTTON1") == 0x05)
    check("XBUTTON2 -> 0x06", to_vk("XBUTTON2") == 0x06)
    check("F8 -> 0x77", to_vk("F8") == 0x77)


def test_overlay_offscreen():
    print("== 悬浮窗(离屏) ==")
    from PySide6.QtWidgets import QApplication
    from dbdtimer.overlay import OverlayWindow
    from dbdtimer.config import CONFIG_PATH

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = load_cfg()
    existed = os.path.exists(CONFIG_PATH)
    orig_cfg = None
    if existed:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            orig_cfg = f.read()
    cfg["overlay"].update({"beep": False, "locked": False,
                           "x": -1.0, "y": -1.0, "font_px": 52,
                           "color_protection": "#FFD600", "color_ds": "#FFFFFF"})
    bank = TimerBank()
    ov = OverlayWindow(cfg, bank)
    check("启动分配槽0", ov.slot_start() is True)
    check("再分配槽1", ov.slot_start() is True)
    check("第三次被忽略", ov.slot_start() is False)

    # 模拟时间流逝，验证数字与颜色
    now = time.monotonic()
    bank.slots[0].started = now - 5.0     # 5s -> 黄色(保护期)
    bank.slots[1].started = now - 25.0    # 25s -> 白色(仅DS)
    ov._tick()
    check("槽0 显示 5", ov._labels[0]._text == "5")
    check("槽0 黄色", ov._labels[0]._color.name() == "#ffd600",
          ov._labels[0]._color.name())
    check("槽1 显示 25", ov._labels[1]._text == "25")
    check("槽1 白色", ov._labels[1]._color.name() == "#ffffff",
          ov._labels[1]._color.name())

    # 61s -> 槽释放，标签清空
    bank.slots[0].started = now - 61.0
    ov._tick()
    check("61s 后槽0 清空", ov._labels[0]._text == "")

    # 锁定切换不报错
    ov.toggle_lock()
    check("锁定切换正常", ov._locked is True)
    ov.toggle_lock()
    ov.close()
    app.processEvents()
    # 还原 config.json（测试可能写入了位置/锁定状态），避免污染用户配置
    if existed and orig_cfg is not None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(orig_cfg)
    elif not existed and os.path.exists(CONFIG_PATH):
        try:
            os.remove(CONFIG_PATH)
        except OSError:
            pass


def test_detector_synthetic():
    print("== 检测器状态机(合成帧) ==")
    from dbdtimer.detector import Detector

    cfg = {"detect": {
        "confirm_frames": 1, "alive_threshold": 0.6,
        "state_threshold": 0.5, "simple_mode": True, "debug_frames": False,
    }}
    events = []
    det = Detector(cfg, on_unhook=lambda idx, ts: events.append((idx, ts)))
    boxes = [[0.2, 0.2, 0.8, 0.8]]   # 只处理槽0

    W, H = 160, 120
    grad = np.tile(np.linspace(0, 255, W, dtype=np.float32), (H, 1)).astype(np.uint8)
    alive = cv2_merge(grad)
    changed = cv2_merge(255 - grad)   # 反相 => 与 alive 参考强负相关

    t = time.monotonic()
    for _ in range(2):
        det.process(alive, boxes, t)
    check("初始化为 ALIVE(无事件)", len(events) == 0)
    det.process(changed, boxes, t + 1)
    check("偏离后仍未触发(还需恢复)", len(events) == 0)
    det.process(alive, boxes, t + 2)
    check("恢复=>触发下钩事件1个", len(events) == 1, f"events={events}")

    # 三挂牺牲：偏离后没有恢复(保持死亡/偏离画面)，不应再触发
    det.process(changed, boxes, t + 3)
    for i in range(3):
        det.process(changed, boxes, t + 4 + i)
    det.process(changed, boxes, t + 8)   # 死亡画面持续偏离，无恢复
    check("牺牲/持续偏离不触发", len(events) == 1, f"events={events}")


def test_overlay_render_pixels():
    """离屏抓图验证：数字是否真的画出来了(黄/白像素计数)。"""
    print("== 悬浮窗像素级渲染自检 ==")
    from PySide6.QtWidgets import QApplication
    from dbdtimer.overlay import OverlayWindow

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = load_cfg()
    cfg["overlay"].update({"beep": False, "locked": False,
                           "x": -1.0, "y": -1.0, "font_px": 52,
                           "color_protection": "#FFD600", "color_ds": "#FFFFFF"})
    bank = TimerBank()
    ov = OverlayWindow(cfg, bank)
    ov.show()
    now = time.monotonic()
    ov.slot_start()
    bank.slots[0].started = now - 5.0          # 黄色 '5'（保护期）
    ov._tick()
    ov.repaint()
    app.processEvents()
    img = ov.grab().toImage()
    yellow = white = 0
    for yy in range(img.height()):
        for xx in range(img.width()):
            c = img.pixelColor(xx, yy)
            if c.red() > 200 and c.green() > 170 and c.blue() < 100:
                yellow += 1
            elif c.red() > 225 and c.green() > 225 and c.blue() > 225:
                white += 1
    check("渲染出现黄色数字像素", yellow > 50, f"yellow={yellow}")
    ov.slot_start()
    bank.slots[1].started = now - 25.0         # 白色 '25'
    ov._tick()
    ov.repaint()
    app.processEvents()
    img = ov.grab().toImage()
    white = 0
    for yy in range(img.height()):
        for xx in range(img.width()):
            c = img.pixelColor(xx, yy)
            if c.red() > 225 and c.green() > 225 and c.blue() > 225:
                white += 1
    check("渲染出现白色数字像素", white > 50, f"white={white}")
    ov.close()
    app.processEvents()


def cv2_merge(gray):
    import cv2
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def main():
    test_timers()
    test_config()
    test_hotkey_map()
    test_overlay_offscreen()
    test_overlay_render_pixels()
    test_detector_synthetic()
    print(f"\n全部通过: {len(PASS)} 项")


if __name__ == "__main__":
    main()
