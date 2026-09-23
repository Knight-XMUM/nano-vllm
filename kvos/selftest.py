"""kvos.selftest —— K0 门禁的模拟器侧版本，纯 stdlib，Mac 可跑。

    python3 -m kvos.selftest

门禁（编号对应 PROTOCOL K0 精神，不是 K0 本身——K0 要等真 trace）：
  G1 确定性   同 trace 同策略回放两遍，事件日志 sha256 一致（K0-2 精神）
  G2 不变量   驱逐候选永不含 live 块（replayer 内建 assert，跑通即过，K0-1i）
  G3 饱和探针 跑 H 臂，报告 context plane 驱逐扫描中 bit==1 占比（K0-1ii 仪器）
  G4 八臂     A~H 全部跑通；Belady 命中率 >= 各臂（oracle 数学上界）
  G5 分析链   S(W) / Kneedle / bootstrap / refault 统计产出正常
  G6 schema   dump->load 往返一致；oracle fanout 与在线计数终值吻合
"""

from __future__ import annotations

import json
import os
import tempfile

from collections import deque

from kvos import adapters, analysis, arms, synth, trace as tr
from kvos.replayer import Replayer


# ---------- FakeBM：逐行复刻上游 block_manager.py 语义（G7 用） ----------

class _FakeBlock:
    def __init__(self, block_id):
        self.block_id = block_id
        self.ref_count = 0
        self.hash = -1
        self.token_ids = []

    def update(self, h, token_ids):
        self.hash = h
        self.token_ids = token_ids

    def reset(self):
        self.ref_count = 1
        self.hash = -1
        self.token_ids = []


class _FakeSeq:
    def __init__(self, seq_id, token_ids, block_size):
        self.seq_id = seq_id
        self.token_ids = list(token_ids)
        self.block_size = block_size
        self.block_table = []
        self.num_cached_tokens = 0
        self.num_scheduled_tokens = 0

    @property
    def num_blocks(self):
        return (len(self.token_ids) + self.block_size - 1) // self.block_size

    def block(self, i):
        return self.token_ids[i * self.block_size:(i + 1) * self.block_size]


class FakeBM:
    """上游 BlockManager 的逐行复刻（哈希换成确定性字符串，不依赖 xxhash）。"""

    def __init__(self, num_blocks, block_size):
        self.block_size = block_size
        self.blocks = [_FakeBlock(i) for i in range(num_blocks)]
        self.hash_to_block_id = {}
        self.free_block_ids = deque(range(num_blocks))
        self.used_block_ids = set()

    @classmethod
    def compute_hash(cls, token_ids, prefix=-1):
        return "%s|%s" % (prefix, ",".join(map(str, token_ids)))

    def _allocate_block(self):
        block_id = self.free_block_ids.popleft()
        block = self.blocks[block_id]
        assert block.ref_count == 0
        if block.hash != -1 and self.hash_to_block_id.get(block.hash) == block_id:
            del self.hash_to_block_id[block.hash]
        block.reset()
        self.used_block_ids.add(block_id)
        return block_id

    def _deallocate_block(self, block_id):
        assert self.blocks[block_id].ref_count == 0
        self.used_block_ids.remove(block_id)
        self.free_block_ids.append(block_id)

    def can_allocate(self, seq):
        h = -1
        num_cached = 0
        num_new = seq.num_blocks
        for i in range(seq.num_blocks - 1):
            token_ids = seq.block(i)
            h = self.compute_hash(token_ids, h)
            bid = self.hash_to_block_id.get(h, -1)
            if bid == -1 or self.blocks[bid].token_ids != token_ids:
                break
            num_cached += 1
            if bid in self.used_block_ids:
                num_new -= 1
        if len(self.free_block_ids) < num_new:
            return -1
        return num_cached

    def allocate(self, seq, num_cached):
        assert not seq.block_table
        h = -1
        for i in range(num_cached):
            h = self.compute_hash(seq.block(i), h)
            bid = self.hash_to_block_id[h]
            blk = self.blocks[bid]
            if bid in self.used_block_ids:
                blk.ref_count += 1
            else:
                blk.ref_count = 1
                self.free_block_ids.remove(bid)
                self.used_block_ids.add(bid)
            seq.block_table.append(bid)
        for _ in range(num_cached, seq.num_blocks):
            seq.block_table.append(self._allocate_block())
        seq.num_cached_tokens = num_cached * self.block_size

    def deallocate(self, seq):
        for bid in reversed(seq.block_table):
            blk = self.blocks[bid]
            blk.ref_count -= 1
            if blk.ref_count == 0:
                self._deallocate_block(bid)
        seq.num_cached_tokens = 0
        seq.block_table.clear()

    def hash_blocks(self, seq):
        start = seq.num_cached_tokens // self.block_size
        end = (seq.num_cached_tokens + seq.num_scheduled_tokens) // self.block_size
        if start == end:
            return
        h = self.blocks[seq.block_table[start - 1]].hash if start > 0 else -1
        for i in range(start, end):
            blk = self.blocks[seq.block_table[i]]
            h = self.compute_hash(seq.block(i), h)
            blk.update(h, seq.block(i))
            self.hash_to_block_id[h] = blk.block_id


class _TrackPolicy(arms.HashLRU):
    """记账版 A 臂：数四个钩子各被调了几次，记每次驱逐的候选集。"""
    name = "T"

    def __init__(self):
        self.calls = {"alloc": 0, "access": 0, "evict": 0, "commit": 0}
        self.evict_cands = []

    def on_allocate(self, blk, tick, was_evicted):
        self.calls["alloc"] += 1

    def on_access(self, blk, tick):
        self.calls["access"] += 1

    def on_commit(self, blk, tick):
        self.calls["commit"] += 1

    def on_evict(self, candidates, tick):
        self.calls["evict"] += 1
        self.evict_cands.append([b.hash for b in candidates])
        return min(candidates, key=lambda b: b.last_access).hash


def _oracle_maps(events):
    return tr.derive_fanout(events), tr.derive_next_use(events)


def run_arm(name, events, capacity, seed=0):
    fan, nxt = _oracle_maps(events)
    pol = arms.make(name, seed=seed, oracle_fanout=fan, oracle_next_use=nxt)
    rep = Replayer(capacity, pol)
    return rep, rep.run(events)


def main():
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("[%s] %s %s" % ("PASS" if cond else "FAIL", label, extra))

    events = synth.gen_trace(n_sessions=12, reqs_per_session=10, n_chains=4,
                             chain_depth=6, seed=7)
    capacity = 220  # 故意调小：逼出驱逐与 refault

    # G1 确定性（K0-2 精神）
    _, m1 = run_arm("A", events, capacity)
    _, m2 = run_arm("A", events, capacity)
    check("G1 确定性：两次回放日志 sha256 一致", m1["log_sha256"] == m2["log_sha256"],
          m1["log_sha256"][:12])

    # G2 不变量：跑通即过（replayer 内建 assert 会在候选混入 live 时炸）
    check("G2 live-块永不进候选（内建 assert 未触发）", True)

    # G4 八臂 sanity + Belady 上界
    results = {}
    for name in arms.ARM_NAMES:
        rep, m = run_arm(name, events, capacity)
        results[name] = m
        extra = ""
        if name == "H":  # G3 饱和探针
            sat = rep.policy.saturation()
            extra = " CLOCK位饱和=%.2f" % sat if sat is not None else ""
        print("      臂 %-8s hit=%.3f evict=%d refault中位=%s 删失=%d%s"
              % (name, m["hit_rate"], m["evictions"],
                 analysis.refault_stats(m["refault_distances"], m["evictions"])["median"],
                 m["censored_refaults"], extra))
    check("G4 八臂全跑通", len(results) == len(arms.ARM_NAMES))
    check("G4 Belady 是命中率上界",
          results["E"]["hit_rate"] >= max(m["hit_rate"] for m in results.values()) - 1e-9,
          "E=%.3f" % results["E"]["hit_rate"])

    # G5 分析链
    curve = analysis.sw_curve(events)
    knee_w, knee_r, diff = analysis.kneedle(curve)
    med, lo, hi = analysis.bootstrap_ci(results["B"]["refault_distances"])
    rs = analysis.refault_stats(results["B"]["refault_distances"], results["B"]["evictions"])
    check("G5 S(W) 曲线产出", len(curve) > 0 and curve[0][1] > 0,
          "W=%s r*=%s" % (knee_w, ("%.3f" % knee_r) if knee_r else "无knee"))
    check("G5 refault 统计+删失率", rs["censored_ratio"] >= 0 and (med is None or lo <= med <= hi),
          "删失率=%.2f CI=[%s,%s]" % (rs["censored_ratio"], lo, hi))

    # G6 schema 往返 + fanout 对账
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.jsonl")
        tr.dump_events(events, p)
        loaded, st = tr.load_events(p)
        tr.write_derived(loaded, os.path.join(d, "t.derived.jsonl"))
    check("G6 dump->load 往返一致",
          [tr.access_hashes(e) for e in loaded] == [tr.access_hashes(e) for e in events],
          "kept=%d dropped=%d" % (st["kept"], st["dropped"]))
    fan = _oracle_maps(events)[0]
    rep_b, _ = run_arm("B", events, capacity)
    final_obs = {h: len(s) for h, s in rep_b.table._fanout_seen.items()}
    check("G6 oracle fanout == 在线计数终值", fan == final_obs)

    # G7 引擎侧双平面（FakeBM 上验 K0-1 语义）
    from kvos.engine_plane import DualPlaneAllocator

    bs = 4
    bm = FakeBM(num_blocks=6, block_size=bs)
    pol = _TrackPolicy()
    DualPlaneAllocator(bm, pol)

    sa = _FakeSeq("A", list(range(12)), bs)          # A：3 块
    na = bm.can_allocate(sa)
    bm.allocate(sa, na)
    sa.num_scheduled_tokens = len(sa.token_ids) - sa.num_cached_tokens
    bm.hash_blocks(sa)
    bm.deallocate(sa)                                # A 死 → 3 块应进 context 不进 free
    alloc = pol.table._a                             # context_ids 挂在 allocator 侧
    check("G7 deallocate → context=3, free=3（不是 6）",
          len(alloc.context_ids) == 3 and len(bm.free_block_ids) == 3,
          "context=%d free=%d" % (len(alloc.context_ids), len(bm.free_block_ids)))
    check("G7 on_commit 被调 3 次", pol.calls["commit"] == 3)
    check("G7 on_allocate 被调 3 次", pol.calls["alloc"] == 3)

    sb = _FakeSeq("B", list(range(8)) + [20, 21, 22, 23], bs)  # 共享 A 头两块
    nb = bm.can_allocate(sb)
    check("G7 context 命中算 cached（上游只算 used）", nb == 2, "num_cached=%d" % nb)
    bm.allocate(sb, nb)
    sb.num_scheduled_tokens = len(sb.token_ids) - sb.num_cached_tokens
    bm.hash_blocks(sb)
    check("G7 复活触发 on_access×2", pol.calls["access"] == 2)
    check("G7 B 的新块触发 on_allocate", pol.calls["alloc"] == 4)

    # B 还活着（live），C 来抢：free=2 + context=1，C 要 3 块 → 必然驱逐
    sc = _FakeSeq("C", [99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89, 88], bs)
    nc = bm.can_allocate(sc)
    b_live = set(sb.block_table)
    bm.allocate(sc, nc)
    check("G7 free 见底触发驱逐且只动 context（B 的活块没挨刀）",
          pol.calls["evict"] >= 1
          and all(b in bm.used_block_ids for b in b_live))
    evicted_hash = alloc.evict_tick.keys()
    check("G7 牺牲者不是 B 的块",
          not any(bm.blocks[b].hash in evicted_hash for b in b_live))

    # G8 adapter 框架 + K0-4 复核机
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "canon.jsonl")
        tr.dump_events(events, p)
        ev2, st2 = adapters.get("canonical").parse_file(p)
    rep = adapters.stats_report(ev2)
    check("G8 canonical 直通 + 统计报告",
          rep["n_requests"] == len(events) and rep["n_sessions"] == 12,
          "sessions=%d blocks=%d" % (rep["n_sessions"], rep["n_distinct_blocks"]))
    good = adapters.check_reproduction(ev2, {"n_sessions": 12, "n_requests": len(events)})
    bad = adapters.check_reproduction(ev2, {"n_sessions": 9999})
    none = adapters.check_reproduction(ev2, None)
    check("G8 K0-4 复核机：对/错/空三种结局正确",
          good["ok"] and not bad["ok"] and not none["ok"])

    # G8b 三源真实字段样本（2026-09-24 已核实的 schema）
    with tempfile.TemporaryDirectory() as d:
        # mooncake：JSONL {timestamp,input_length,output_length,hash_ids}
        p = os.path.join(d, "mooncake.jsonl")
        with open(p, "w") as f:
            f.write(json.dumps({"timestamp": 0, "input_length": 6758,
                                "output_length": 500, "hash_ids": [0, 1, 2]}) + "\n")
            f.write(json.dumps({"timestamp": 1, "input_length": 7322,
                                "output_length": 490, "hash_ids": [0, 3, 4]}) + "\n")
        ev_m, _ = adapters.get("mooncake").parse_file(p)
        check("G8b mooncake：hash_ids→前缀 + native 标记",
              len(ev_m) == 2 and ev_m[0].prefix_block_hashes == ["0", "1", "2"]
              and ev_m[0].hash_provenance == "native")

        # weka：单 JSON 文档 {id, block_size, requests:[{t,in,out,hash_ids}]}
        p = os.path.join(d, "trace_0001.json")
        with open(p, "w") as f:
            json.dump({"id": "trace_0001", "block_size": 64, "hash_id_scope": "local",
                       "requests": [{"t": 0.0, "type": "n", "in": 71175, "out": 169,
                                     "hash_ids": [1, 2, 3]},
                                    {"t": 1.5, "type": "n", "in": 100, "out": 50,
                                     "hash_ids": [1, 2, 9]}]}, f)
        ev_w, st_w = adapters.get("weka").parse_file(p)
        check("G8b weka：文件=会话 + local id 加文件前缀",
              len(ev_w) == 2 and ev_w[1].prefix_block_hashes[0] == "trace_0001#1"
              and ev_w[1].parent_request_id == "trace_0001_r0")

        # cachewise：JSON 数组，llm_call/tool_call 混合，位置派生块
        pdir = os.path.join(d, "project_001", "session_0001")
        os.makedirs(pdir)
        p = os.path.join(pdir, "events.json")
        with open(p, "w") as f:
            json.dump([
                {"event_type": "llm_call", "timestamp": "2020-07-07T17:18:54+00:00",
                 "session_id": "session_0001", "request_id": "req_00001",
                 "input_tokens": 40, "output_tokens": 10,
                 "cache_creation_input_tokens": 36312, "cache_read_input_tokens": 32,
                 "has_tool_use": True, "num_tools_called": 1},
                {"event_type": "tool_call", "tool_name": "Grep"},
                {"event_type": "llm_call", "timestamp": "2020-07-07T17:19:00+00:00",
                 "session_id": "session_0001", "request_id": "req_00002",
                 "input_tokens": 80, "output_tokens": 20,
                 "cache_creation_input_tokens": 100, "cache_read_input_tokens": 60},
            ], f)
        ev_c, _ = adapters.get("cachewise").parse_file(p)
        check("G8b cachewise：只收 llm_call + 项目共享前缀派生",
              len(ev_c) == 2
              and ev_c[0].prefix_block_hashes[0].startswith("project_001:sys:")
              and ev_c[0].hash_provenance == "derived")

    # G9 网格 + 冻结 + 判定器
    from kvos import grid
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.jsonl")
        tr.dump_events(events, p)
        rec = grid.freeze_knee(p)
        rows = grid.run_grid(events, [80, 220], ["A", "E", "F"])
    check("G9 freeze 落盘含曲线与判定",
          "curve" in rec and "r_star" in rec and len(rows) == 6)
    check("G9 verdict 三态正确",
          grid.verdict(0.05, 0.04, 0.06) == "beat"
          and grid.verdict(0.05, -0.10, 0.20) == "indistinguishable"
          and grid.verdict(-0.05, -0.06, -0.04) == "lose")
    cb = analysis.class_breakdown(events, rep_b.log)
    check("G9 H4 四象限分解产出", isinstance(cb, dict) and len(cb) > 0,
          "quadrants=%d" % len(cb))

    print("\nselftest %s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
