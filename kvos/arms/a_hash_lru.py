"""A 臂 —— content-hash 平面 LRU：任何 context 块都可被选，逐最久未访问者。

对照点：链式哈希下驱逐中间块会让下游子树失联（块还在架上却再也查不到）。
A 不管这个 —— 这正是它与 B（只动叶子）的分野。
"""

from kvos.hooks import Policy


class HashLRU(Policy):
    name = "A"

    def on_evict(self, candidates, tick):
        return min(candidates, key=lambda b: b.last_access).hash
