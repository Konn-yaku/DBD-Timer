# -*- coding: utf-8 -*-
"""双槽 60s 正计时内核（纯逻辑、无 GUI 依赖，便于单测）。

规则（与用户约定）：
- 一次“下钩事件”到来时，优先用 1 号槽；1 号槽占用且 2 号槽空闲则用 2 号。
- 两槽都在计时时，第 3 个及以后的事件直接忽略（人类已崩盘，无需计时）。
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
    def __init__(self, duration=60.0, protection=10.0):
        self.duration = float(duration)
        self.protection = float(protection)
        self.slots = [TimerSlot(0), TimerSlot(1)]

    # ---- 事件入口 ----
    def trigger(self, now=None):
        """尝试启动一个槽。返回启动的槽号(0/1)，若两槽都忙返回 None。"""
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
