"""kvos.engine_plane —— 引擎侧双平面（K0-1 正身的非侵入式实现）。

关键观察（读上游 block_manager.py 得来）：nano-vllm 的 `free_block_ids`
deque 已经是一个隐式 FIFO 驱逐器——deallocate 只减 ref_count，块的
hash/token_ids 留在原地，allocate 走 cached 分支还能复活它。
KVOS 要做的事因此非常干净：把这条 FIFO free-list 换成 policy 管理的
context plane，其余上游逻辑一行不动（猴子补丁，不改上游文件）。

身份口径：策略只认**内容哈希**（blk.hash，链式），不认物理 block_id
——物理块会被回收复用，哈希才是内容的身份证。
时机口径：新块在 allocate() 时 hash 还是 -1（上游 hash_blocks() 才打标），
所以新块的 on_allocate 钩子在 hash_blocks 补丁里触发。

鸭子类型：不 import nanovllm（避免 transformers 依赖链），只要求对象有
BlockManager 的同名属性——本模块在 Mac 上可用 FakeBM 完整自测（selftest G7）。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Dict, Optional

from kvos.planes import Block as MetaBlock


class DualPlaneAllocator:
    """wrap 一个上游 BlockManager：refcount 归零的块进 context plane
    （policy 辖区）；分配优先复活 context 命中，free 见底才驱逐。"""

    def __init__(self, bm, policy, context_capacity: Optional[int] = None):
        self.bm = bm
        self.policy = policy
        self.context_capacity = context_capacity   # None = 不设上限（≈上游原行为）
        self.tick = 0                              # 请求粒度时钟（allocate 一响 +1）
        self.context_ids: "OrderedDict[int, None]" = OrderedDict()  # bid 驻留可驱逐
        self.meta: Dict[int, MetaBlock] = {}       # 内容哈希 -> MetaBlock
        self._parent: Dict[int, Optional[int]] = {}  # 哈希 -> 父块哈希
        self._children: Dict[int, int] = {}        # 哈希 -> 驻留子块数（叶子判定）
        self._holders: Dict[int, set] = {}         # 哈希 -> 当前持有它的 seq_id
        self.evict_tick: Dict[int, int] = {}       # 哈希 -> 被驱逐 tick（refault 距离）
        policy.table = _TableView(self)
        self._patch()

    # ---- 元数据 ----

    def _meta(self, h, tick: int, parent=None) -> MetaBlock:
        m = self.meta.get(h)
        if m is None:
            m = MetaBlock(hash=h, plane="live", born=tick, parent=parent)
            self.meta[h] = m
            self._parent[h] = parent
            if parent is not None:
                self._children[parent] = self._children.get(parent, 0) + 1
        return m

    def _note_access(self, h: int, seq_id, tick: int) -> MetaBlock:
        m = self._meta(h, tick)
        m.plane = "live"
        m.last_access = tick
        m.freq += 1
        m.ref_bit = True
        seen = self._holders.setdefault(h, set())
        if seq_id not in seen:
            seen.add(seq_id)
            m.fanout_obs += 1
        return m

    # ---- 驱逐 ----

    def evict(self, block_id: int):
        """context 块真回收：清哈希索引，回 free deque（等 _allocate_block 取用）。"""
        bm = self.bm
        blk = bm.blocks[block_id]
        h = blk.hash
        if h != -1 and bm.hash_to_block_id.get(h) == block_id:
            del bm.hash_to_block_id[h]
        if h in self.meta:
            self.meta[h].plane = "evicted"
            self.evict_tick[h] = self.tick
        p = self._parent.get(h)
        if p is not None:  # h 离开驻留 → 其父块的驻留子块数减一
            self._children[p] = max(0, self._children.get(p, 1) - 1)
        del self.context_ids[block_id]
        bm.free_block_ids.append(block_id)

    def _pick_victim(self) -> int:
        cands = [self.meta[self.bm.blocks[bid].hash] for bid in self.context_ids]
        victim_hash = self.policy.on_evict(cands, self.tick)
        for bid in self.context_ids:
            if self.bm.blocks[bid].hash == victim_hash:
                return bid
        raise AssertionError("policy 选了不在 context 的块: %r" % (victim_hash,))

    def _enforce_capacity(self):
        if self.context_capacity is None:
            return
        while len(self.context_ids) > self.context_capacity:
            self.evict(self._pick_victim())

    # ---- 补丁点（全部走上游同名方法，语义逐行对得上） ----

    def _patch(self):
        bm, self_ = self.bm, self
        orig_alloc_block = bm._allocate_block
        orig_deallocate = bm.deallocate
        orig_hash_blocks = bm.hash_blocks

        def _allocate_block():
            if not bm.free_block_ids:
                self_.evict(self_._pick_victim())   # 牺牲者回 free 尾，下一轮 popleft 取走
            return orig_alloc_block()

        def _deallocate_block(block_id):
            # refcount 归零：不回 free deque，进 context plane
            bm.used_block_ids.remove(block_id)
            self_.context_ids[block_id] = None
            h = bm.blocks[block_id].hash
            if h in self_.meta:
                m = self_.meta[h]
                m.plane = "context"
                self_.policy.on_commit(m, self_.tick)

        def can_allocate(seq):
            # 上游只把 used 命中算免分配；KVOS：context 命中同样免分配，
            # 且供给量 = free + context（不够可以驱逐腾位）
            h = -1
            num_cached = 0
            num_new = seq.num_blocks
            for i in range(seq.num_blocks - 1):
                token_ids = seq.block(i)
                h = bm.compute_hash(token_ids, h)
                bid = bm.hash_to_block_id.get(h, -1)
                if bid == -1 or bm.blocks[bid].token_ids != token_ids:
                    break
                num_cached += 1
                if bid in bm.used_block_ids or bid in self_.context_ids:
                    num_new -= 1
            if len(bm.free_block_ids) + len(self_.context_ids) < num_new:
                return -1
            return num_cached

        def allocate(seq, num_cached_blocks):
            self_.tick += 1
            assert not seq.block_table
            h = -1
            for i in range(num_cached_blocks):
                token_ids = seq.block(i)
                h = bm.compute_hash(token_ids, h)
                bid = bm.hash_to_block_id[h]
                blk = bm.blocks[bid]
                if bid in bm.used_block_ids:
                    blk.ref_count += 1             # live 命中（在跑请求还攥着）
                else:
                    self_.context_ids.pop(bid)     # context 复活 → 回 live
                    blk.ref_count = 1
                    bm.used_block_ids.add(bid)
                seq.block_table.append(bid)
                m = self_._note_access(h, seq.seq_id, self_.tick)
                self_.policy.on_access(m, self_.tick)
            for _ in range(num_cached_blocks, seq.num_blocks):
                seq.block_table.append(_allocate_block())  # 新块 hash=-1，等 hash_blocks 认亲
            seq.num_cached_tokens = num_cached_blocks * bm.block_size
            self_._enforce_capacity()

        def deallocate(seq):
            held = [bm.blocks[bid].hash for bid in seq.block_table]  # 上游会清空表，先快照
            orig_deallocate(seq)
            for h in held:
                s = self_._holders.get(h)
                if s:
                    s.discard(seq.seq_id)
            self_._enforce_capacity()

        def hash_blocks(seq):
            orig_hash_blocks(seq)
            # 新块此刻才有内容身份：注册 meta + 父链 + on_allocate（refault 在此认出）
            for i, bid in enumerate(seq.block_table):
                h = bm.blocks[bid].hash
                if h == -1:
                    continue
                m0 = self_.meta.get(h)
                if m0 is not None and m0.plane != "evicted":
                    continue                       # 已在册（cached 命中在 allocate 记过）
                parent = bm.blocks[seq.block_table[i - 1]].hash if i else None
                was_evicted = h in self_.evict_tick
                self_._note_access(h, seq.seq_id, self_.tick)
                m = self_._meta(h, self_.tick, parent)
                m.plane = "live"
                self_.policy.on_allocate(m, self_.tick, was_evicted)

        bm._allocate_block = _allocate_block
        bm._deallocate_block = _deallocate_block
        bm.can_allocate = can_allocate
        bm.allocate = allocate
        bm.deallocate = deallocate
        bm.hash_blocks = hash_blocks


class _TableView:
    """把 DualPlaneAllocator 包装成 Policy 期望的只读表接口。"""

    def __init__(self, alloc: DualPlaneAllocator):
        self._a = alloc

    @property
    def capacity(self):
        return max(1, len(self._a.context_ids))

    def get(self, h):
        return self._a.meta.get(h)   # 键 = 原始哈希值（int 或 str，视引擎而定）

    def is_leaf(self, h):
        return self._a._children.get(h, 0) == 0

    def shared_count(self, h):
        return len(self._a._holders.get(h, ()))
