"""kvos.analysis —— 阅卷组：S(W) 工作集曲线 / Kneedle / bootstrap / refault 统计。

口径全部对 PROTOCOL §5–§6：
- S(W)：窗 W 内被 ≥2 个不同会话触达的去重块数，对所有滑窗位置取均值（不许挑窗）；
- r* = S(W_knee)/W_set(W_knee)，knee 由 Kneedle 在 log2(W) 轴上找；
- refault 距离中位数只在真 refault 上算，删失率单独报（v1.1b + v1.2-g）。
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from kvos import trace as tr


def sw_curve(events: List[tr.Event], W_list: Optional[List[int]] = None):
    """返回 [(W, W_set均值, S(W)均值)]；滑窗用增量计数，O(n) per W。"""
    n = len(events)
    if W_list is None:
        W_list = [w for w in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384) if w <= n]
    streams = [tr.access_hashes(ev) for ev in events]
    sess = [ev.session_id for ev in events]
    curve = []
    for W in W_list:
        if W > n:
            continue
        cnt: Dict[str, Dict[str, int]] = defaultdict(dict)  # hash -> {sid: 次数}
        for i in range(W):
            for h in streams[i]:
                cnt[h][sess[i]] = cnt[h].get(sess[i], 0) + 1
        wset_sum = sw_sum = 0
        positions = n - W + 1
        for start in range(positions):
            if start:
                out_i, in_i = start - 1, start + W - 1
                for h in streams[out_i]:
                    d = cnt[h]
                    d[sess[out_i]] -= 1
                    if d[sess[out_i]] == 0:
                        del d[sess[out_i]]
                    if not d:
                        del cnt[h]
                for h in streams[in_i]:
                    cnt[h][sess[in_i]] = cnt[h].get(sess[in_i], 0) + 1
            wset_sum += len(cnt)
            sw_sum += sum(1 for d in cnt.values() if len(d) >= 2)
        curve.append((W, wset_sum / positions, sw_sum / positions))
    return curve


def kneedle(curve) -> Tuple[Optional[int], Optional[float], float]:
    """Kneedle（§6）：x=log2(W) 归一化，diff = y_norm - x_norm，knee=argmax。
    返回 (W_knee or None, r* or None, max_diff)；max_diff < 0.1 → 无 knee，不许手挑。"""
    if len(curve) < 3:
        return None, None, 0.0
    xs = [math.log2(w) for w, _, _ in curve]
    ys = [s for _, _, s in curve]
    if xs[-1] == xs[0] or ys[-1] == ys[0]:
        return None, None, 0.0
    xn = [(x - xs[0]) / (xs[-1] - xs[0]) for x in xs]
    yn = [(y - ys[0]) / (ys[-1] - ys[0]) for y in ys]
    diffs = [y - x for x, y in zip(xn, yn)]
    i = max(range(len(diffs)), key=lambda j: diffs[j])
    if diffs[i] < 0.1:
        return None, None, diffs[i]
    W_knee, wset, sw = curve[i]
    return W_knee, (sw / wset if wset else None), diffs[i]


def bootstrap_ci(values, n_resample=1000, seed=0):
    """(中位数, lo, hi) —— 95% CI，§2 统计口径。"""
    v = list(values)
    if not v:
        return None, None, None
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(v, k=len(v))) for _ in range(n_resample))
    lo, hi = meds[int(0.025 * n_resample)], meds[min(int(0.975 * n_resample), n_resample - 1)]
    return statistics.median(v), lo, hi


def refault_stats(refault_distances, n_evicted):
    """v1.1b + v1.2-g：中位数只在真 refault 上算；删失（驱逐后永不再用）单独报率。"""
    d = sorted(refault_distances)
    censored = max(0, n_evicted - len(d))
    out = {
        "n_refaults": len(d),
        "censored": censored,
        "censored_ratio": censored / max(1, n_evicted),
        "median": None,
        "iqr": None,
    }
    if len(d) >= 2:
        q1, _, q3 = statistics.quantiles(d, n=4)
        out["median"], out["iqr"] = statistics.median(d), (q1, q3)
    elif d:
        out["median"] = float(d[0])
    return out
