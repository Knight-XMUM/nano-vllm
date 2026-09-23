"""C 臂 —— ARC（Adaptive Replacement Cache）：频率轴代表（v1.2 选定，不是 2Q）。

结构：T1（见过一次）/ T2（见过 ≥2 次）两个常驻 LRU 队列 +
      B1 / B2 两个幽灵队列（只记 hash 不占架位，记"刚被赶走的人"）。
自适应参数 p = T1 的目标容量：refault 打中 B1 就上调（该多留新欢），
打中 B2 就下调（该多留常客）。

四钩子分工：on_commit 进 T1；on_access T1→T2 升级；on_evict 按 p 选牺牲者
并录幽灵；on_allocate 时若撞上幽灵 → 调 p 且复活进 T2。
"""

from collections import OrderedDict

from kvos.hooks import Policy


class ARC(Policy):
    name = "C"

    def __init__(self):
        self.T1 = OrderedDict()  # hash -> Block，只见过一次
        self.T2 = OrderedDict()  # hash -> Block，见过至少两次
        self.B1 = OrderedDict()  # hash -> None，T1 的幽灵
        self.B2 = OrderedDict()  # hash -> None，T2 的幽灵
        self.p = 0.0             # T1 的目标容量（自适应）
        self._resurrect = set()  # 本轮 refault 复活、commit 时应进 T2 的 hash

    # ---- 簿记 ----

    def _ghost_push(self, ghost, h):
        ghost[h] = None
        ghost.move_to_end(h)
        while len(ghost) > self.table.capacity:
            ghost.popitem(last=False)

    def _untrack(self, h):
        for q in (self.T1, self.T2, self.B1, self.B2):
            q.pop(h, None)

    # ---- 四钩子 ----

    def on_allocate(self, blk, tick, was_evicted):
        h = blk.hash
        if h in self.B1:
            delta = max(1.0, len(self.B2) / max(1, len(self.B1)))
            self.p = min(self.p + delta, self.table.capacity)
            self.B1.pop(h)
            self._resurrect.add(h)
        elif h in self.B2:
            delta = max(1.0, len(self.B1) / max(1, len(self.B2)))
            self.p = max(self.p - delta, 0.0)
            self.B2.pop(h)
            self._resurrect.add(h)

    def on_access(self, blk, tick):
        h = blk.hash
        if h in self.T1:
            self.T1.pop(h)
            self.T2[h] = blk          # 二次见面 → 升级常客
        elif h in self.T2:
            self.T2.move_to_end(h)    # 常客刷新热度

    def on_commit(self, blk, tick):
        h = blk.hash
        if h in self._resurrect:
            self._resurrect.discard(h)
            self._untrack(h)
            self.T2[h] = blk          # 幽灵复活 → 直接进常客
        elif h not in self.T1 and h not in self.T2:
            self.T1[h] = blk          # 新人 → 进一面之缘

    def on_evict(self, candidates, tick):
        over_p = len(self.T1) > self.p
        if over_p and self.T1:
            h, _ = next(iter(self.T1.items()))       # T1 最老
            self.T1.pop(h)
            self._ghost_push(self.B1, h)
        elif self.T2:
            h, _ = next(iter(self.T2.items()))       # T2 最老
            self.T2.pop(h)
            self._ghost_push(self.B2, h)
        elif self.T1:
            h, _ = next(iter(self.T1.items()))
            self.T1.pop(h)
            self._ghost_push(self.B1, h)
        else:
            # 簿记兜底（理论上不会发生）：退化为 LRU
            return min(candidates, key=lambda b: b.last_access).hash
        return h
