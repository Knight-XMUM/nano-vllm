"""D 臂 —— radix 叶子候选中按 (扇出升序, 最近访问升序) 挑牺牲者：
宁可赶走"没人共享的旧块"，留住"多会话共用的高扇出块"。

同一类、两个口径（PROTOCOL §8 纪律：两版都报，互相标定）：
  D_online  —— 用至今观察到的扇出（fanout_obs），可上线
  D_oracle  —— 用 trace 最终扇出（未来信息，不可上线，只做上界对照）
"""

from kvos.hooks import Policy


class FanoutD(Policy):
    name = "D"

    def __init__(self, oracle_fanout=None):
        self.oracle = oracle_fanout  # None -> online 口径

    def on_evict(self, candidates, tick):
        leaves = [b for b in candidates if self.table.is_leaf(b.hash)]
        pool = leaves or candidates
        if self.oracle is not None:
            score = lambda b: (self.oracle.get(b.hash, b.fanout_obs), b.last_access)
        else:
            score = lambda b: (b.fanout_obs, b.last_access)
        return min(pool, key=score).hash
