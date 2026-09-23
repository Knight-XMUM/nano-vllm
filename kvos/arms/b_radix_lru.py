"""B 臂 —— radix 树 LRU：只许驱逐叶子块（没有子块的链尾），保持前缀树连通。

若全场无叶子（理论上不该发生），退化为普通 LRU 兜底。
"""

from kvos.hooks import Policy


class RadixLRU(Policy):
    name = "B"

    def on_evict(self, candidates, tick):
        leaves = [b for b in candidates if self.table.is_leaf(b.hash)]
        pool = leaves or candidates
        return min(pool, key=lambda b: b.last_access).hash
