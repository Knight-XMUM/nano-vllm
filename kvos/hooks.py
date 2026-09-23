"""kvos.hooks —— 策略四钩子接口（PROTOCOL §8 / PLAN §14）。

四个钩子 = 策略能按的全部按钮：
  on_allocate  新块出生、或被驱逐块因 miss 重生（refault）时
  on_access    块被请求触达时
  on_evict     context 超容、要挑一个牺牲者时 —— 返回牺牲者 hash
  on_commit    块从 live 转入 context（正式进入策略辖区）时

类比：policy 是仓库管理员，这四个钩子是它的全部操作面板。
策略不许碰 live plane，不许偷看未来（oracle 臂除外——它本来就是上界）。
"""

from __future__ import annotations

from typing import List

from kvos.planes import Block


class Policy:
    """所有臂的基类。self.table 由 Replayer 注入（只读查询用）。"""

    name = "?"
    table = None  # DualPlaneTable，注入后可用 is_leaf/shared_count

    def on_allocate(self, blk: Block, tick: int, was_evicted: bool) -> None:
        pass

    def on_access(self, blk: Block, tick: int) -> None:
        pass

    def on_evict(self, candidates: List[Block], tick: int) -> str:
        raise NotImplementedError

    def on_commit(self, blk: Block, tick: int) -> None:
        pass
