# -*- coding: utf-8 -*-
"""配置加载与持久化。

config.json 位于项目根目录，由校准/悬浮窗在运行时写回。
未校准前一切用默认值，程序仍可运行（仅手动热键可用）。
"""
import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# 图标训练/模型目录（icon_model.npz 与训练样本 templates/icons/ 都在其下）
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
        # 手动兜底：自己看到/听到下钩时按一下，立即启动一个空闲计时槽。
        # 统一存为列表（可含 Ctrl/Alt/Shift/Win 修饰），例如 ['XBUTTON2'] 或 ['Ctrl','Alt','M']。
        "manual_start": ["XBUTTON2"],
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
        "beep": True,                    # 到期/阶段切换提示音
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
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            _deep_update(data, user)
        except Exception as exc:  # 配置损坏时静默回退默认
            print(f"[config] 读取 config.json 失败，使用默认值：{exc}")
    # 把三个快捷键统一规范化成列表（兼容旧版字符串/缺失 quit 的历史配置）
    for _k in ("manual_start", "toggle_lock", "quit"):
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
