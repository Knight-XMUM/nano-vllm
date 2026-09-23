"""kvos.replayer —— 回放器（PROTOCOL §4.4 / K0-2）。

职责：驱动请求到达时钟、维护双平面、调用策略四钩子、逐条记事件日志。
类比：同一卷磁带给每个考生各放一遍，答题卡逐条记录，谁也别想换题。

关键语义（与 nano-vllm 链式哈希对齐）：
- 前缀查找遇到首个 miss 即整段后缀判 miss（父块不在，子块哈希链断裂）；
- miss 块按原哈希重生（内容是确定的），曾遭驱逐者记 refault + 距离；
- 驱逐只发生在请求间（commit 之后），live plane 永远不进候选集（K0-1 不变量）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List

from kvos import trace as tr
from kvos.planes import DualPlaneTable


class Replayer:
    def __init__(self, capacity_blocks: int, policy):
        self.table = DualPlaneTable(capacity_blocks)
        self.policy = policy
        policy.table = self.table  # 注入只读查询接口（is_leaf / shared_count / get）
        self.log: List[str] = []
        self.hits = 0
        self.misses = 0
        self.recomputed_tokens = 0
        self.evictions = 0
        self.refaults: List[int] = []      # 每次 refault 的距离（请求数）
        self._refaulted = set()            # 至少重生过一次的块

    def _emit(self, **kw):
        self.log.append(json.dumps(kw, sort_keys=True))

    def run(self, events: List[tr.Event]) -> dict:
        for tick, ev in enumerate(events):
            self._request(tick, ev)
        return self._metrics()

    # ---- 内部 ----

    def _request(self, tick: int, ev: tr.Event):
        t, p = self.table, self.policy
        prefix = ev.prefix_block_hashes

        # 1) 前缀查找：链式哈希 → 首个 miss 后整段后缀必 miss
        k = 0
        for h in prefix:
            blk = t.get(h)
            if blk is None:
                break
            t.access(blk, tick, ev.session_id)
            p.on_access(blk, tick)
            self.hits += 1
            self._emit(tick=tick, kind="hit", hash=h)
            k += 1

        missed = prefix[k:]
        self.misses += len(missed)
        self.recomputed_tokens += len(missed) * tr.BLOCK_SIZE

        # 2) miss 重算 + 新块出生；父块 = 链上前驱
        parent = prefix[k - 1] if k else None
        for h in missed + tr.new_block_hashes(ev):
            # was_evicted 只认"真被策略驱逐过且不驻留"：链断孤儿虽在 missed 里
            # 却仍驻留 context（父块死了它没死），当前世没被逐过 → 不算 refault
            was_evicted = h in t.evict_tick and t.get(h) is None
            if was_evicted:
                dist = tick - t.evict_tick[h]
                self.refaults.append(dist)
                self._refaulted.add(h)
                self._emit(tick=tick, kind="refault", hash=h, dist=dist)
            blk = t.allocate(h, tick, parent=parent)
            t.access(blk, tick, ev.session_id)
            p.on_allocate(blk, tick, was_evicted)
            self._emit(tick=tick, kind="alloc", hash=h)
            parent = h

        # 3) commit：live → context；更新会话链（G 的 refcount 底账）
        for blk in list(t.live.values()):
            p.on_commit(blk, tick)
        t.commit()
        t.update_session_chain(ev.session_id, tr.access_hashes(ev))

        # 4) 容量执法：只在 context plane 驱逐（K0-1 不变量由 assert 锁死）
        while len(t.context) > t.capacity:
            cands = t.candidates()
            assert all(b.plane == "context" for b in cands)
            victim = p.on_evict(cands, tick)
            assert victim in t.context, "policy 必须选 context 内的块: %s" % victim
            t.evict(victim, tick)
            self.evictions += 1
            self._emit(tick=tick, kind="evict", hash=victim)

    def _metrics(self) -> dict:
        evicted = set(self.table.evict_tick)
        return {
            "arm": self.policy.name,
            "accesses": self.hits + self.misses,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hits / max(1, self.hits + self.misses),
            "evictions": self.evictions,
            "refault_distances": sorted(self.refaults),
            "censored_refaults": len(evicted - self._refaulted),
            "recomputed_tokens": self.recomputed_tokens,
            "log_sha256": hashlib.sha256("\n".join(self.log).encode()).hexdigest(),
        }
