"""kvos.arms —— 八臂驱逐策略（PROTOCOL §8 v1.2 修订版）。

一臂一文件，文件名即臂号；行为差异只许来自策略本身，其余全部相同。
  A  content-hash 平面 LRU        —— 基线
  B  radix 树 LRU（只动叶子）      —— 结构感知基线
  C  B + ARC 频率记忆              —— 频率轴代表
  D  B + 扇出保护（online/oracle）—— 拓扑轴代表，两版都报
  E  Belady oracle                 —— 命中率数学上界（不可上线）
  F  纯随机驱逐                    —— 零信号底线
  G  LRU 但跳过跨会话共享块        —— 民间智慧版拓扑
  H  CLOCK                         —— 最低元数据开销的 recency
"""
from __future__ import annotations

from kvos.arms.a_hash_lru import HashLRU
from kvos.arms.b_radix_lru import RadixLRU
from kvos.arms.c_arc import ARC
from kvos.arms.d_fanout import FanoutD
from kvos.arms.e_belady import Belady
from kvos.arms.f_random import RandomEvict
from kvos.arms.g_skip_shared import SkipShared
from kvos.arms.h_clock import Clock

ARMS = {
    "A": HashLRU,
    "B": RadixLRU,
    "C": ARC,
    "D_online": FanoutD,
    "D_oracle": FanoutD,
    "E": Belady,
    "F": RandomEvict,
    "G": SkipShared,
    "H": Clock,
}

ARM_NAMES = list(ARMS)


def make(name, seed=0, oracle_fanout=None, oracle_next_use=None):
    """按臂号实例化；oracle 信息只发给 E 与 D_oracle（纪律：在线臂不许看未来）。"""
    if name == "E":
        return Belady(next_use=oracle_next_use or {})
    if name == "D_oracle":
        return FanoutD(oracle_fanout=oracle_fanout or {})
    if name == "D_online":
        return FanoutD(oracle_fanout=None)
    if name == "F":
        return RandomEvict(seed=seed)
    return ARMS[name]()
