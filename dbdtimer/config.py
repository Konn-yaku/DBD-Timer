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
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DEBUG_DIR = os.path.join(BASE_DIR, "debug")

# 模板子目录名（校准时可拍 正常/上钩/倒地 三态参考图）
TPL_ALIVE = "alive"          # 每槽的正常头像原图(按槽位命名 slot{0..3}.png)
# 方案①：差异模板 —— “该框状态图 - 该槽正常图”，可去掉角色脸/背景，只留图标
TPL_HOOKED_DIFF = "hooked_diff"
TPL_DOWNED_DIFF = "downed_diff"

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
        # 手动兜底：自己看到/听到下钩时按一下，立即启动一个计时槽。
        # 实测：该用户鼠标的"下侧键"在 Windows 里是 XBUTTON2。
        # 若换了鼠标/按键不符，改成 XBUTTON1 即可。
        "manual_start": "XBUTTON2",
        # 悬浮窗锁定/解锁切换（锁定后鼠标穿透；解锁后可拖动）
        "toggle_lock": ["Ctrl", "Alt", "L"],
    },
    "overlay": {
        # 悬浮窗记住的位置（屏幕像素，Qt 逻辑坐标）。解锁拖动结束或锁定时写回。
        "x": -1.0,   # -1 = 未设置，首次启动自动放主屏右下角
        "y": -1.0,
        "locked": False,   # 是否以锁定态启动
        # 锁定语义：False=仅禁止拖动(锁按钮可解锁)；True=锁定同时鼠标穿透(只能热键解锁)
        "passthrough_on_lock": False,
        "font_px": 52,     # 数字字号（像素）
        "color_protection": "#FFD600",   # 下钩保护期(0~10s) 黄色
        "color_ds": "#FFFFFF",           # 果断反击期(10~60s) 白色
        "beep": True,                    # 到期/阶段切换提示音
    },
    "detect": {
        "auto": True,          # 启动画面自动识别（需先校准 boxes）
        "engine": "auto",     # 识别引擎: auto(有图标模型用icon)/icon/face
        "require_foreground": True,  # 仅当 DBD 窗口在前台才识别(被遮挡时抓到的不是游戏画面，会误报)
        "fps": 15.0,           # 识别采样帧率
        "confirm_frames": 3,   # 连续多少帧一致才确认状态切换（抑制闪烁）
        "alive_threshold": 0.85,  # 与“存活参考”整图相关性高于此 => 视为正常(降低以便捕捉小图标变化)
        "min_event_s": 2.5,   # 进入“上钩/倒地/偏离”态所需最短持续秒数：滤掉秒级动画/高亮抖动造成的误触发
        "state_threshold": 0.50,  # 差异图与 hooked/downed 模板相关性需高于此才认定匹配
        "simple_mode": True,   # 无差异模板时：任何 偏离→恢复 都当作“下钩”（倒地拉起会误报）
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


def tpl_dir(category):
    d = os.path.join(TEMPLATES_DIR, category)
    os.makedirs(d, exist_ok=True)
    return d


def ensure_dirs():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    os.makedirs(DEBUG_DIR, exist_ok=True)
