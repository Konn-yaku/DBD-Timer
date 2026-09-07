# -*- coding: utf-8 -*-
"""DBD 下钩计时助手 - 入口。

用法：
  python app.py              正常使用（自动识别 + 鼠标侧键手动兜底）
  python app.py --demo       无游戏演示悬浮窗效果（每几秒自动触发一次）
  python app.py --calibrate  校准：框选 4 个头像 + 拍三态模板
  python app.py --debug      打开调试：识别画面存到 debug/ 目录

运行前提：在虚拟环境里运行，例如  .venv\\Scripts\\python.exe app.py
"""
import argparse
import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from dbdtimer import capture, detector as detector_mod, gamewindow, hotkey, iconengine, overlay
from dbdtimer.config import load as load_cfg
from dbdtimer.timers import TimerBank

_KEY_LABEL = {
    "XBUTTON1": "鼠标侧键1(XBUTTON1)",
    "XBUTTON2": "鼠标侧键2(XBUTTON2)",
}


def _now():
    return time.strftime("%H:%M:%S")


def _key_label(name):
    name = name if isinstance(name, str) else "+".join(name)
    return _KEY_LABEL.get(name, name)


def run_demo(app, cfg):
    """演示模式：无需游戏，定时触发两个槽位，便于查看悬浮窗效果。"""
    bank = TimerBank()
    ov = overlay.OverlayWindow(cfg, bank)
    ov.show()
    state = {"n": 0}

    def auto_trigger():
        state["n"] += 1
        got = ov.slot_start()
        print(f"[{_now()}] demo 触发 第{state['n']}次 分配={got}")
        if state["n"] >= 4:   # 触发几次后只靠既有槽位自然走完
            timer.stop()

    timer = QTimer()
    timer.timeout.connect(auto_trigger)
    timer.start(6000)
    print("[demo] 悬浮窗已显示（右下角），约每 6 秒自动触发一次。关闭终端即可退出。")
    return app.exec()


def run_normal(app, cfg):
    """正常模式：悬浮窗 + 手动热键 + 画面自动识别(需先校准)。"""
    bank = TimerBank()
    ov = overlay.OverlayWindow(cfg, bank)
    ov.show()

    keys = cfg["keys"]
    manual_label = _key_label(keys["manual_start"])
    locator = gamewindow.WindowLocator(cfg["game"]["window_title"])
    source = capture.FrameSource(locator)

    # 引擎选择：icon=图标识别(需已训练模型) / face=旧整框比对 / auto=有图标模型用icon否则face
    def _make_detector():
        eng = str(cfg["detect"].get("engine", "auto")).lower()
        icon = iconengine.IconEngine(cfg, on_unhook=lambda i, ts: _on_unhook(i, ts, ov))
        if eng == "icon" or (eng == "auto" and icon.ready):
            icon.is_icon = True
            return icon
        d = detector_mod.Detector(cfg, on_unhook=lambda i, ts: _on_unhook(i, ts, ov))
        d.is_icon = False
        return d

    det = _make_detector()

    boxes = cfg["hud"]["boxes"]
    auto_on = bool(cfg["detect"].get("auto", True))
    require_fg = bool(cfg["detect"].get("require_foreground", True))
    _hinted_no_boxes = not boxes
    _hinted_no_window = False
    _reported_found = False
    _was_fg = None   # None=尚未评估前台；False=在后台；True=在前台

    def _on_unhook(idx, ts, ov_ref):
        ok = ov_ref.slot_start()
        print(f"[{_now()}] 自动识别到一次下钩 → 槽{idx} 分配={'成功' if ok else '已满(忽略)'}")

    def _on_manual():
        ok = ov.slot_start()
        cur = _key_label(keys["manual_start"])
        print(f"[{_now()}] 手动触发 {cur} → 分配={'成功' if ok else '两槽都在计时(忽略)'}")

    def _on_lock():
        ov.toggle_lock()
        print(f"[{_now()}] 悬浮窗 锁定={ov._locked}")

    def _on_quit():
        print(f"[{_now()}] 退出")
        app.quit()

    watcher = hotkey.KeyWatcher()

    def _configure_watcher():
        """(重新)注册全部按键——重绑手动键后也靠它重建。"""
        watcher.clear()
        try:
            watcher.add(keys["manual_start"], _on_manual)
            watcher.add(keys["toggle_lock"], _on_lock)
        except ValueError as exc:
            print(f"[hotkey] {exc}")
        watcher.add(["Ctrl", "Alt", "Q"], _on_quit)   # 退出

    def _on_rebind():
        from dbdtimer.config import save as save_cfg
        from dbdtimer.keycapture import KeyCaptureDialog
        # 捕获期间暂停按键监听：避免“用来改键的那次按键”同时触发计时
        watcher.stop()
        accepted = False
        try:
            dlg = KeyCaptureDialog(current=_key_label(keys["manual_start"]), parent=ov)
            if dlg.exec() == 1 and dlg.key_name:          # QDialog.Accepted
                keys["manual_start"] = dlg.key_name
                save_cfg(cfg)
                _configure_watcher()                      # 用新键重建监听
                accepted = True
        finally:
            watcher.start()                               # 重新武装后再恢复监听
        if accepted:
            print(f"[{_now()}] 手动计时键已改为: {_key_label(dlg.key_name)}")

    ov.rebind_requested.connect(_on_rebind)
    _configure_watcher()
    watcher.start()

    # 自动识别主循环
    interval = max(40, int(1000.0 / max(1.0, float(cfg["detect"].get("fps", 15.0)))))
    auto_timer = QTimer()
    auto_timer.setInterval(interval)

    def _auto_tick():
        nonlocal _hinted_no_boxes, _hinted_no_window, _reported_found, _was_fg
        if not auto_on:
            return
        if not boxes:
            if not _hinted_no_boxes:
                _hinted_no_boxes = True
                print(f"[{_now()}] 尚未校准头像框，自动识别关闭。请先运行: "
                      f"python app.py --calibrate （或按 {manual_label} 手动计时）")
            return
        # 关键：每次都主动调用 locator.rect() 触发窗口搜索(带1s缓存)，
        # 而不是先看 locator.found（found 只有在搜索后才会变真，会成死循环）
        win_rect = locator.rect()
        if win_rect is None:
            if not _hinted_no_window:
                _hinted_no_window = True
                print(f"[{_now()}] 未找到 DBD 窗口，自动识别等待中……（仅手动 {manual_label} 可用）")
            return
        # 前台守卫：窗口被遮挡/最小化时，抓屏抓到的是遮挡物而非游戏画面，
        # 会疯狂误触发，故暂停识别；切回游戏窗口后自动恢复并重建基线。
        if require_fg and not locator.foreground():
            if _was_fg is not False:
                _was_fg = False
                print(f"[{_now()}] DBD 不在前台（被遮挡/最小化），已暂停自动识别。"
                      f"切回游戏窗口会自动恢复（避免把聊天/桌面误判成下钩）")
            return
        if _was_fg is False:
            det.reset()   # 离开前台期间画面可能已大变(可能已有人上钩/倒地)，重建基线
            print(f"[{_now()}] 回到 DBD 前台，重建识别基线")
        _was_fg = True
        if not _reported_found:
            _reported_found = True
            print(f"[{_now()}] 已找到 DBD 窗口，画面自动识别开始")
        _hinted_no_window = False
        frame, rect = source.grab()
        if frame is None or rect is None:
            return
        # Detector 触发时会回调 on_unhook，由 overlay 分配槽位
        det.process(frame, boxes)

    auto_timer.timeout.connect(_auto_tick)
    auto_timer.start()

    print(f"[{_now()}] DBD 下钩计时助手已启动")
    print(f"        手动计时: 按 {manual_label}（可点悬浮窗左上『键』按钮随时改）")
    print(f"        锁定/解锁悬浮窗: 点悬浮窗🔒按钮 或 {'+'.join(keys['toggle_lock'])}")
    print(f"        退出: Ctrl+Alt+Q")
    if boxes:
        if getattr(det, "is_icon", False):
            print(f"        已加载 {len(boxes)} 个头像框，自动识别开启（图标识别模式，已加载训练模型）")
        else:
            print(f"        已加载 {len(boxes)} 个头像框，自动识别开启"
                  + (f"（hooked模板{len(det.pool_hooked)}张/downed模板{len(det.pool_downed)}张）"
                     if det.has_templates else "（未拍模板：simple 模式，倒地拉起可能误报）"))
    else:
        print(f"        未校准头像框：自动识别关闭，仅手动 {manual_label} 可用")
    return app.exec()


def run_calibrate(app, cfg):
    from dbdtimer.calibration import CalibrationDialog
    locator = gamewindow.WindowLocator(cfg["game"]["window_title"])
    source = capture.FrameSource(locator)
    dlg = CalibrationDialog(cfg, locator, source)
    dlg.exec()
    print(f"[{_now()}] 校准结束，已保存框数量: {len(cfg['hud']['boxes'])}")


def main():
    # 统一输出编码为 UTF-8（避免部分终端/管道下打印 emoji/中文时 GBK 报错）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="DBD 下钩计时助手")
    parser.add_argument("--demo", action="store_true", help="无游戏演示悬浮窗")
    parser.add_argument("--calibrate", action="store_true", help="校准头像框/模板")
    parser.add_argument("--debug", action="store_true", help="开启调试帧保存")
    args = parser.parse_args()

    cfg = load_cfg()
    if args.debug:
        cfg["detect"]["debug_frames"] = True

    # 静音 Qt 的 DPI 提示（无害：系统已由其他组件设为按显示器感知，Qt 无法重复设置）
    _rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    _extra = "qt.qpa.window=false"
    os.environ["QT_LOGGING_RULES"] = (_rules + ";" + _extra) if _rules else _extra

    app = QApplication(sys.argv)
    app.setApplicationName("DBD Timer")

    # 供自检/CI 使用：设置 DBDTIMER_AUTOQUIT_MS=<毫秒> 可让程序自动退出
    try:
        _aq = int(os.environ.get("DBDTIMER_AUTOQUIT_MS", "0") or 0)
        if _aq > 0:
            QTimer.singleShot(_aq, app.quit)
    except ValueError:
        pass

    try:
        if args.calibrate:
            return run_calibrate(app, cfg)
        if args.demo:
            return run_demo(app, cfg)
        return run_normal(app, cfg)
    finally:
        try:
            from dbdtimer.config import ensure_dirs
            ensure_dirs()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
