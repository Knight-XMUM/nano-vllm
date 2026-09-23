"""adapter 输出的统计复核器 —— K0-4 的机器。

纪律：每个 adapter 必须先复现源论文自报的命中率/规模统计；
复现不了 = 解析器有 bug = 该 trace 禁止进主分析（垃圾进垃圾出防线）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from kvos import trace as tr


def stats_report(events: List[tr.Event]) -> dict:
    """adapter 输出的体检报告：规模 + 结构 + 无限缓存命中率上界。"""
    sessions = {ev.session_id for ev in events}
    fan = tr.derive_fanout(events)
    n_access = sum(len(tr.access_hashes(ev)) for ev in events)
    n_prefix = sum(len(ev.prefix_block_hashes) for ev in events)
    shared = sum(1 for v in fan.values() if v > 1)
    # 无限缓存上界：每块只需第一次算 —— hit_rate_max = 1 - distinct/accesses
    inf_hit = 1 - (len(fan) / n_access) if n_access else 0.0
    return {
        "n_requests": len(events),
        "n_sessions": len(sessions),
        "n_distinct_blocks": len(fan),
        "n_accesses": n_access,
        "n_prefix_accesses": n_prefix,
        "n_shared_blocks": shared,
        "shared_ratio": shared / max(1, len(fan)),
        "infinite_cache_hit_rate": inf_hit,
        "mean_prefix_blocks": n_prefix / max(1, len(events)),
        "hash_provenance": sorted({ev.hash_provenance for ev in events}),
    }


def check_reproduction(events: List[tr.Event], expected: Optional[Dict[str, float]],
                       tol: float = 0.10) -> dict:
    """K0-4 复核：expected 里的每个键与实测对比，相对误差 > tol 即失败。
    expected=None → 未复核 → ok=False（纪律：不许进主分析）。"""
    got = stats_report(events)
    if expected is None:
        return {"ok": False, "reason": "expected_stats 为空（源论文数字未登记）", "got": got}
    fails = {}
    for k, want in expected.items():
        g = got.get(k)
        if g is None or not isinstance(g, (int, float)):
            fails[k] = ("missing", want)
        elif want == 0:
            if g != 0:
                fails[k] = (g, want)
        elif abs(g - want) / abs(want) > tol:
            fails[k] = (g, want)
    return {"ok": not fails, "fails": fails, "got": got}
