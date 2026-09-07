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


def test_window_title_match():
    print("== 窗口标题匹配(忽略空格/大小写) ==")
    from dbdtimer.gamewindow import pattern_matches
    check("Dead by Daylight 命中 DeadByDaylight",
          pattern_matches("Dead by Daylight", "DeadByDaylight") is True)
    check("DEAD BY DAYLIGHT 也命中",
          pattern_matches("DEAD BY DAYLIGHT", "deadbydaylight") is True)
    check("无关标题不命中",
          pattern_matches("Settings", "DeadByDaylight") is False)


def test_locator_rect_searches():
    """locator.rect() 每次都会执行搜索(find_hwnd)，不会因“先看 found”而死等。"""
    print("== 定位器搜索触发 ==")
    import dbdtimer.gamewindow as gw
    real_find, real_hrect = gw.find_hwnd, gw.hwnd_rect
    gw.find_hwnd = lambda title: "FAKE_HWND"
    gw.hwnd_rect = lambda h: (10, 20, 800, 600) if h == "FAKE_HWND" else None
    try:
        loc = gw.WindowLocator("DeadByDaylight", cache_seconds=10)
        r = loc.rect()
        check("rect() 触发搜索并返回矩形", r == (10, 20, 800, 600))
        check("搜索后 found=True", loc.found is True)
    finally:
        gw.find_hwnd, gw.hwnd_rect = real_find, real_hrect


def test_config():
    print("== 配置结构 ==")
    cfg = load_cfg()
    check("配置为 dict 且含关键段",
          isinstance(cfg, dict) and all(k in cfg for k in ("game", "hud", "keys", "overlay", "detect")))
    check("hud.boxes 为列表", isinstance(cfg["hud"]["boxes"], list))
    check("overlay 含计时颜色", "color_protection" in cfg["overlay"] and "color_ds" in cfg["overlay"])
    from dbdtimer.hotkey import to_vk
    check("手动键可被识别", to_vk(cfg["keys"]["manual_start"]) is not None)


def test_hotkey_map():
    print("== 热键虚拟键码 ==")
    from dbdtimer.hotkey import to_vk, canonical_name, key_candidates
    check("XBUTTON1 -> 0x05", to_vk("XBUTTON1") == 0x05)
    check("XBUTTON2 -> 0x06", to_vk("XBUTTON2") == 0x06)
    check("F8 -> 0x77", to_vk("F8") == 0x77)
    check("canonical 0x06=XBUTTON2", canonical_name(0x06) == "XBUTTON2")
    check("canonical 0x41=A", canonical_name(0x41) == "A")
    check("候选键非空", len(key_candidates()) > 30)


def test_watcher_single_string_combo():
    """回归：单个按键字符串(F1/XBUTTON2)不能被当作字符列表拆开。"""
    print("== KeyWatcher 单键注册 ==")
    from dbdtimer.hotkey import KeyWatcher
    w = KeyWatcher()
    w.add("F1", lambda: None)
    w.add("XBUTTON2", lambda: None)
    _item0, _item1 = w._items
    check("F1 无修饰键", _item0[1] == [])
    check("F1 主键 vk=0x70", _item0[2] == 0x70)
    check("XBUTTON2 主键 vk=0x06", _item1[2] == 0x06)
    w.add(["Ctrl", "Alt", "L"], lambda: None)
    check("组合键仍正常(2个修饰)", w._items[2][1] == [0x11, 0x12])


def test_watcher_arming():
    """启动/恢复时若主键已被按住，不应误触发；松开后再按才触发且不重复。"""
    print("== KeyWatcher 武装门 ==")
    import dbdtimer.hotkey as hk
    real = hk._is_down
    down = set()
    hk._is_down = lambda vk: vk in down
    try:
        w = hk.KeyWatcher()
        fired = []
        w.add("F1", lambda: fired.append(1))
        down.add(0x70)
        w._poll()                     # 启动瞬间 F1 被按住 => 不应触发
        check("启动即按住不误触发", len(fired) == 0)
        down.clear()
        w._poll()                     # 松开 => 武装完成
        down.add(0x70)
        w._poll()                     # 按下 => 触发一次
        check("武装后按下触发1次", len(fired) == 1)
        w._poll()                     # 持续按住 => 不重复
        check("按住不重复触发", len(fired) == 1)
        down.clear()
        w._poll()
        down.add(0x70)
        w._poll()                     # 松开再按 => 可再次触发
        check("松开再按可再次触发", len(fired) == 2)
    finally:
        hk._is_down = real


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
    import shutil
    import tempfile

    import dbdtimer.detector as D
    from dbdtimer.detector import Detector

    # 隔离到临时模板目录：避免“本机已拍真实模板”影响分支选择（无模板=simple / 有模板=倒地豁免）
    tmp = tempfile.mkdtemp(prefix="dbd_tpl_")
    orig_tpl = D.TEMPLATES_DIR
    D.TEMPLATES_DIR = tmp
    try:
        os.makedirs(os.path.join(tmp, "hooked_diff"))
        os.makedirs(os.path.join(tmp, "downed_diff"))

        def make_det(events):
            cfg = {"detect": {
                "confirm_frames": 1, "alive_threshold": 0.6,
                "state_threshold": 0.5, "simple_mode": True,
                "debug_frames": False,
            }}
            return Detector(cfg, on_unhook=lambda i, ts: events.append((i, ts)))

        boxes = [[0.2, 0.2, 0.8, 0.8]]   # 只处理槽0
        W, H = 160, 120
        grad = np.tile(np.linspace(0, 255, W, dtype=np.float32), (H, 1)).astype(np.uint8)
        alive = cv2_merge(grad)
        changed = cv2_merge(255 - grad)   # 反相 => 与 alive 参考强负相关

        # ---- A. 无模板 => simple：长偏离→恢复触发；短抖动不触发 ----
        events = []
        det = make_det(events)
        t = time.monotonic()
        for _ in range(2):
            det.process(alive, boxes, t)
        check("A 初始化为 ALIVE(无事件)", len(events) == 0)
        det.process(changed, boxes, t + 1)   # 短偏离(<min_event_s=2.5s)
        check("A 短偏离不进入事件态", det.slots[0].committed == "alive")
        check("A 短偏离不触发", len(events) == 0, f"events={events}")
        det.process(changed, boxes, t + 4)   # 偏离累计>=2.5s → 进入事件态
        check("A 长偏离进入事件态", det.slots[0].committed == "changed")
        det.process(alive, boxes, t + 5)     # 恢复 → 触发下钩
        check("A 长偏离后恢复=>触发1个", len(events) == 1, f"events={events}")
        for i in range(4):
            det.process(changed, boxes, t + 6 + i)
        check("A 持续偏离不重复触发", len(events) == 1, f"events={events}")

        # ---- B. 有差异模板：状态机语义（直接驱动 _advance 做单元验证）----
        import cv2
        stub = np.full((32, 32), 128, np.uint8)
        cv2.imwrite(os.path.join(tmp, "hooked_diff", "stub.png"), stub)
        cv2.imwrite(os.path.join(tmp, "downed_diff", "stub.png"), stub)

        events = []
        det = make_det(events)          # 现在 has_templates=True
        sl = det.slots[0]
        t = time.monotonic()
        det._advance(sl, "hooked", t)              # prev=None → 进入事件态
        fired = det._advance(sl, "alive", t + 1)   # 上钩→恢复 → 触发
        check("B 上钩→恢复 触发", fired is True)
        det._advance(sl, "downed", t + 2)          # 短倒地(<2.5s)不进入
        det._advance(sl, "downed", t + 4)          # last_alive=t+1, 3s>=2.5 → 进入倒地
        check("B 进入倒地态", sl.committed == "downed")
        fired = det._advance(sl, "alive", t + 4.1)  # 倒地恢复(拉起/自起) → 不触发
        check("B 倒地→恢复 不触发(拉起/自起)", fired is not True)
        det._advance(sl, "changed", t + 4.2)       # 短偏离不进入
        det._advance(sl, "changed", t + 7.0)       # last_alive=t+4.1, 2.9s>=2.5 → 进入
        fired = det._advance(sl, "alive", t + 7.1)  # 未知偏离恢复 → 触发(防漏报)
        check("B 未知偏离→恢复 触发(防漏报)", fired is True)

        # ---- C. 秒级抖动：短偏离(<min_event_s)后恢复，绝不触发 ----
        det.reset()
        sl = det.slots[0]
        sl.committed = "alive"; sl.raw = "alive"; sl.last_alive = t + 20
        fired = det._advance(sl, "changed", t + 20.2)
        check("C 短偏离不进入事件态", fired is False and sl.committed == "alive")
        fired = det._advance(sl, "alive", t + 20.4)
        check("C 抖动恢复不触发", fired is False)
    finally:
        D.TEMPLATES_DIR = orig_tpl
        shutil.rmtree(tmp, ignore_errors=True)


def test_icon_hook_state():
    """图标状态机：only 钩上→离开(非献祭) 触发；献祭/死亡屏蔽；防抖。"""
    print("== 图标状态机(下钩检测) ==")
    from dbdtimer.iconstate import IconHookDetector

    def mk():
        ev = []
        det = IconHookDetector(on_unhook=lambda i, ts: ev.append((i, ts)),
                               n=4, confirm=2, post_window_s=0.5, dead_idle_s=1.0)
        return det, ev

    def feed(det, cats, dt=0.3):
        t = 0.0
        for c in cats:
            others = ["other"] * 4
            others[0] = c
            det.process(others, t)
            t += dt

    # 防抖：单帧 hooked 抖动不进入/不触发
    det, ev = mk()
    feed(det, ["other", "other", "hooked", "other", "other", "other"])
    check("图标 单帧hooked抖动不触发", len(ev) == 0, f"ev={ev}")

    # 下钩：hooked 持续(>=confirm) → 离开且非献祭 → 触发
    det, ev = mk()
    feed(det, ["other", "other", "hooked", "hooked",
               "other", "other", "other", "other"])
    check("图标 上钩→恢复 触发下钩", len(ev) == 1, f"ev={ev}")

    # 献祭：hooked→sacrificed 不触发，死后屏蔽
    det, ev = mk()
    feed(det, ["other", "other", "hooked", "hooked",
               "sacrificed", "sacrificed", "other", "other", "other"])
    check("图标 献祭不触发", len(ev) == 0, f"ev={ev}")

    # 死亡屏蔽解除(视为新局)后可再次触发（单条连续时间序列）
    det, ev = mk()
    feed(det, (["other", "other", "hooked", "hooked", "sacrificed", "sacrificed"]
               + ["other"] * 6            # 超过 dead_idle_s(1.0s) → 释放屏蔽
               + ["hooked", "hooked"]     # 新一局再次上钩
               + ["other"] * 4))          # 真正下钩 → 触发
    check("图标 献祭屏蔽解除后可再触发", len(ev) == 1, f"ev={ev}")

    # 离开钩上不足窗口又回钩(仍挂着)：不触发；真正下钩才触发 1 次
    det, ev = mk()
    feed(det, ["other", "other", "hooked", "hooked", "other",
               "hooked", "hooked", "other", "other", "other", "other", "other"])
    check("图标 离开又回钩不触发，真正下钩触发", len(ev) == 1, f"ev={ev}")

    # 误判 dead 自愈：死人不会上钩，DEAD 中若持续出现 hooked → 解除屏蔽，随后下钩可触发
    det, ev = mk()
    feed(det, (["other", "other", "hooked", "hooked", "sacrificed", "sacrificed"]
               + ["hooked", "hooked"]        # DEAD 中再持续 hooked → 自愈解除
               + ["other"] * 4))             # 随后真实下钩 → 触发
    check("图标 误判dead后可自愈并触发", len(ev) == 1, f"ev={ev}")


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
    test_window_title_match()
    test_locator_rect_searches()
    test_config()
    test_hotkey_map()
    test_watcher_single_string_combo()
    test_watcher_arming()
    test_overlay_offscreen()
    test_overlay_render_pixels()
    test_detector_synthetic()
    test_icon_hook_state()
    print(f"\n全部通过: {len(PASS)} 项")


if __name__ == "__main__":
    main()
