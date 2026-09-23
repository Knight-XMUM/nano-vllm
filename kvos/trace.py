"""kvos.trace —— canonical 事件 schema（PROTOCOL §4.1 JSONL v1）与派生字段。

职责单一：定义"考卷格式"。加载/落盘/纳入标准校验（§4.2）/
oracle 扇出派生（写入 <trace>.derived.jsonl，原始文件只读）。
access_hashes() 是 replayer 与 S(W) 分析共用的唯一口径——
两个模块绝不各自再造一份，防止口径漂移。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

SCHEMA = "kvos-trace-v1"
BLOCK_SIZE = 16  # K1 固定（PROTOCOL §9）；块大小是独立变量，K3 才扫


@dataclass
class Event:
    """一次请求事件；字段名与 PROTOCOL §4.1 一一对应。"""

    session_id: str
    request_id: str
    parent_request_id: Optional[str]
    arrive_ts_ms: int
    think_time_ms: int
    prefix_block_hashes: List[str]
    new_prefill_tokens: int
    gen_tokens: int
    tool_output_blocks: int
    hash_provenance: str = "derived"  # native | derived


def new_block_hashes(ev: Event) -> List[str]:
    """请求新产生的块的确定性合成哈希（trace 不记 token 内容）。

    同 request_id 重放必得同批哈希 —— 回放确定性（K0-2）的来源之一。
    """
    n = (
        math.ceil(ev.new_prefill_tokens / BLOCK_SIZE)
        + ev.tool_output_blocks
        + math.ceil(ev.gen_tokens / BLOCK_SIZE)
    )
    return ["%s#%d" % (ev.request_id, i) for i in range(n)]


def access_hashes(ev: Event) -> List[str]:
    """请求触达的全部块 = 前缀链 + 新块（顺序即前缀树路径，头在前）。"""
    return list(ev.prefix_block_hashes) + new_block_hashes(ev)


def validate_inclusion(ev: Event) -> bool:
    """§4.2 纳入标准：会话 ID + 时间戳 + 块标识齐备 → 进主分析；
    缺块标识的只用于到达过程/长度分布统计。"""
    has_blocks = (
        bool(ev.prefix_block_hashes)
        or ev.new_prefill_tokens > 0
        or ev.tool_output_blocks > 0
        or ev.gen_tokens > 0
    )
    return bool(ev.session_id) and ev.arrive_ts_ms >= 0 and has_blocks


def load_events(path: str) -> Tuple[List[Event], dict]:
    """读 canonical JSONL；返回 (纳入事件列表, 统计字典)。"""
    kept: List[Event] = []
    total = dropped = 0
    with open(path) as f:
        for total, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError("line %d: 非法 JSON: %s" % (total, e)) from e
            schema = d.get("schema", SCHEMA)
            if schema != SCHEMA:
                raise ValueError("line %d: schema %r != %r" % (total, schema, SCHEMA))
            ev = Event(
                session_id=d["session_id"],
                request_id=d["request_id"],
                parent_request_id=d.get("parent_request_id"),
                arrive_ts_ms=int(d["arrive_ts_ms"]),
                think_time_ms=int(d.get("think_time_ms", 0)),
                prefix_block_hashes=list(d.get("prefix_block_hashes", [])),
                new_prefill_tokens=int(d.get("new_prefill_tokens", 0)),
                gen_tokens=int(d.get("gen_tokens", 0)),
                tool_output_blocks=int(d.get("tool_output_blocks", 0)),
                hash_provenance=d.get("hash_provenance", "derived"),
            )
            if validate_inclusion(ev):
                kept.append(ev)
            else:
                dropped += 1
    return kept, {"total_lines": total, "kept": len(kept), "dropped": dropped}


def dump_events(events: List[Event], path: str) -> None:
    """写 canonical JSONL（sort_keys 保证字节级稳定）。"""
    with open(path, "w") as f:
        for ev in events:
            d = {
                "schema": SCHEMA,
                "session_id": ev.session_id,
                "request_id": ev.request_id,
                "parent_request_id": ev.parent_request_id,
                "arrive_ts_ms": ev.arrive_ts_ms,
                "think_time_ms": ev.think_time_ms,
                "prefix_block_hashes": ev.prefix_block_hashes,
                "new_prefill_tokens": ev.new_prefill_tokens,
                "gen_tokens": ev.gen_tokens,
                "tool_output_blocks": ev.tool_output_blocks,
                "hash_provenance": ev.hash_provenance,
            }
            f.write(json.dumps(d, sort_keys=True) + "\n")


def derive_fanout(events: List[Event]) -> Dict[str, int]:
    """oracle 扇出 = 全 trace 里触达该块的不同会话数（§4.1 派生字段，离线全知）。"""
    fan: Dict[str, Set[str]] = {}
    for ev in events:
        for h in access_hashes(ev):
            fan.setdefault(h, set()).add(ev.session_id)
    return {h: len(s) for h, s in fan.items()}


def derive_next_use(events: List[Event]) -> Dict[str, List[int]]:
    """每块被访问的 tick 序列（Belady 用：驱逐"下次访问最远"的块）。"""
    nxt: Dict[str, List[int]] = {}
    for tick, ev in enumerate(events):
        for h in access_hashes(ev):
            nxt.setdefault(h, []).append(tick)
    return nxt


def write_derived(events: List[Event], path: str) -> None:
    """派生文件 <trace>.derived.jsonl：fanout / shared_flag（原始文件不动）。"""
    fan = derive_fanout(events)
    with open(path, "w") as f:
        for h in sorted(fan):
            row = {"hash": h, "fanout": fan[h], "shared_flag": fan[h] > 1}
            f.write(json.dumps(row, sort_keys=True) + "\n")
