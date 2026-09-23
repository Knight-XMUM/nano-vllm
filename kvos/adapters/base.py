"""adapter 基类：raw 文件 → canonical Event 流。

契约（K0-2 交付物）：
- parse_file(path) -> (events, stats)   stats 含 total/kept/dropped
- expected_stats() -> dict | None        K0-4：源论文自报统计（未核实填 None，
  None = 该 adapter 尚未通过复核，其输出不许进主分析）
- FIELD_MAP：canonical 字段 -> 源文件字段名；None = 源里没有/待核
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from kvos.trace import Event, validate_inclusion

CANONICAL_FIELDS = (
    "session_id", "request_id", "parent_request_id", "arrive_ts_ms",
    "think_time_ms", "prefix_block_hashes", "new_prefill_tokens",
    "gen_tokens", "tool_output_blocks", "hash_provenance",
)


class Adapter:
    name = "?"
    source_url = ""
    notes = ""

    def parse_file(self, path: str) -> Tuple[List[Event], dict]:
        raise NotImplementedError

    def expected_stats(self) -> Optional[dict]:
        return None


class JsonlAdapter(Adapter):
    """逐行 JSON 源 → canonical：靠 FIELD_MAP 做字段名映射，缺字段用默认值。
    映射没核实过的字段在 FIELD_MAP 里标 None 并在 notes 里写明待核。"""

    FIELD_MAP: Dict[str, Optional[str]] = {}

    def parse_file(self, path: str) -> Tuple[List[Event], dict]:
        kept: List[Event] = []
        total = dropped = 0
        with open(path) as f:
            for total, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                ev = self.map_record(d, total)
                if ev is None:
                    dropped += 1
                elif validate_inclusion(ev):
                    kept.append(ev)
                else:
                    dropped += 1
        return kept, {"total_lines": total, "kept": len(kept), "dropped": dropped}

    def map_record(self, d: dict, lineno: int) -> Optional[Event]:
        kw = {}
        for canon in CANONICAL_FIELDS:
            src = self.FIELD_MAP.get(canon)
            kw[canon] = d.get(src) if src else None
        if kw["session_id"] is None or kw["request_id"] is None:
            return None  # 连身份都没有的行无法纳入
        return Event(
            session_id=str(kw["session_id"]),
            request_id=str(kw["request_id"]),
            parent_request_id=(str(kw["parent_request_id"])
                               if kw["parent_request_id"] is not None else None),
            arrive_ts_ms=int(kw["arrive_ts_ms"] or lineno),
            think_time_ms=int(kw["think_time_ms"] or 0),
            prefix_block_hashes=list(kw["prefix_block_hashes"] or []),
            new_prefill_tokens=int(kw["new_prefill_tokens"] or 0),
            gen_tokens=int(kw["gen_tokens"] or 0),
            tool_output_blocks=int(kw["tool_output_blocks"] or 0),
            hash_provenance=str(kw["hash_provenance"] or "derived"),
        )
