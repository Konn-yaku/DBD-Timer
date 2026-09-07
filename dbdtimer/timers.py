# -*- coding: utf-8 -*-
"""4 个独立 60s 正计时内核（纯逻辑、无 GUI 依赖，便于单测）。

规则（与用户约定，自 4 计时器版本起）：
- 4 个槽位一一对应 4 名逃生者（槽 0..3）。
  某槽(幸存者)被下钩时，只启动/重启“该槽自己”的计时器，互不抢占——
  不再使用旧的“1 号占用就用 2 号”公共池规则。
- 手动兜底 manual_trigger()：按下时并不知道对应哪位逃生者，
  取当前第一个空闲槽启动；若 4 槽全忙则忽略。
- 每个槽从 0 正数到 60s：0~10s 为下钩保护期(黄色)，10~60s 为果断反击期(白色)。
"""
import time


class TimerSlot:
    __slots__ = ("idx", "active", "started")

    def __init__(self, idx):
        self.idx = idx
        self.active = False
        self.started = 0.0


class TimerBank:
    """4 个独立槽：每槽对应一名幸存者（槽号 = 幸存者 HUD 槽号）。"""

    def __init__(self, duration=60.0, protection=10.0, n=4):
        self.duration = float(duration)
        self.protection = float(protection)
        self.slots = [TimerSlot(i) for i in range(max(1, int(n)))]

    # ---- 事件入口 ----
    def start_slot(self, idx, now=None):
        """(自动识别用) 启动/重启指定槽 idx 的计时器——幸存者槽与计时器一一对应。

        若该槽已在计时（同一人 60s 内再次被下钩，如被救后又被挂），
        则视为一次新的下钩，重新从 0 开始。
        返回启动的槽号，idx 非法时返回 None。
        """
        if now is None:
            now = time.monotonic()
        if not (0 <= idx < len(self.slots)):
            return None
        s = self.slots[idx]
        s.active = True
        s.started = now
        return s.idx

    def manual_trigger(self, now=None):
        """(手动兜底用) 启动第一个空闲槽。4 槽全忙返回 None。"""
        if now is None:
            now = time.monotonic()
        for s in self.slots:
            if not s.active:
                s.active = True
                s.started = now
                return s.idx
        return None

    def active_slots(self):
        return [s for s in self.slots if s.active]

    # ---- 每帧采样 ----
    def sample(self, now=None):
        """返回每个活动槽的采样信息；已满 60s 的槽在本帧被释放。

        返回值: list[dict] -> {idx, elapsed, protection, finished}
          protection=True 表示仍处 0~10s 保护期。
        """
        if now is None:
            now = time.monotonic()
        out = []
        for s in self.slots:
            if not s.active:
                continue
            e = now - s.started
            finished = e >= self.duration
            if finished:
                s.active = False
            out.append({
                "idx": s.idx,
                "elapsed": min(e, self.duration),
                "protection": e < self.protection,
                "finished": finished,
            })
        return out
