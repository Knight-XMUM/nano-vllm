"""kvos.report —— 把 grid 结果渲染成 markdown 表（OPTLOG 直接粘贴用）。

列序 = 因果链序：命中率 → refault 中位 → 删失率 → 重算 token（§5 指标链）。
"""

from __future__ import annotations

from typing import List


def md_table(rows: List[dict], sort_by: str = "hit_rate") -> str:
    """rows = grid.run_grid 输出；按指标降序排（hit_rate）或升序。"""
    rows = sorted(rows, key=lambda r: (r["capacity"], -(r.get(sort_by) or 0)))
    lines = [
        "| capacity | arm | hit_rate | evictions | refault_med | refault_CI95 | censored | recomputed_tok |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ci = r.get("refault_ci") or [None, None]
        ci_s = "[%s,%s]" % (_fmt(ci[0]), _fmt(ci[1])) if ci[0] is not None else "—"
        lines.append(
            "| %d | %s | %.4f | %d | %s | %s | %.2f | %d |" % (
                r["capacity"], r["arm"], r["hit_rate"], r["evictions"],
                _fmt(r.get("refault_median")), ci_s,
                r.get("censored_ratio", 0.0), r["recomputed_tokens"],
            )
        )
    return "\n".join(lines)


def _fmt(x):
    return ("%.1f" % x) if isinstance(x, float) else ("—" if x is None else str(x))


def verdict_block(verdict: dict) -> str:
    """judge.evaluate 输出 → markdown 小节（OPTLOG 结论栏直接引用）。"""
    lines = ["- verdict: **%s**（counted sessions=%d）"
             % (verdict["verdict"], verdict["trace_sessions"])]
    for k, v in verdict["checks"].items():
        ev = v.get("evidence", v.get("reason", ""))
        lines.append("  - %s: %s — %s" % (k, v["status"], ev))
    return "\n".join(lines)
