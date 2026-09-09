# -*- coding: utf-8 -*-
"""DBD 下钩计时助手 - 入口。

用法：
  python app.py              正常使用（进入 DBD 后：自动识别 + 手动1~4键 精确兜底）
  python app.py --demo       无游戏演示悬浮窗效果（每几秒自动触发一次）
  python app.py --calibrate  校准：框选 4 个幸存者头像框
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

from dbdtimer import capture, gamewindow, hotkey, iconengine, overlay
from dbdtimer.config import ensure_runtime_data, load as load_cfg
from dbdtimer.timers import TimerBank

_KEY_LABEL = {
    "XBUTTON1": "鼠标侧键1(XBUTTON1)",
    "XBUTTON2": "鼠标侧键2(XBUTTON2)",
}


def _redirect_stdio_if_windowed():
    """单文件无控制台版(exe)：把 stdout/stderr 指到 exe 旁的“运行日志.log”。

    PyInstaller --noconsole 运行时 sys.stdout/stderr 为 None，直接 print 会崩，
    且用户看不到任何信息；统一重定向到日志文件便于排障。
    源码运行/控制台版保持不变。
    """
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return   # 控制台版 exe：保持原样
    try:
        from dbdtimer.config import BASE_DIR
        _f = open(os.path.join(BASE_DIR, "运行日志.log"), "w", encoding="utf-8")
        sys.stdout = _f
        sys.stderr = _f
    except Exception:
        import io
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()


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
    """演示模式：无需游戏，定时逐个启动 1~4 号计时器，便于查看悬浮窗效果。"""
    bank = TimerBank()
    # 演示以“解锁态”展示，方便看到锁图标/可拖动
    cfg["overlay"]["locked"] = False
    ov = overlay.OverlayWindow(cfg, bank)   # 无校正框 => 自由排布(顶部小锁+4行)
    ov.show()
    state = {"n": 0}

    def auto_trigger():
        idx = state["n"]
        if idx < 4:
            got = ov.start_slot(idx)   # 逐个启动 1~4 号
            print(f"[{_now()}] demo 触发 第{idx + 1}号计时器 槽{got}")
        state["n"] += 1
        if state["n"] >= 4:   # 4 槽都启动后让其自然走完
            timer.stop()

    timer = QTimer()
    timer.timeout.connect(auto_trigger)
    timer.start(6000)
    print("[demo] 悬浮窗已显示（贴近左侧头像列或屏幕左下），约每 6 秒逐个启动 1~4 号计时器。")
    print("       关闭终端即可退出。")
    return app.exec()


def run_normal(app, cfg):
    """正常模式：悬浮窗 + 手动1~4键精确计时 + 画面自动识别(进入 DBD 后)。"""
    bank = TimerBank()
    locator0 = gamewindow.WindowLocator(cfg["game"]["window_title"])
    boxes0 = cfg["hud"]["boxes"]
    # 4 行计时器由悬浮窗自行锚定到 4 个头像框左侧(行距=头像间距)
    ov = overlay.OverlayWindow(cfg, bank, boxes=boxes0,
                               rect_provider=lambda: locator0.rect())
    # 注意：不再无条件 show()——悬浮窗按“是否找到 DBD 窗口”自动显隐(_sync_visibility)：
    # 无 DBD 窗口时完全隐藏(托盘常驻反馈)，进入游戏后自动显示并贴齐头像列。

    # 正常模式主循环里复用同一个定位器/源
    locator = locator0

    keys = cfg["keys"]
    source = capture.FrameSource(locator)

    def _manual_keys_hint():
        return "  ".join(f"{i + 1}号={_key_label(keys[f'manual_{i + 1}'])}" for i in range(4))

    # 识别引擎：图标识别（唯一方案；旧整框人脸比对已移除）。
    # 无训练模型时 det.ready=False，自动识别不触发（手动1~4键仍可用，需先进 DBD）。
    det = iconengine.IconEngine(cfg, on_unhook=lambda i, ts: _on_unhook(i, ts, ov))

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

    def _make_manual(idx):
        """手动快捷键 idx(0..3)：精确只启/重启第 idx+1 号计时器(该幸存者)。

        与自动识别一致：仅当检测到 DBD 窗口(进入游戏)后才允许手动计时；
        未进游戏时按键无效并提示，避免在无意义的桌面场景误启动计时。
        """
        def _on_manual():
            if locator.rect() is None:   # 主动触发一次窗口搜索
                print(f"[{_now()}] 手动计时需先进入 DBD（未检测到游戏窗口，已忽略）")
                return
            ov.start_slot(idx)   # 有效槽总是成功(从0重新开始)
            cur = _key_label(keys[f"manual_{idx + 1}"])
            print(f"[{_now()}] 手动触发 {idx + 1}号 → 计时器{idx + 1} 从 0 开始（{cur}）")
        return _on_manual

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
            for i in range(4):
                watcher.add(keys.get(f"manual_{i + 1}", []), _make_manual(i))
            watcher.add(keys["toggle_lock"], _on_lock)
            watcher.add(keys["quit"], _on_quit)
        except ValueError as exc:
            print(f"[hotkey] 快捷键无效：{exc}（已忽略该项）")

    def _open_shortcuts():
        """托盘右键→快捷键设置：在一个页面里编辑全部快捷键。

        注意：托盘菜单是系统原生菜单，若在其 triggered 里同步 exec()，
        原生菜单仍抓着输入焦点导致新对话框点不到——故调用方用
        QTimer.singleShot(0) 延迟到菜单关闭后再打开。
        """
        from dbdtimer.config import save as save_cfg
        from dbdtimer.keycapture import ShortcutSettingsDialog
        from PySide6.QtWidgets import QDialog
        rows = [
            ("manual_1", "手动计时 1号逃生者", keys["manual_1"]),
            ("manual_2", "手动计时 2号逃生者", keys["manual_2"]),
            ("manual_3", "手动计时 3号逃生者", keys["manual_3"]),
            ("manual_4", "手动计时 4号逃生者", keys["manual_4"]),
            ("toggle_lock", "锁定 / 解锁悬浮窗", keys["toggle_lock"]),
            ("quit", "退出程序", keys["quit"]),
        ]
        watcher.stop()   # 改键期间暂停监听，避免“用来改键的那次按键”同时触发
        try:
            # parent=None：避免挂在“鼠标穿透/工具窗”悬浮窗下导致无法正常激活；
            # 应用级模态由 exec() 直接弹出(原生菜单已关闭，可正常获得焦点)。
            dlg = ShortcutSettingsDialog(None, rows)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_bindings:
                for kid, binding in dlg.result_bindings.items():
                    keys[kid] = binding
                save_cfg(cfg)
                _configure_watcher()
                print(f"[{_now()}] 快捷键已更新："
                      f"手动1 {_key_label(keys['manual_1'])} | "
                      f"手动2 {_key_label(keys['manual_2'])} | "
                      f"手动3 {_key_label(keys['manual_3'])} | "
                      f"手动4 {_key_label(keys['manual_4'])} | "
                      f"锁定 {_key_label(keys['toggle_lock'])} | "
                      f"退出 {_key_label(keys['quit'])}")
        finally:
            watcher.start()   # 重新武装后再恢复监听

    def _open_calibration():
        """托盘右键→校准头像框（发布版无控制台，也靠它校准）。

        与改键同理：原生托盘菜单仍抓着焦点，须由 singleShot(0) 延迟到菜单
        关闭后再打开。校准期间暂停自动识别与热键；保存后悬浮窗/识别立即生效。
        未保存(放弃)则只恢复暂停前状态，不影响原框。
        """
        nonlocal boxes, _hinted_no_boxes, _was_fg
        from dbdtimer.calibration import CalibrationDialog
        from PySide6.QtWidgets import QDialog
        was_running = auto_timer.isActive()
        watcher.stop()
        auto_timer.stop()
        loc2 = gamewindow.WindowLocator(cfg["game"]["window_title"])
        src2 = capture.FrameSource(loc2)
        accepted = False
        try:
            dlg = CalibrationDialog(cfg, loc2, src2)
            dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
        finally:
            src2.close()
            watcher.start()                 # 无论保存与否都恢复监听/识别
            if was_running:
                auto_timer.start()
        if not accepted:
            return                          # 放弃：不动原有框与状态
        boxes = cfg["hud"]["boxes"]         # 仅“保存并退出”(accept)才写回 cfg
        _hinted_no_boxes = not boxes        # 允许自动识别重新提示/恢复
        ov.set_boxes(boxes)                 # 悬浮窗行数/锚定随之更新
        det.reset()                         # 新框对应新画面位置，重建状态基线
        # 校准对话框是模态的，关闭后 Windows 前台未必回到 DBD——主动带回前台，
        # 否则前台守卫会一直以为 DBD 在后台而暂停识别（需用户手动切屏才恢复）。
        _was_fg = None                      # 重置，让识别循环重新评估前台
        if locator.focus():
            print(f"[{_now()}] 已切回 DBD 窗口前台，自动识别即刻恢复")
        else:
            print(f"[{_now()}] 提示：请点击一下游戏窗口以恢复自动识别")
        print(f"[{_now()}] 校准结束：头像框 {len(boxes)} 个，已生效"
              f"（模型{'已加载' if det.ready else '缺失·仅手动'}，"
              f"自动识别{'开启' if det.ready else '不可用'}）")

    def _prompt_calibrate():
        """首启未校准：弹窗引导用户去校准（发布版无控制台也需要）。"""
        from PySide6.QtWidgets import QMessageBox
        mb = QMessageBox(None)
        mb.setWindowTitle("DBD 下钩计时助手")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText("尚未校准头像框，自动识别暂不可用。")
        mb.setInformativeText(
            "请先启动并进入 DBD（悬浮窗/手动计时也需检测到游戏窗口才会启用）。\n"
            "进入 DBD 后，用「校准头像框…」在左侧 4 个逃生者状态图标上各框一个框，"
            "即可开启自动识别。"
        )
        cal_btn = mb.addButton("去校准…", QMessageBox.ButtonRole.AcceptRole)
        mb.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        mb.setWindowModality(Qt.WindowModality.ApplicationModal)
        mb.exec()
        if mb.clickedButton() is cal_btn:
            QTimer.singleShot(0, _open_calibration)

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
        menu.addAction("校准头像框…（首次使用/换分辨率）").triggered.connect(
            lambda: QTimer.singleShot(0, _open_calibration))
        menu.addAction("快捷键设置…").triggered.connect(
            lambda: QTimer.singleShot(0, _open_shortcuts))
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(_on_quit)
        _tray = QSystemTrayIcon(_make_tray_icon())
        _tray.setContextMenu(menu)
        _tray.show()
    else:
        print(f"[{_now()}] 提示: 系统托盘不可用；请用热键锁定/解锁/退出")
    ov.lock_changed.connect(_on_lock_state)

    # 仅“首次启动且启动时没有 DBD 窗口”时，弹一次托盘气泡说明悬浮窗为何不显示。
    # 悬浮窗此时按 _sync_visibility 隐藏(不显示矮窗口)；托盘常驻作为“程序在跑”的反馈。
    def _notify_startup_no_dbd():
        if _tray is None or locator.rect() is not None:
            return
        try:
            _tray.showMessage(
                "DBD 下钩计时助手",
                "已启动并驻留托盘：未检测到 DBD 窗口，悬浮窗暂不显示。\n"
                "进入游戏后会自动出现并贴齐头像列。",
                QSystemTrayIcon.MessageIcon.Information, 5000)
        except Exception:
            pass

    if _tray is not None:
        QTimer.singleShot(1200, _notify_startup_no_dbd)

    # 自动识别主循环
    interval = max(40, int(1000.0 / max(1.0, float(cfg["detect"].get("fps", 15.0)))))
    auto_timer = QTimer()
    auto_timer.setInterval(interval)

    def _auto_tick():
        nonlocal _hinted_no_boxes, _hinted_no_window, _reported_found, _was_fg
        if not auto_on:
            return
        # 关键：每次都主动调用 locator.rect() 触发窗口搜索(带1s缓存)，
        # 而不是先看 locator.found（found 只有在搜索后才会变真，会成死循环）
        win_rect = locator.rect()
        if win_rect is None:
            if not _hinted_no_window:
                _hinted_no_window = True
                print(f"[{_now()}] 未找到 DBD 窗口，自动识别与手动计时均待命。"
                      f"请先启动并进入 DBD（悬浮窗也会随之出现）")
            return
        if not boxes:
            if not _hinted_no_boxes:
                _hinted_no_boxes = True
                print(f"[{_now()}] 已找到 DBD，但尚未校准头像框：自动识别关闭，"
                      f"可用 手动1~4 键 精确计时（{_manual_keys_hint()}）")
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
    print(f"        手动计时: 精确控制 1~4 号（{_manual_keys_hint()}）——与自动识别相同，"
          f"需先进入 DBD 后才可用")
    print(f"        悬浮窗平时为鼠标穿透；锁定/退出等快捷键可在托盘图标右键→快捷键设置 中自由配置")
    print(f"        当前：锁定 {_key_label(keys['toggle_lock'])} · 退出 {_key_label(keys['quit'])} "
          f"· 手动1 {_key_label(keys['manual_1'])}")
    print(f"        解锁后悬浮窗顶部会出现锁图标，点击即重新锁定(穿透)")
    # 图标模型状态（与是否校准无关，始终打印以便排查）
    _model_txt = ("已加载训练模型" if det.ready
                  else "未找到图标模型 templates/icon_model.npz（自动识别不可用，仅手动）")
    print(f"        图标模型: {_model_txt}")
    if boxes:
        if det.ready:
            print(f"        已加载 {len(boxes)} 个头像框，自动识别开启")
        else:
            print(f"        已加载 {len(boxes)} 个头像框，但模型缺失，自动识别暂不可用"
                  f"（请先运行 train_icon_clf.py 训练，或进入 DBD 后仅用手动1~4键）")
    else:
        print(f"        未校准头像框：进入 DBD 后自动识别关闭，可用 手动1~4 键 精确计时"
              f"（{_manual_keys_hint()}）")

    # 首启未校准自动引导（发布版无控制台也适用）：弹出提示并提供“去校准”。
    if not boxes:
        QTimer.singleShot(800, _prompt_calibrate)

    return app.exec()


def run_calibrate(app, cfg):
    from dbdtimer.calibration import CalibrationDialog
    locator = gamewindow.WindowLocator(cfg["game"]["window_title"])
    source = capture.FrameSource(locator)
    dlg = CalibrationDialog(cfg, locator, source)
    dlg.exec()
    print(f"[{_now()}] 校准结束，已保存框数量: {len(cfg['hud']['boxes'])}")


def main():
    # 单文件无控制台 exe：先把输出重定向到 exe 旁的 运行日志.log
    _redirect_stdio_if_windowed()
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
    # 打包后首启：确保 exe 旁 templates/debug 就位并从内置解出图标模型
    ensure_runtime_data()
    if args.debug:
        cfg["detect"]["debug_frames"] = True

    # 静音 Qt 的 DPI 提示（无害：系统已由其他组件设为按显示器感知，Qt 无法重复设置）
    _rules = os.environ.get("QT_LOGGING_RULES", "").strip()
    _extra = "qt.qpa.window=false"
    os.environ["QT_LOGGING_RULES"] = (_rules + ";" + _extra) if _rules else _extra

    app = QApplication(sys.argv)
    app.setApplicationName("DBD Timer")
    # 托盘常驻应用：悬浮窗是 Qt.Tool(不计为主窗口)，若开启“最后窗口关闭即退出”，
    # 关闭 parent=None 的设置对话框会被当成最后一个主窗口而把整个程序带退。
    # 改为显式退出：托盘“退出”/退出热键/其它 app.quit() 仍正常生效。
    app.setQuitOnLastWindowClosed(False)

    # 供自检/CI 使用：设置 DBDTIMER_AUTOQUIT_MS=<毫秒> 可让程序自动退出
    try:
        _aq = int(os.environ.get("DBDTIMER_AUTOQUIT_MS", "0") or 0)
        if _aq > 0:
            QTimer.singleShot(_aq, app.quit)
    except ValueError:
        pass

    if args.calibrate:
        return run_calibrate(app, cfg)
    if args.demo:
        return run_demo(app, cfg)
    return run_normal(app, cfg)


if __name__ == "__main__":
    sys.exit(main())
