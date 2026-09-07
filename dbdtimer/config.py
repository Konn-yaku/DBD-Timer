# -*- coding: utf-8 -*-
"""配置加载与持久化。

- 开发(源码)运行时：config.json/templates/debug 位于项目根目录；
- PyInstaller 打包后(sys.frozen)：位于 exe 同目录，随 exe 走、可写。
未校准前一切用默认值，程序仍可运行（仅手动热键可用）。
"""
import copy
import json
import os
import sys


def _app_base_dir():
    """用户数据根目录：源码=项目根；exe=exe 所在目录。

    注意 PyInstaller 单文件运行时会先把程序解压到临时目录(_MEIPASS)，
    不能把可写数据(config.json/templates/debug)放那儿——每次启动都会重建。
    放 exe 同目录既能持久化，也便于用户整包迁移。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(rel):
    """只读内置资源路径（打包时打进 exe 的数据）。

    源码运行 = 项目目录；打包后 = PyInstaller 解压目录(_MEIPASS)/rel，
    用于把单文件 exe 里内置的 icon_model.npz 首次运行时解出到 exe 旁。
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
        return os.path.join(base, rel)
    return os.path.join(_app_base_dir(), rel)


BASE_DIR = _app_base_dir()
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 图标模型目录（打包后位于 exe 同目录，由程序内置副本自动解出）
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DEBUG_DIR = os.path.join(BASE_DIR, "debug")

DEFAULTS = {
    "game": {
        # DBD 窗口标题包含该子串即可命中（无边框窗口模式运行）
        "window_title": "DeadByDaylight",
    },
    "hud": {
        # 4 个幸存者头像框，每项 [x0, y0, x1, y1] 为相对游戏窗口的归一化坐标 (0~1)
        # 由 --calibrate 校准界面写入；为空时自动识别不启用。
        "boxes": [],
    },
    "keys": {
        # 手动计时：4 个键分别精确控制 1~4 号计时器（对应 4 名逃生者）。
        # 自动识别漏检时，看到“几号被下钩”就按对应号码键，只启/重启那位的计时。
        # 统一存为列表（可含 Ctrl/Alt/Shift/Win 修饰），如 ['F1'] 或 ['Ctrl','Alt','M']。
        "manual_1": ["F1"],
        "manual_2": ["F2"],
        "manual_3": ["F3"],
        "manual_4": ["F4"],
        # 悬浮窗锁定/解锁切换（锁定后鼠标穿透；解锁后可拖动）
        "toggle_lock": ["Ctrl", "Alt", "L"],
        # 退出程序
        "quit": ["Ctrl", "Alt", "Q"],
    },
    "overlay": {
        # 悬浮窗记住的位置（屏幕像素，Qt 逻辑坐标）。解锁拖动结束或锁定时写回。
        "x": -1.0,   # -1 = 未设置，首次启动自动放到头像列左侧
        "y": -1.0,
        # 锁定态：True=整窗鼠标穿透(不挡游戏，默认)；False=解锁(可点击/拖动微调)。
        # 锁定/解锁由托盘图标右键或 Ctrl+Alt+L 切换。
        "locked": True,
        # 解锁后若超过该秒数未拖动，自动回到锁定(穿透)。0 = 不自动回锁。
        "auto_relock_s": 20,
        # 数字字号（逻辑像素）——指 1080p(参考宽1920)下的字号；
        # 自动识别模式下悬浮窗会按游戏窗口实际宽度等比放大字号以适配 2K/4K。
        "font_px": 24,
        # 锚定模式下相对“头像列左侧贴齐点”的偏移(可由解锁拖动自动写回)
        "dx": 0.0,
        "dy": 0.0,
        "color_protection": "#FFD600",   # 下钩保护期(0~10s) 黄色
        "color_ds": "#FFFFFF",           # 果断反击期(10~60s) 白色
    },
    "detect": {
        "auto": True,          # 启动画面自动识别（需先校准 boxes 且有训练模型）
        # 图标引擎运行阈值：icon_hook_thr 判定“钩上”所需 masked 相关。
        # 受伤/被救后的人脸与钩形在判别区常只有 ~0.55~0.60 相关，抬高到 0.65
        # 可避免其被误判成“一直钩上”而漏掉下钩；献祭阈值沿用模型训练阈值。
        "icon_hook_thr": 0.65,
        "require_foreground": True,  # 仅当 DBD 窗口在前台才识别(被遮挡时抓到的不是游戏画面，会误报)
        "fps": 15.0,           # 识别采样帧率
        "debug_frames": False, # 周期性把识别画面存到 debug/ 便于排查
    },
}


def _deep_update(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def load():
    """载入配置：默认值 + config.json 覆盖。"""
    data = copy.deepcopy(DEFAULTS)
    user = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            _deep_update(data, user)
        except Exception as exc:  # 配置损坏时静默回退默认
            print(f"[config] 读取 config.json 失败，使用默认值：{exc}")
    # 迁移旧版 manual_start(单个“任意空闲槽”键) -> manual_1(只控 1 号)。
    # 依据“原始用户配置”判断：用户若写了旧 manual_start 而未写新 manual_1，
    # 则把旧键作为 1 号手动键；否则保留默认/用户新值。
    user_keys = user.get("keys", {}) if isinstance(user, dict) else {}
    if ("manual_start" in user_keys and "manual_1" not in user_keys):
        data["keys"]["manual_1"] = data["keys"].get("manual_start") \
            or DEFAULTS["keys"]["manual_1"]
    data["keys"].pop("manual_start", None)
    # 把所有快捷键统一规范化成列表（兼容旧版字符串/缺失的历史配置）
    for _k in ("manual_1", "manual_2", "manual_3", "manual_4",
               "toggle_lock", "quit"):
        _v = data["keys"].get(_k)
        if isinstance(_v, (list, tuple)):
            data["keys"][_k] = [str(x) for x in _v]
        elif isinstance(_v, str) and _v:
            data["keys"][_k] = [_v]
        else:
            data["keys"][_k] = [str(x) for x in DEFAULTS["keys"].get(_k, [])]
    return data


def save(data):
    """原子写回 config.json。"""
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except Exception as exc:
        print(f"[config] 保存失败：{exc}")
        return False


def ensure_dirs():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)


def ensure_runtime_data():
    """让运行所需目录与图标模型就位（每次启动都调用，幂等）。

    1. 确保 templates/ 存在（打包后位于 exe 同目录）。debug/ 刻意**不**预先
       创建：只有显式开启调试(--debug / config detect.debug_frames=true)真正
       写帧时，图标引擎才会惰性创建该目录——正常与发布使用不会产生任何截图目录。
    2. 若 exe 旁还没有 templates/icon_model.npz，则从 exe 内置副本
       (_MEIPASS/templates/icon_model.npz) 复制一份——这样单文件 exe
       无需额外携带模型文件，删除模板目录后也能自动恢复。
    """
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    model_dst = os.path.join(TEMPLATES_DIR, "icon_model.npz")
    if os.path.exists(model_dst):
        return
    try:
        src = resource_path(os.path.join("templates", "icon_model.npz"))
        if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(model_dst):
            import shutil
            shutil.copyfile(src, model_dst)
            print(f"[config] 已从程序内置解出图标模型 -> {model_dst}")
    except Exception as exc:
        print(f"[config] 解出图标模型失败：{exc}")
