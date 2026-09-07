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
import math
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

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
    """把快捷键(字符串或列表)显示成可读文案，鼠标侧键等映射成中文。"""
    if isinstance(name, str):
        return _KEY_LABEL.get(name, name)
    return "+".join(_KEY_LABEL.get(p, p) for p in name)


def _make_tray_icon():
    """运行时画一个简单的“时钟/计时”托盘图标，无需额外图片资源。"""
    s = 64
    pm = QPixmap(s, s)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(38, 42, 58))          # 深色圆底
    p.drawEllipse(1, 1, s - 2, s - 2)
    p.setPen(QPen(QColor(255, 214, 0), max(2, s // 16)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(int(s * 0.16), int(s * 0.16), int(s * 0.68), int(s * 0.68))

    def _hand(deg, length, width, color):
        a = math.radians(deg - 90.0)
        x2 = s / 2 + math.cos(a) * length
        y2 = s / 2 + math.sin(a) * length
        p.setPen(QPen(QColor(*color), max(2, width)))
        p.drawLine(s // 2, s // 2, int(x2), int(y2))

    _hand(0, s * 0.20, s // 12, (255, 255, 255))    # 分针→12
    _hand(60, s * 0.14, s // 14, (255, 214, 0))     # 时针偏→2 表示计时中
    p.end()
    return QIcon(pm)


def run_demo(app, cfg):
    """演示模式：无需游戏，定时触发 4 行计时器，便于查看悬浮窗效果。"""
    bank = TimerBank()
    # 演示以“解锁态”展示，方便看到锁图标/可拖动
    cfg["overlay"]["locked"] = False
    ov = overlay.OverlayWindow(cfg, bank)   # 无校正框 => 自由排布(顶部小锁+4行)
    ov.show()
    state = {"n": 0}

    def auto_trigger():
        state["n"] += 1
        got = ov.manual_start()
        print(f"[{_now()}] demo 触发 第{state['n']}次 分配=槽{got}")
        if state["n"] >= 5:   # 触发几次后只靠既有槽位自然走完(第5次应在4槽全忙时被忽略)
            timer.stop()

    timer = QTimer()
    timer.timeout.connect(auto_trigger)
    timer.start(6000)
    print("[demo] 悬浮窗已显示（贴近左侧头像列或屏幕左下），约每 6 秒自动触发一次。")
    print("       关闭终端即可退出。")
    return app.exec()


def run_normal(app, cfg):
    """正常模式：悬浮窗 + 手动热键 + 画面自动识别(需先校准)。"""
    bank = TimerBank()
    locator0 = gamewindow.WindowLocator(cfg["game"]["window_title"])
    boxes0 = cfg["hud"]["boxes"]
    # 4 行计时器由悬浮窗自行锚定到 4 个头像框左侧(行距=头像间距)
    ov = overlay.OverlayWindow(cfg, bank, boxes=boxes0,
                               rect_provider=lambda: locator0.rect())
    ov.show()

    # 正常模式主循环里复用同一个定位器/源
    locator = locator0

    keys = cfg["keys"]
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
        # 槽号(0..3)即幸存者编号：只启动/重启该槽自己的计时器
        ok = ov_ref.start_slot(idx)
        print(f"[{_now()}] 自动识别到一次下钩 → 幸存者槽{idx} 启动{idx + 1}号计时器"
              f"{'' if ok else '(该槽已在计时，已重新归零)'}")

    def _on_manual():
        got = ov.manual_start()
        cur = _key_label(keys["manual_start"])
        label = "成功" if got is not None else "4槽都在计时(忽略)"
        if got is not None:
            label = f"成功 → {got + 1}号计时器"
        print(f"[{_now()}] 手动触发 {cur} → 分配={label}")

    def _on_lock():
        # 直接切换；由 lock_changed 信号统一刷新托盘/自动回锁/日志
        ov.toggle_lock()

    def _on_quit():
        print(f"[{_now()}] 退出")
        app.quit()

    watcher = hotkey.KeyWatcher()

    def _configure_watcher():
        """(重新)注册全部按键——快捷键设置保存后也靠它重建。"""
        watcher.clear()
        try:
            watcher.add(keys["manual_start"], _on_manual)
            watcher.add(keys["toggle_lock"], _on_lock)
            watcher.add(keys["quit"], _on_quit)
        except ValueError as exc:
            print(f"[hotkey] 快捷键无效：{exc}（已忽略该项）")

    def _open_shortcuts():
        """托盘右键→快捷键设置：在一个页面里编辑全部快捷键。"""
        from dbdtimer.config import save as save_cfg
        from dbdtimer.keycapture import ShortcutSettingsDialog
        from PySide6.QtWidgets import QDialog
        rows = [
            ("manual_start", "手动计时（手动兜底开始计时）", keys["manual_start"]),
            ("toggle_lock", "锁定 / 解锁悬浮窗", keys["toggle_lock"]),
            ("quit", "退出程序", keys["quit"]),
        ]
        watcher.stop()   # 改键期间暂停监听，避免“用来改键的那次按键”同时触发
        try:
            dlg = ShortcutSettingsDialog(ov, rows)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_bindings:
                for kid, binding in dlg.result_bindings.items():
                    keys[kid] = binding
                save_cfg(cfg)
                _configure_watcher()
                print(f"[{_now()}] 快捷键已更新："
                      f"手动 {_key_label(keys['manual_start'])} | "
                      f"锁定 {_key_label(keys['toggle_lock'])} | "
                      f"退出 {_key_label(keys['quit'])}")
        finally:
            watcher.start()   # 重新武装后再恢复监听

    _configure_watcher()
    watcher.start()

    # ---------- 托盘图标 + 锁定(鼠标穿透)管理 ----------
    _auto_relock_s = max(0, int(float(cfg["overlay"].get("auto_relock_s", 20))))
    _relock_timer = QTimer()
    _relock_timer.setSingleShot(True)
    _tray = None
    _tray_lock_act = None
    _tray_auto_act = None

    def _arm_relock():
        _relock_timer.stop()
        if not ov.is_locked and _auto_relock_s > 0:
            _relock_timer.start(int(_auto_relock_s * 1000))

    def _relock_timeout():
        if ov.is_dragging:      # 还在拖动中则顺延
            _arm_relock()
            return
        ov.set_locked(True)

    _relock_timer.timeout.connect(_relock_timeout)

    def _set_auto_relock(checked):
        nonlocal _auto_relock_s
        _auto_relock_s = 20 if checked else 0
        cfg["overlay"]["auto_relock_s"] = _auto_relock_s
        try:
            from dbdtimer.config import save as _sc
            _sc(cfg)
        except Exception:
            pass
        _arm_relock()

    def _refresh_tray(locked):
        if _tray_lock_act is not None:
            _tray_lock_act.setChecked(locked)
        if _tray is not None:
            _tray.setToolTip("DBD 下钩计时助手 - "
                             + ("已锁定·鼠标穿透" if locked else "已解锁·可拖动微调"))

    def _on_lock_state(locked):
        print(f"[{_now()}] 悬浮窗 {'锁定(鼠标穿透)' if locked else '解锁(可拖动微调)'}")
        _refresh_tray(locked)
        _arm_relock()

    if QSystemTrayIcon.isSystemTrayAvailable():
        menu = QMenu()
        _tray_lock_act = menu.addAction("锁定悬浮窗（鼠标穿透）")
        _tray_lock_act.setCheckable(True)
        _tray_lock_act.setChecked(ov.is_locked)
        _tray_lock_act.triggered.connect(ov.set_locked)   # triggered(bool) -> set_locked
        menu.addSeparator()
        _tray_auto_act = menu.addAction("解锁后自动回锁（20 秒）")
        _tray_auto_act.setCheckable(True)
        _tray_auto_act.setChecked(_auto_relock_s > 0)
        _tray_auto_act.triggered.connect(_set_auto_relock)
        menu.addSeparator()
        menu.addAction("快捷键设置…").triggered.connect(_open_shortcuts)
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(_on_quit)
        _tray = QSystemTrayIcon(_make_tray_icon())
        _tray.setContextMenu(menu)
        _tray.show()
    else:
        print(f"[{_now()}] 提示: 系统托盘不可用；请用热键锁定/解锁/退出")
    ov.lock_changed.connect(_on_lock_state)

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
                      f"python app.py --calibrate （或按 {_key_label(keys['manual_start'])} 手动计时）")
            return
        # 关键：每次都主动调用 locator.rect() 触发窗口搜索(带1s缓存)，
        # 而不是先看 locator.found（found 只有在搜索后才会变真，会成死循环）
        win_rect = locator.rect()
        if win_rect is None:
            if not _hinted_no_window:
                _hinted_no_window = True
                print(f"[{_now()}] 未找到 DBD 窗口，自动识别等待中……"
                      f"（仅手动 {_key_label(keys['manual_start'])} 可用）")
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
    print(f"        自动识别: 4 个计时器与 4 名逃生者一一对应（槽0~3→计时器1~4）")
    print(f"        手动计时: 按 {_key_label(keys['manual_start'])} 启动一个空闲计时器")
    print(f"        悬浮窗平时为鼠标穿透；锁定/退出等快捷键可在托盘图标右键→快捷键设置 中自由配置")
    print(f"        当前：锁定 {_key_label(keys['toggle_lock'])} · 退出 {_key_label(keys['quit'])}")
    print(f"        解锁后悬浮窗顶部会出现锁图标，点击即重新锁定(穿透)")
    if boxes:
        if getattr(det, "is_icon", False):
            print(f"        已加载 {len(boxes)} 个头像框，自动识别开启（图标识别模式，已加载训练模型）")
        else:
            print(f"        已加载 {len(boxes)} 个头像框，自动识别开启"
                  + (f"（hooked模板{len(det.pool_hooked)}张/downed模板{len(det.pool_downed)}张）"
                     if det.has_templates else "（未拍模板：simple 模式，倒地拉起可能误报）"))
    else:
        print(f"        未校准头像框：自动识别关闭，仅手动 {_key_label(keys['manual_start'])} 可用")
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
