"""kvos.demo —— 三分钟看懂 KVOS 在干嘛（零基础可看，Mac 直接跑）。

    python3 -m kvos.demo

演什么：一个只能装 4 块的缓存，两个会话共用一条"系统 prompt 链"。
同一份请求录像放给两个策略看——
  A 臂（LRU）：赶"最久没被碰的"
  D 臂（扇出）：赶"最少人共享的"
看它们在同一格容量下做出不同选择，最后差几个百分点命中率。
全部输出是中文解说，每行对应事件日志里真实发生的一步。
"""

from __future__ import annotations

import json

from kvos import arms, trace as tr
from kvos.replayer import Replayer


def _scene() -> list:
    """手工剧本：容量 4 块，A/B 两会话共用 [s0,s1] 前缀链。"""
    def ev(sid, rid, prefix, ts):
        return tr.Event(session_id=sid, request_id=rid, parent_request_id=None,
                        arrive_ts_ms=ts, think_time_ms=0,
                        prefix_block_hashes=list(prefix),
                        new_prefill_tokens=0, gen_tokens=16, tool_output_blocks=0)
    return [
        ev("A", "a1", ["s0", "s1"], 0),   # A 开工：系统链 s0,s1 + 自己的生成块
        ev("B", "b1", ["s0", "s1"], 1),   # B 也开工：同一条系统链
        ev("B", "b2", ["b0"], 2),         # B 干私活：一块私有前缀
        ev("A", "a2", ["s0", "s1"], 3),   # A 回来继续：还要那条系统链
        ev("B", "b3", ["s0", "s1"], 4),   # B 也回来：也还要它
    ]


_CN = {
    "hit":     "    ✓ 命中 {hash} —— 还在架子上，白捡",
    "miss_a":  "    ✗ {hash} 不在 → 重算（花 token 的）",
    "refault": "    ✗ {hash} 之前被赶走过，现在又要 → 这就是 refault，距上次被赶 {dist} 个请求",
    "alloc":   "    + 新块 {hash} 上架",
    "evict":   "    − 架满了，赶走 {hash}",
}


def _narrate(arm_name: str, events: list, cap: int, blurb: str):
    fan = tr.derive_fanout(events)
    pol = arms.make(arm_name, seed=0, oracle_fanout=fan)
    rep = Replayer(cap, pol)
    m = rep.run(events)
    print("\n" + "=" * 62)
    print("臂 %s —— %s" % (arm_name, blurb))
    print("=" * 62)
    tick = -1
    _refaulted = set()
    for line in rep.log:
        d = json.loads(line)
        if d["tick"] != tick:
            tick = d["tick"]
            _refaulted = set()
            ev = events[tick]
            print("\n  tick%d | 会话%s 的请求 %s：要前缀 %s"
                  % (tick, ev.session_id, ev.request_id, ev.prefix_block_hashes))
        if d["kind"] == "hit":
            print(_CN["hit"].format(**d))
        elif d["kind"] == "alloc":
            # refault 行刚解说过的不重复喊 miss
            if d["hash"] in _refaulted:
                continue
            if d["hash"] in ev.prefix_block_hashes:
                print(_CN["miss_a"].format(**d))
            else:
                print(_CN["alloc"].format(**d))
        elif d["kind"] == "refault":
            _refaulted.add(d["hash"])
            print(_CN["refault"].format(**d))
        elif d["kind"] == "evict":
            print(_CN["evict"].format(**d))
    print("\n  → 命中率 %.1f%%，驱逐 %d 块，refault %d 次"
          % (m["hit_rate"] * 100, m["evictions"], len(m["refault_distances"])))
    return m


def main():
    events = _scene()
    cap = 4
    print("场景：缓存只装得下 %d 块；[s0,s1] 是两个会话共用的系统 prompt 链。"
          % cap)
    print("请求序列共 %d 个，同一卷录像放给两个策略。" % len(events))
    ma = _narrate("A", events, cap, "LRU：赶最久没被碰的（不看块被谁共享）")
    md = _narrate("D_online", events, cap,
                  "扇出：赶最少人共享的（共享块是公共财产，舍不得扔）")
    print("\n" + "-" * 62)
    print("对照：A %.1f%% vs D %.1f%%——"
          "差的那几个百分点，就是'这块有几个人指着它'这条信号多看见的东西。"
          % (ma["hit_rate"] * 100, md["hit_rate"] * 100))
    print("KVOS 要回答的真问题：这条信号在真实负载、真实容量下值多少钱——"
          "值，还是不值，数据说了算。")


if __name__ == "__main__":
    main()
