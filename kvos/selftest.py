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

import os
import tempfile

from kvos import analysis, arms, synth, trace as tr
from kvos.replayer import Replayer


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

    print("\nselftest %s" % ("ALL PASS" if ok else "HAS FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
