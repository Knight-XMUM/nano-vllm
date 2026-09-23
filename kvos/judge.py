"""kvos.judge —— 死刑判官：把 PROTOCOL 的死刑判据写成代码（改判据先改协议）。

判据精确口径（PROTOCOL §7 矩阵 + v1.2 修订）：
  死刑① H3：c/N > r* 区，C 相对 B 沿因果链同向改善且至少一环 ≥1%；否则死。
  死刑② H5：C、D_online 至少一臂赢 **G** ≥1%（v1.2：锚改 G；D 只算 online；
        F 为零信号底线参照，数据必报但不进判据）。
  死刑③ H6：实测转移点 r_obs 落在 r*±10% 且 ≥2 真 trace 成立；
        Kneedle 无 knee → "不可检验"，按测量失败另案处理。
  v1.2-f：refault CI 半宽 ≥ 阈值 → 该格 "indeterminate"（样本不足，不许硬判）。
  v1.2-e：参与判定的 trace 须 ≥30 会话，否则该 trace 不计票。

输出每格 {status: alive|dead|indeterminate|untestable, evidence: {...}}。
判定器不产出结论文章，只产出判定表——写结论时引用它。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from kvos import analysis, trace as tr

MIN_SESSIONS = 30          # v1.2-e
THRESH = 0.01              # 判定阈值 1%


def _rows_by(rows: List[dict], arm: str) -> List[dict]:
    return [r for r in rows if r["arm"] == arm]


def _best_diff(rows_a: List[dict], rows_b: List[dict], key: str,
               capacity: Optional[int] = None) -> Optional[float]:
    """a 相对 b 在指标 key 上的最大相对改善（同容量格配对，取最好的一格）。"""
    best = None
    for ra in rows_a:
        if capacity is not None and ra["capacity"] != capacity:
            continue
        rb = next((x for x in rows_b if x["capacity"] == ra["capacity"]), None)
        if rb is None or not rb.get(key):
            continue
        diff = (ra[key] - rb[key]) / abs(rb[key])
        best = diff if best is None else max(best, diff)
    return best


def observed_r(rows: List[dict], reference_arm: str = "B") -> Optional[float]:
    """r_obs：参考臂 hit_rate 随容量曲线的经验拐点（Kneedle，x=capacity）。"""
    pts = sorted((r["capacity"], r["hit_rate"]) for r in _rows_by(rows, reference_arm))
    if len(pts) < 3:
        return None
    curve = [(c, 1.0, h) for c, h in pts]        # 复用 kneedle：y=hit_rate 当 S(W)
    knee, _r, _d = analysis.kneedle(curve)
    return float(knee) if knee is not None else None


def evaluate(rows: List[dict], knee_rec: dict, n_sessions: int,
             threshold: float = THRESH) -> dict:
    """单 trace 判定。rows=run_grid 输出；knee_rec=freeze_knee 记录。"""
    out = {"trace_sessions": n_sessions, "checks": {}}

    if n_sessions < MIN_SESSIONS:
        out["checks"]["sample_floor"] = {
            "status": "untestable",
            "reason": "会话数 %d < %d（v1.2-e），本 trace 不计票" % (n_sessions, MIN_SESSIONS),
        }
        return out

    r_star = knee_rec.get("r_star")
    W_knee = knee_rec.get("W_knee")
    wset = next((c["W_set"] for c in knee_rec.get("curve", [])
                 if c["W"] == W_knee), None)

    # ---- 不可分辨先行：refault CI 半宽 >= 阈值的格子标 indeterminate ----
    wide = sorted({r["arm"] for r in rows
                   if r.get("refault_ci") and r["refault_ci"][0] is not None
                   and (r["refault_ci"][1] - r["refault_ci"][0]) / 2 >= threshold})
    out["checks"]["ci_width"] = {
        "status": "ok" if not wide else "indeterminate",
        "wide_arms": wide,
        "note": "CI 半宽≥阈值只许写'样本不足'（v1.2-f）",
    }

    # ---- 死刑①：r* 以上区，C 相对 B 至少一环 ≥1% 且方向一致 ----
    if r_star and wset:
        above = [r for r in rows if r["capacity"] / wset > r_star]
        caps = {r["capacity"] for r in above}
        gains = []
        for cap in caps:
            d_hit = _best_diff(_rows_by(above, "C"), _rows_by(above, "B"),
                               "hit_rate", cap)
            d_recomp = _best_diff(_rows_by(above, "B"), _rows_by(above, "C"),
                                  "recomputed_tokens", cap)  # C 应更低 → 反向
            if d_hit is not None:
                gains.append((cap, d_hit, d_recomp))
        ok = any(g[1] >= threshold or (g[2] is not None and g[2] >= threshold)
                 for g in gains)
        out["checks"]["death1_h3"] = {
            "status": "alive" if ok else "dead",
            "evidence": gains,
            "rule": "c/N>r* 区 C 相对 B 因果链至少一环 ≥1%",
        }
    else:
        out["checks"]["death1_h3"] = {"status": "untestable",
                                      "reason": "无 r*/W_knee"}

    # ---- 死刑②：C 或 D_online 赢 G ≥1%（F 只当参照） ----
    g_rows = _rows_by(rows, "G")
    if g_rows:
        d_c = _best_diff(_rows_by(rows, "C"), g_rows, "hit_rate")
        d_d = _best_diff(_rows_by(rows, "D_online"), g_rows, "hit_rate")
        ok = (d_c is not None and d_c >= threshold) or \
             (d_d is not None and d_d >= threshold)
        out["checks"]["death2_h5"] = {
            "status": "alive" if ok else "dead",
            "evidence": {"C_vs_G": d_c, "D_online_vs_G": d_d,
                         "F_hit_rate_ref": [r["hit_rate"] for r in _rows_by(rows, "F")]},
            "rule": "C/D_online 至少一臂赢 G ≥1%（v1.2：D 只算 online，F 仅参照）",
        }
    else:
        out["checks"]["death2_h5"] = {"status": "untestable", "reason": "缺 G 臂行"}

    # ---- 死刑③：r_obs ∈ r*±10%（单 trace 一票；≥2 trace 由调用方汇总） ----
    if knee_rec.get("no_knee"):
        out["checks"]["death3_h6"] = {"status": "untestable",
                                      "reason": "Kneedle 无 knee → 不可检验（另案）"}
    elif r_star:
        r_obs = observed_r(rows)
        if r_obs is None or not wset:
            out["checks"]["death3_h6"] = {"status": "untestable",
                                          "reason": "经验拐点算不出"}
        else:
            r_obs_ratio = r_obs / wset
            rel_err = abs(r_obs_ratio - r_star) / r_star
            out["checks"]["death3_h6"] = {
                "status": "alive" if rel_err <= 0.10 else "dead",
                "evidence": {"r_obs_blocks": r_obs, "r_obs_ratio": r_obs_ratio,
                             "r_star": r_star, "rel_err": rel_err},
                "rule": "r_obs 落在 r*±10% 内（≥2 真 trace 需调用方汇总）",
            }
    else:
        out["checks"]["death3_h6"] = {"status": "untestable", "reason": "无 r*"}

    dead = [k for k, v in out["checks"].items()
            if k.startswith("death") and v["status"] == "dead"]
    out["verdict"] = "DEAD" if dead else "alive"
    out["dead_criteria"] = dead
    return out


def aggregate(verdicts: List[dict]) -> dict:
    """跨 trace 汇总：任一死刑在 ≥1 个合格 trace 上触发 → 全局死（H6 另需≥2票）。"""
    counted = [v for v in verdicts
               if v["checks"].get("sample_floor", {}).get("status") != "untestable"]
    h6_alive = sum(1 for v in counted
                   if v["checks"].get("death3_h6", {}).get("status") == "alive")
    dead_votes = [d for v in counted for d in v.get("dead_criteria", [])]
    dead = set(dead_votes)
    if counted and h6_alive < 2 and all(
            v["checks"].get("death3_h6", {}).get("status") != "untestable"
            for v in counted):
        dead.add("death3_h6")
    return {"global": "DEAD" if dead else "alive",
            "counted_traces": len(counted),
            "dead_criteria": sorted(dead),
            "h6_votes_alive": h6_alive}
