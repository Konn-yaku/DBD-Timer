# -*- coding: utf-8 -*-
"""图标状态机：以“固定状态图标”为输入的下钩检测（不再依赖每局随机的角色脸）。

设计（对应游戏规则）：
- 只有处于“钩上”的幸存者，后续只会走向两种结局：被救(下钩) 或 三挂献祭。
- 因此检测逻辑 = 识别“钩上”图标出现 → 之后图标离开“钩上”：
    若离开后出现“献祭”标识 → 不触发（人已死，没有下钩计时）；
    否则（恢复正常/其他）→ 触发一次下钩计时。
- 输入类别由图标分类器给出：'hooked' / 'sacrificed' / 'other'。

每槽状态机：
    IDLE ——(出现 hooked)--> HOOKED
    HOOKED ——(离开 hooked)--> PENDING（等待窗口，确认不是献祭）
    PENDING ——(出现 sacrificed)--> DEAD          （献祭：不计时）
    PENDING ——(窗口内持续非hooked/非sacrificed)--> 触发下钩 -> IDLE
    DEAD  ——(长时间无活动,视为新局)----------> IDLE
- 连续 confirm 帧同类别才生效（滤分类器瞬闪）。
"""
import time

IDLE = "idle"
HOOKED = "hooked"
PENDING = "pending"
DEAD = "dead"


class _Slot:
    __slots__ = ("last_cat", "hold", "state", "pend_at", "dead_at")

    def __init__(self):
        self.last_cat = None
        self.hold = 0
        self.state = IDLE
        self.pend_at = 0.0
        self.dead_at = 0.0


class IconHookDetector:
    """输入：每槽当前类别；输出：下钩事件 [(idx, now)]。"""

    def __init__(self, on_unhook, n=4, confirm=4,
                 post_window_s=1.2, dead_idle_s=60.0):
        self.on_unhook = on_unhook
        self.confirm = max(1, int(confirm))
        self.post_window_s = float(post_window_s)   # 离开钩上后等多久确认是否献祭
        self.dead_idle_s = float(dead_idle_s)       # 献祭屏蔽多久后释放(视为新局)
        self.slots = [_Slot() for _ in range(n)]

    def reset(self):
        """清空全部槽位（新对局/切回前台时调用）。"""
        self.slots = [_Slot() for _ in self.slots]

    # ---- 每帧入口 ----
    def process(self, cats, now=None):
        """cats: 长度=槽数，每项 'hooked'/'sacrificed'/'other'。返回 [(idx, now)]。"""
        if now is None:
            now = time.monotonic()
        events = []
        for i, cat in enumerate(self.slots):
            if i >= len(cats):
                break
            c = cats[i]
            st = self.slots[i]

            # 连续帧确认（滤分类器瞬闪）
            if c == st.last_cat:
                st.hold += 1
            else:
                st.last_cat = c
                st.hold = 1
            if st.hold < self.confirm:
                continue

            if st.state == DEAD:
                # 死人绝不会再上钩：若仍持续出现 hooked，说明此前的 DEAD 是误判
                # (如受伤/倒地的红调头像被误认成献祭)，解除屏蔽重新正常判定，
                # 避免“误判死亡 → 整段时间内真实下钩全部漏报”。
                if c == "hooked":
                    st.state = HOOKED
                    st.pend_at = 0.0
                    continue
                # 长时间无活动视为新局/界面切换，释放
                if now - st.dead_at > self.dead_idle_s:
                    st.state = IDLE
                continue

            if c == "hooked":
                st.pend_at = 0.0
                if st.state != HOOKED:
                    st.state = HOOKED
            elif c == "sacrificed":
                # 献祭/死亡标识：若曾经过钩上或在等待窗口，判定为三挂献祭 → 不计时
                if st.state in (HOOKED, PENDING) or st.state == IDLE:
                    st.state = DEAD
                    st.dead_at = now
            else:  # 'other'（正常/被救/其他非钩状态）
                if st.state == HOOKED:
                    # 图标离开“钩上”：进入等待窗口，确认不是献祭
                    st.state = PENDING
                    st.pend_at = now
                elif st.state == PENDING:
                    if now - st.pend_at >= self.post_window_s:
                        # 窗口内未出现献祭标识 => 这就是一次下钩
                        st.state = IDLE
                        events.append((i, now))
                        try:
                            self.on_unhook(i, now)
                        except Exception:
                            pass
        return events
