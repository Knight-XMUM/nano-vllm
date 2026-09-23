"""kvos.synth —— 五维合成 trace 生成器（PROTOCOL §4.3，单变量诊断专用）。

生成结构：n_chains 条"系统 prompt 式"共享前缀链（各 chain_depth 块），
会话按 Zipf 权重选链 → 共享链块天然高扇出；会话历史块是私有的（扇出 1）。
负载形状可调：会话数/轮数/链数/链深/Zipf 偏度/工具输出概率/think time。

纪律：合成 trace 只用于诊断与门禁自测，任何结论都要等真 trace（§4.3）。
"""

from __future__ import annotations

import random
from typing import List

from kvos import trace as tr


def gen_trace(
    n_sessions: int = 10,
    reqs_per_session: int = 10,
    n_chains: int = 4,
    chain_depth: int = 6,
    zipf_alpha: float = 1.1,
    tool_p: float = 0.3,
    think_ms: int = 8000,
    seed: int = 0,
) -> List[tr.Event]:
    rng = random.Random(seed)
    chains = [["sys%d#%d" % (c, i) for i in range(chain_depth)] for c in range(n_chains)]
    weights = [1.0 / (i + 1) ** zipf_alpha for i in range(n_chains)]

    events: List[tr.Event] = []
    ts = 0
    for s in range(n_sessions):
        chain = rng.choices(chains, weights)[0]
        history: List[str] = []
        parent = None
        for r in range(reqs_per_session):
            gap = think_ms + rng.randint(0, think_ms)   # 实际抽到的思考间隔
            ts += gap
            rid = "s%dr%d" % (s, r)
            ev = tr.Event(
                session_id="s%d" % s,
                request_id=rid,
                parent_request_id=parent,
                arrive_ts_ms=ts,
                think_time_ms=gap,
                # 前缀 = 会话上轮的完整链（已含共享链头）；首轮直接用共享链
                prefix_block_hashes=list(history) if history else list(chain),
                new_prefill_tokens=rng.randint(24, 160),
                gen_tokens=rng.randint(32, 96),
                tool_output_blocks=(1 if rng.random() < tool_p else 0),
            )
            events.append(ev)
            history = tr.access_hashes(ev)  # 本会话当前链 = 上轮全链
            parent = rid
    return events
