"""E 臂 —— Belady oracle：驱逐"下一次被访问最远"的块（永不再用 = 无限远 = 最先走）。

命中率的数学上界，不可上线；存在的意义是给其他七臂当天花板对照（H 系列用）。
"""

from bisect import bisect_right

from kvos.hooks import Policy


class Belady(Policy):
    name = "E"

    def __init__(self, next_use=None):
        self.next_use = next_use or {}  # hash -> 升序 tick 列表（离线派生）

    def on_evict(self, candidates, tick):
        def furthest(b):
            uses = self.next_use.get(b.hash, [])
            i = bisect_right(uses, tick)
            return uses[i] if i < len(uses) else float("inf")

        return max(candidates, key=furthest).hash
