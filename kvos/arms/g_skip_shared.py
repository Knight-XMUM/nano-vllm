"""G 臂 —— LRU 但跳过"当前跨会话 refcount > 1"的块（v1.2 升格臂）。

定位：random（F）< G < D_online 阶梯的中间点 —— 回答"二值共享保护
比 graded 拓扑信号少值多少"。refcount 口径 = 当前引用该块的会话数
（planes.block_sessions 记账），只用在线可观测信息。
"""

from kvos.hooks import Policy


class SkipShared(Policy):
    name = "G"

    def on_evict(self, candidates, tick):
        solo = [b for b in candidates if self.table.shared_count(b.hash) <= 1]
        pool = solo or candidates
        return min(pool, key=lambda b: b.last_access).hash
