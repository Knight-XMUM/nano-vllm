"""kvos.planes —— 双平面块表（PLAN §14 契约的模拟器实现）。

live plane    = 正在处理中的请求的块：pin 住，永远不是驱逐候选。
context plane = 已提交、可复用的前缀块：唯一被策略管理的区域。
时钟          = 请求到达序号（§3：不数墙钟不数 token 步，数"第几个请求"）。

类比：live = 灶上正炒的菜（谁也不许动）；context = 备菜架（满了管理员决定扔谁）。
本类只维护状态与不变量；挑谁走是 Policy 的事（关注点分离）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass
class Block:
    hash: str
    plane: str  # "live" | "context" | "evicted"
    last_access: int = 0       # 最近一次被触达的请求 tick（LRU 信号）
    freq: int = 0              # 累计触达次数（频率信号）
    ref_bit: bool = False      # CLOCK 访问位（1 bit 元数据）
    fanout_obs: int = 0        # 至今观察到的不同会话数（D_online 信号）
    parent: Optional[str] = None
    born: int = 0


class DualPlaneTable:
    def __init__(self, capacity_blocks: int):
        assert capacity_blocks >= 1
        self.capacity = capacity_blocks        # context plane 容量（块数）
        self.live: Dict[str, Block] = {}
        self.context: Dict[str, Block] = {}    # dict 保序：插入序≈入架序
        self.evict_tick: Dict[str, int] = {}   # hash -> 被驱逐 tick（refault 距离用）
        self.block_sessions: Dict[str, Set[str]] = {}  # 当前引用会话集（G 的 refcount）
        self.session_chain: Dict[str, List[str]] = {}  # 会话当前链
        self._fanout_seen: Dict[str, Set[str]] = {}    # fanout_obs 的计数底账
        self._children: Dict[str, int] = {}    # 前缀树子节点数（跨驱逐存活，B 叶子规则）

    # ---- 查询（策略只许通过这些接口看世界） ----

    def get(self, h: str) -> Optional[Block]:
        return self.live.get(h) or self.context.get(h)

    def candidates(self) -> List[Block]:
        """驱逐候选集 = context plane 全体（不变量：永远不含 live）。"""
        return list(self.context.values())

    def shared_count(self, h: str) -> int:
        """当前跨会话引用数 —— G 臂的 refcount 口径。"""
        return len(self.block_sessions.get(h, ()))

    def is_leaf(self, h: str) -> bool:
        """前缀树叶子 = 没有任何已分配块指认它为父块。"""
        return self._children.get(h, 0) == 0

    # ---- 请求内动作（由 replayer 调用） ----

    def access(self, blk: Block, tick: int, session_id: str) -> None:
        if blk.plane == "context":
            del self.context[blk.hash]
            self.live[blk.hash] = blk
            blk.plane = "live"
        blk.last_access = tick
        blk.freq += 1
        blk.ref_bit = True
        seen = self._fanout_seen.setdefault(blk.hash, set())
        if session_id not in seen:
            seen.add(session_id)
            blk.fanout_obs += 1

    def allocate(self, h: str, tick: int, parent: Optional[str] = None) -> Block:
        blk = Block(hash=h, plane="live", born=tick, parent=parent)
        self.live[h] = blk
        if parent is not None:
            self._children[parent] = self._children.get(parent, 0) + 1
        return blk

    def commit(self) -> None:
        """请求结束：其全部块从 live 转入 context（成为可复用前缀）。"""
        for blk in self.live.values():
            blk.plane = "context"
            self.context[blk.hash] = blk
        self.live.clear()

    def evict(self, h: str, tick: int) -> Block:
        blk = self.context.pop(h)
        blk.plane = "evicted"
        self.evict_tick[h] = tick
        if blk.parent is not None:
            self._children[blk.parent] = max(0, self._children.get(blk.parent, 1) - 1)
        return blk

    # ---- 会话链记账（G 的"当前跨会话 refcount"） ----

    def update_session_chain(self, session_id: str, chain: List[str]) -> None:
        """会话当前链 = 本次请求的完整触达路径；分叉时旧链自动退订。"""
        new, old = set(chain), set(self.session_chain.get(session_id, []))
        for h in old - new:
            s = self.block_sessions.get(h)
            if s:
                s.discard(session_id)
        for h in new - old:
            self.block_sessions.setdefault(h, set()).add(session_id)
        self.session_chain[session_id] = list(chain)
