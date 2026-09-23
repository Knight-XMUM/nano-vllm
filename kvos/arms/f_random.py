"""F 臂 —— 纯随机驱逐：零信号底线（v1.2 重定义）。

存在的意义：Random Attention 等证据提示"随机在推理负载下可能不差"——
如果连随机都赢不了，任何信号臂的收益都要打问号。种子固定保回放确定性。
"""

import random

from kvos.hooks import Policy


class RandomEvict(Policy):
    name = "F"

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def on_evict(self, candidates, tick):
        return self.rng.choice(candidates).hash
