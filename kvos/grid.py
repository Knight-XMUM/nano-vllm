"""kvos.grid —— K1 装备：臂×容量网格跑分机 + K1-1 拐点冻结 + v1.2-f 判定器。

纪律写死在代码里：
- K1-1：先 freeze_knee() 把 knee/r* 落盘冻结，跑分函数才认账（防手挑窗口）；
- v1.2-f：CI 半宽 >= 判定阈值 → 该格判 "indistinguishable"，不许硬判输赢；
- 死刑②口径：verdict() 只用于 D_online，D_oracle 不进死刑判定。

用法：
    python3 -m kvos.grid freeze <trace.jsonl>          # K1-1：冻结拐点
    python3 -m kvos.grid run <trace.jsonl> --caps 128 256 512   # 网格（签字后）
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, List, Optional

from kvos import analysis, arms, trace as tr
from kvos.replayer import Replayer


def freeze_knee(trace_path: str, out_path: Optional[str] = None) -> dict:
    """K1-1：从 trace 算 S(W) 曲线 + Kneedle 拐点 + r*，写 <trace>.knee.json。
    冻结内容含曲线全量与判定阈值，跑分一律引用此文件，不许跑后改。"""
    events, st = tr.load_events(trace_path)
    curve = analysis.sw_curve(events)
    W_knee, r_star, diff = analysis.kneedle(curve)
    rec = {
        "trace": trace_path,
        "frozen_at": time.strftime("%Y-%m-%d %H:%M +08"),
        "n_events": len(events),
        "curve": [{"W": w, "W_set": ws, "S": s} for w, ws, s in curve],
        "W_knee": W_knee,
        "r_star": r_star,
        "kneedle_diff": diff,
        "no_knee": W_knee is None,
        "protocol": "PROTOCOL v1.2 §6 Kneedle；max_diff<0.1 判无 knee",
    }
    out = out_path or trace_path.replace(".jsonl", "") + ".knee.json"
    with open(out, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
    return rec


def run_grid(events: List[tr.Event], capacities: List[int],
             arm_names: Optional[List[str]] = None, seed: int = 0) -> List[dict]:
    """每格 = 一个臂 × 一个容量；回同一份 trace。返回逐格指标表。"""
    fan, nxt = tr.derive_fanout(events), tr.derive_next_use(events)
    rows = []
    for cap in capacities:
        for name in (arm_names or arms.ARM_NAMES):
            pol = arms.make(name, seed=seed, oracle_fanout=fan, oracle_next_use=nxt)
            m = Replayer(cap, pol).run(events)
            rs = analysis.refault_stats(m["refault_distances"], m["evictions"])
            med, lo, hi = analysis.bootstrap_ci(m["refault_distances"])
            rows.append({
                "arm": name, "capacity": cap,
                "hit_rate": m["hit_rate"], "evictions": m["evictions"],
                "recomputed_tokens": m["recomputed_tokens"],
                "refault_median": rs["median"], "refault_ci": [lo, hi],
                "censored_ratio": rs["censored_ratio"],
            })
    return rows


def verdict(diff_median: float, ci_lo: float, ci_hi: float,
            threshold: float = 0.01) -> str:
    """v1.2-f 判定器：CI 半宽 >= 阈值 → 不可分辨；否则按中位数过阈值判胜负。
    防的就是死刑被噪声误触发 / 存活被噪声伪造 —— 两头都防。"""
    if None in (diff_median, ci_lo, ci_hi):
        return "indistinguishable"
    if (ci_hi - ci_lo) / 2 >= threshold:
        return "indistinguishable"
    if diff_median >= threshold:
        return "beat"
    if diff_median <= -threshold:
        return "lose"
    return "tie"


def main():
    ap = argparse.ArgumentParser(prog="kvos.grid")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="K1-1：冻结 S(W) 拐点与 r*")
    f.add_argument("trace")
    f.add_argument("--out", default=None)
    r = sub.add_parser("run", help="网格跑分（须先 freeze；签字纪律照旧）")
    r.add_argument("trace")
    r.add_argument("--caps", type=int, nargs="+", required=True)
    r.add_argument("--arms", nargs="*", default=None)
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--allow-unfrozen", action="store_true",
                   help="跳过冻结检查——仅限合成 trace 调试用，真 trace 不许用")
    args = ap.parse_args()

    if args.cmd == "freeze":
        rec = freeze_knee(args.trace, args.out)
        print(json.dumps({k: rec[k] for k in ("W_knee", "r_star", "no_knee",
                                              "kneedle_diff", "n_events")}, indent=2))
    else:
        knee_path = args.trace.replace(".jsonl", "") + ".knee.json"
        if not os.path.exists(knee_path) and not args.allow_unfrozen:
            raise SystemExit(
                "没有冻结文件 %s —— K1-1 纪律：先 `python3 -m kvos freeze %s`"
                " 把拐点落盘再跑分；合成 trace 调试才许加 --allow-unfrozen"
                % (knee_path, args.trace))
        events, st = tr.load_events(args.trace)
        rows = run_grid(events, args.caps, args.arms, args.seed)
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
