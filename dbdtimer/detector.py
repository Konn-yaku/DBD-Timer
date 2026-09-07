# -*- coding: utf-8 -*-
"""画面识别：每槽位状态机，检测“被救下钩”事件（方案①：差异图分类）。

原理（针对“角色脸/健康受伤各不相同，上钩/倒地图标相同”的实际情况）：
- 每个槽位维护一张自适应“正常存活”参考（只在该槽确为 ALIVE 时缓慢更新）。
- 每帧把裁剪图与该参考做整图相关：高 => ALIVE；低 => 偏离(出现某种状态图标)。
- 分类“上钩 vs 倒地”时，不再直接比对整张脸，而是比对【差异图】：
    diff = |当前裁剪 - 该槽正常参考|
  差异图会把“静止的脸 + 静止的背景”减掉，只留下变化的图标区，
  因此可跨角色、跨地图背景复用同一种图标模板（记为上钩/记为倒地 拍的差异）。
- 触发仍用“恢复边沿”：committed 从 HOOKED 回到 ALIVE = 被救下 => 下钩事件。
- 连续 confirm_frames 帧一致才切换状态，抑制闪烁。
"""
import glob
import os
import time

import cv2
import numpy as np

from .config import DEBUG_DIR, TEMPLATES_DIR, TPL_DOWNED_DIFF, TPL_HOOKED_DIFF

ALIVE = "alive"
CHANGED = "changed"
HOOKED = "hooked"
DOWNED = "downed"

_TPL_SIZE = 32
# 槽位长期处于“偏离”超过该秒数且无模板可归类时，强制重设基线（处理启动时已上钩/已倒地）
_REBASELINE_S = 35.0


def _load_pool(category):
    files = glob.glob(os.path.join(TEMPLATES_DIR, category, "*.png"))
    out = []
    for fp in files:
        # opencv 在 Windows 读不了中文名，跳过非 ASCII 文件(避免噪音/误读)
        if not os.path.basename(fp).isascii():
            continue
        img = cv2.imread(fp, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        img = cv2.resize(img, (_TPL_SIZE, _TPL_SIZE), interpolation=cv2.INTER_AREA)
        out.append(img.astype(np.float32) / 255.0)
    return out


def _best_corr(pool, img):
    best = -1.0
    for t in pool:
        v = _corr(img, t)
        if v > best:
            best = v
    return best


def _corr(a, b):
    a = a.ravel()
    b = b.ravel()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _diff_map(crop, alive):
    """差异图：|当前裁剪 - 正常参考|，去掉静止的脸/背景，只留变化(图标)区。"""
    return np.abs(crop - alive)


def _crop_gray(frame, box, W, H):
    x0 = max(0, int(box[0] * W))
    y0 = max(0, int(box[1] * H))
    x1 = min(W, int(box[2] * W))
    y1 = min(H, int(box[3] * H))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    c = frame[y0:y1, x0:x1]
    if c.size == 0:
        return None
    c = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    c = cv2.resize(c, (_TPL_SIZE, _TPL_SIZE), interpolation=cv2.INTER_AREA)
    return c.astype(np.float32) / 255.0


class _Slot:
    __slots__ = ("idx", "alive", "raw", "hold", "committed", "last_alive")

    def __init__(self, idx):
        self.idx = idx
        self.alive = None       # 自适应存活参考 (32x32 float)
        self.raw = None         # 最近一次单帧分类
        self.hold = 0           # 连续帧计数
        self.committed = None   # 确认后的稳定状态
        self.last_alive = 0.0


class Detector:
    def __init__(self, cfg, on_unhook):
        self.cfg = cfg
        self.on_unhook = on_unhook   # callable(idx:int, now:float)
        d = cfg["detect"]
        self.confirm = max(1, int(d.get("confirm_frames", 3)))
        self.athr = float(d.get("alive_threshold", 0.90))
        self.sthr = float(d.get("state_threshold", 0.60))
        self.simple = bool(d.get("simple_mode", True))
        self.debug_on = bool(d.get("debug_frames", False))
        self.pool_hooked = _load_pool(TPL_HOOKED_DIFF)   # 差异图模板
        self.pool_downed = _load_pool(TPL_DOWNED_DIFF)   # 差异图模板
        self.slots = [_Slot(i) for i in range(4)]
        self._next_debug = 0.0
        self._dbg_seq = 0

    @property
    def has_templates(self):
        return bool(self.pool_hooked or self.pool_downed)

    # ---- 每帧入口 ----
    def process(self, frame, boxes, now=None):
        """frame: BGR 游戏窗口画面；boxes: 归一化框列表。返回事件 [(idx, now)]。"""
        if now is None:
            now = time.monotonic()
        W, H = frame.shape[1], frame.shape[0]
        events = []
        for sl, box in zip(self.slots, boxes):
            crop = _crop_gray(frame, box, W, H)
            if crop is None:
                continue
            raw = self._classify(sl, crop, now)
            if self._advance(sl, raw, now):
                events.append((sl.idx, now))
                try:
                    self.on_unhook(sl.idx, now)
                except Exception:
                    pass
        if self.debug_on and now >= self._next_debug:
            self._next_debug = now + 4.0
            self._save_debug(frame, boxes)
        return events

    def _classify(self, sl, crop, now):
        if sl.alive is None:
            sl.alive = crop.copy()
            sl.raw = ALIVE
            sl.committed = ALIVE
            sl.last_alive = now
            return ALIVE
        sim = _corr(crop, sl.alive)
        if sim >= self.athr:
            sl.alive = 0.92 * sl.alive + 0.08 * crop   # 缓慢更新存活参考
            return ALIVE

        # 偏离存活 => 用“差异图”分类：减去静止的脸/背景，只比对变化的图标区
        if self.pool_hooked or self.pool_downed:
            d = _diff_map(crop, sl.alive)
            sh = _best_corr(self.pool_hooked, d) if self.pool_hooked else -1.0
            sd = _best_corr(self.pool_downed, d) if self.pool_downed else -1.0
            if sh >= self.sthr and sh >= sd:
                return HOOKED
            if sd >= self.sthr and sd > sh:
                return DOWNED
            return CHANGED

        # 无模板：长期偏离则重设基线（启动时对方已上钩/倒地），否则视为“变化中”
        if now - sl.last_alive > _REBASELINE_S:
            sl.alive = crop.copy()
            sl.last_alive = now
            sl.raw = ALIVE
            sl.committed = ALIVE
            return ALIVE
        return CHANGED

    def _advance(self, sl, raw, now):
        if sl.raw == raw:
            sl.hold += 1
        else:
            sl.raw = raw
            sl.hold = 1
        if sl.hold < self.confirm:
            return False

        prev = sl.committed
        if raw == prev:
            if raw == ALIVE:
                sl.last_alive = now
            return False

        fired = False
        if self.has_templates:
            # 有差异模板：只有确认“上钩”后恢复才触发（倒地恢复不会误触发）
            if prev == HOOKED and raw == ALIVE:
                fired = True
        else:
            # 无模板：simple_mode 兜底（任何 偏离→恢复 都当作下钩）
            if self.simple and prev in (HOOKED, CHANGED) and raw == ALIVE:
                fired = True
        sl.committed = raw
        if raw == ALIVE:
            sl.last_alive = now
        return fired

    # ---- 调试截图 ----
    def _save_debug(self, frame, boxes):
        try:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            vis = frame.copy()
            W, H = vis.shape[1], vis.shape[0]
            colors = {
                ALIVE: (0, 255, 0), CHANGED: (0, 200, 255),
                HOOKED: (0, 0, 255), DOWNED: (0, 140, 255),
            }
            for sl, box in zip(self.slots, boxes):
                x0, y0 = int(box[0] * W), int(box[1] * H)
                x1, y1 = int(box[2] * W), int(box[3] * H)
                col = colors.get(sl.committed, (255, 255, 255))
                cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
                cv2.putText(vis, str(sl.committed), (x0, max(0, y0 - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1, cv2.LINE_AA)
            self._dbg_seq += 1
            name = os.path.join(DEBUG_DIR, f"dbg_{int(time.time())}_{self._dbg_seq:03d}.png")
            cv2.imwrite(name, vis)
            # 只保留最近 60 张
            files = sorted(glob.glob(os.path.join(DEBUG_DIR, "*.png")))
            for fp in files[:-60]:
                try:
                    os.remove(fp)
                except OSError:
                    pass
        except Exception:
            pass
