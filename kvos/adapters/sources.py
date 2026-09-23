"""五个公开 trace 源的 adapter（K0-2 清单，READING.md §trace 区）。

字段核实状态（2026-09-24 已逐一核对真实文件/官方文档）：
  mooncake  [VERIFIED 实文件] JSONL: {timestamp, input_length, output_length, hash_ids}
            —— hash_ids 是原生块哈希；无会话字段，约定 request=session。
  bailian   [VERIFIED 官方 README] JSONL(LFS): {chat_id, parent_chat_id, timestamp(s),
            input_length, output_length, type, turn, hash_ids} —— hash_ids 是
            16-token 原生块哈希（blksz_16，与 K1 的 BLOCK_SIZE=16 正好一致）。
            文件在 Git LFS（~132MB），需云机 git lfs pull 后跑。
  weka      [VERIFIED 实文件] 单 JSON 文档: {id, block_size:64, hash_id_scope:local,
            requests:[{t(s), type, in, out, hash_ids}]} —— 一个文件一个会话。
  cachewise [VERIFIED 实文件] JSON 数组（每 session 一文件）:
            event_type=llm_call 有 session_id/request_id/input_tokens/output_tokens/
            cache_read_input_tokens —— 无块哈希 → 位置派生（见 notes）。
  tracelab  [VERIFIED 实文件] Claude-Code 式 JSONL: {type, sessionId, uuid,
            parentUuid, timestamp, message.content} —— 无 token 数 → 粗估派生。

纪律：adapter 输出必须先过 K0-4 复核（stats.check_reproduction），
expected_stats() 返回 None 的源 = 未复核，禁止进主分析。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import List, Optional, Tuple

from kvos.adapters.base import Adapter, JsonlAdapter
from kvos.trace import Event, validate_inclusion


def _iso_ms(ts: str) -> int:
    """ISO8601 -> epoch ms。"""
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)


class MooncakeAdapter(JsonlAdapter):
    """github.com/kvcache-ai/Mooncake FAST25-release/traces/toolagent_trace.jsonl"""
    name = "mooncake"
    source_url = "github.com/kvcache-ai/Mooncake FAST25-release/traces/"
    notes = ("hash_ids 为原生块哈希（native）；无 session 字段——约定每条请求即独立会话，"
             "跨请求共享由 hash_ids 天然承载；timestamp 单位按 ms 序使用（只用先后次序）。")
    FIELD_MAP = {
        "session_id": None,                  # 源无会话字段 → parse 时用 request_id
        "request_id": None,                  # 用行号
        "arrive_ts_ms": "timestamp",
        "prefix_block_hashes": "hash_ids",
        "new_prefill_tokens": None,          # hash_ids 已覆盖整个输入
        "gen_tokens": "output_length",
        "tool_output_blocks": None,
    }

    def map_record(self, d: dict, lineno: int):
        hashes = [str(h) for h in d.get("hash_ids", [])]
        if not hashes:
            return None
        return Event(
            session_id="req%d" % lineno,     # 约定：请求即会话（见 notes）
            request_id="req%d" % lineno,
            parent_request_id=None,
            arrive_ts_ms=int(d.get("timestamp", lineno)),
            think_time_ms=0,
            prefix_block_hashes=hashes,
            new_prefill_tokens=0,            # hash_ids 已覆盖全部输入块
            gen_tokens=int(d.get("output_length", 0)),
            tool_output_blocks=0,
            hash_provenance="native",
        )


class BailianAdapter(JsonlAdapter):
    """阿里 ATC'25：qwen_{traceA,traceB,thinking,coder}_blksz_16.jsonl（Git LFS）。"""
    name = "bailian"
    source_url = "github.com/alibaba-edu/qwen-bailian-usagetraces-anon"
    notes = ("hash_ids=盐化 SipHash 的 16-token 块（native）；chat_id/turn 给会话结构；"
             "parent_chat_id=-1 表示根请求（分叉点）；timestamp 为秒 → ×1000。")
    FIELD_MAP = {}

    def map_record(self, d: dict, lineno: int):
        hashes = [str(h) for h in d.get("hash_ids", [])]
        if not hashes:
            return None
        return Event(
            session_id=str(d["chat_id"]),
            request_id="c%s_t%s_%d" % (d["chat_id"], d.get("turn", "?"), lineno),
            parent_request_id=(None if d.get("parent_chat_id", -1) in (-1, None)
                               else "c%s" % d["parent_chat_id"]),
            arrive_ts_ms=int(float(d.get("timestamp", 0)) * 1000),
            think_time_ms=0,
            prefix_block_hashes=hashes,
            new_prefill_tokens=0,
            gen_tokens=int(d.get("output_length", 0)),
            tool_output_blocks=0,
            hash_provenance="native",
        )


class WekaAdapter(Adapter):
    """Weka kv-cache-tester：单 JSON 文档，一个文件一个会话。"""
    name = "weka"
    source_url = "github.com/callanjfox/kv-cache-tester traces/"
    notes = ("hash_ids 为原生块 id（hash_id_scope=local：id 只在本文件内有意义）；"
             "源 block_size=64 ≠ K1 的 16，回放时 1 个源块按 4 个 16tok 槽位折算或"
             "独立口径记录（K0-4 复核时定）；t 为秒 → ×1000。")

    def parse_file(self, path: str) -> Tuple[List[Event], dict]:
        with open(path) as f:
            doc = json.load(f)
        sid = doc.get("id", os.path.basename(path))
        kept: List[Event] = []
        dropped = 0
        for i, r in enumerate(doc.get("requests", [])):
            hashes = ["%s#%s" % (sid, h) for h in r.get("hash_ids", [])]  # local scope → 加文件前缀
            ev = Event(
                session_id=sid,
                request_id="%s_r%d" % (sid, i),
                parent_request_id=("%s_r%d" % (sid, i - 1)) if i else None,
                arrive_ts_ms=int(float(r.get("t", i)) * 1000),
                think_time_ms=0,
                prefix_block_hashes=hashes,
                new_prefill_tokens=max(0, int(r.get("in", 0)) - len(hashes) * 64),
                gen_tokens=int(r.get("out", 0)),
                tool_output_blocks=(1 if r.get("type") not in (None, "n") else 0),
                hash_provenance="native",
            )
            if validate_inclusion(ev):
                kept.append(ev)
            else:
                dropped += 1
        return kept, {"total_lines": len(doc.get("requests", [])),
                      "kept": len(kept), "dropped": dropped,
                      "block_size_source": doc.get("block_size")}


class CacheWiseAdapter(Adapter):
    """CacheWise：parsed_traces/<project>/<session>/events.json（JSON 数组）。

    无块哈希 → 位置派生（hash_provenance=derived）：
    会话内第 i 个块的身份 = f"{session}:{i}"（历史只增不减，位置即身份）；
    首请求的 cache_read_input_tokens 视为项目级共享前缀，映射成
    f"{project}:sys:{i}" —— 跨会话共享的近似。两条假设都在 notes 里写明，
    K0-4 复核时对照源论文统计校准。"""
    name = "cachewise"
    source_url = "github.com/cachewise-project/cachewise-coding-traces parsed_traces/"
    notes = "派生哈希假设：会话内位置=身份；首请求 cache_read 前缀=项目共享段。"

    def parse_file(self, path: str) -> Tuple[List[Event], dict]:
        with open(path) as f:
            rows = json.load(f)                      # JSON 数组
        proj = os.path.basename(os.path.dirname(os.path.dirname(path)))
        kept: List[Event] = []
        dropped = 0
        shared_blocks_hint = None                    # 首请求 cache_read 推共享前缀长
        for i, d in enumerate(rows):
            if d.get("event_type") != "llm_call":
                continue
            sid = str(d.get("session_id") or os.path.basename(os.path.dirname(path)))
            in_tok = int(d.get("input_tokens", 0) or 0)
            cache_read = int(d.get("cache_read_input_tokens", 0) or 0)
            nblocks = max(1, math.ceil(in_tok / 16))
            if shared_blocks_hint is None:
                shared_blocks_hint = min(nblocks, math.ceil(cache_read / 16))
            hashes = [
                ("%s:sys:%d" % (proj, j)) if j < shared_blocks_hint
                else ("%s:%d" % (sid, j))
                for j in range(nblocks)
            ]
            ev = Event(
                session_id=sid,
                request_id=str(d.get("request_id") or "r%d" % i),
                parent_request_id=None,
                arrive_ts_ms=_iso_ms(d["timestamp"]) if d.get("timestamp") else i,
                think_time_ms=0,
                prefix_block_hashes=hashes,
                new_prefill_tokens=int(d.get("cache_creation_input_tokens", 0) or 0),
                gen_tokens=int(d.get("output_tokens", 0) or 0),
                tool_output_blocks=int(d.get("num_tools_called", 0) or 0),
                hash_provenance="derived",
            )
            if validate_inclusion(ev):
                kept.append(ev)
            else:
                dropped += 1
        return kept, {"total_lines": len(rows), "kept": len(kept), "dropped": dropped}


class TraceLabAdapter(Adapter):
    """TraceLab：Claude-Code 式 JSONL 会话（example_sessions/ 下的文件可能带
    '===== record N =====' 人类可读分隔符，解析时跳过）。

    无 token 计数 → 粗估：内容字符数/4 作 token 估计，位置派生块哈希。
    精度不足主分析用，K0-4 复核前先当 smoke 源；正式用需云机 tokenizer 重切。"""
    name = "tracelab"
    source_url = "github.com/uw-syfi/TraceLab"
    notes = "char/4 粗估 token；uuid/parentUuid 给分叉链；需 tokenizer 复核。"

    def parse_file(self, path: str) -> Tuple[List[Event], dict]:
        kept: List[Event] = []
        total = dropped = 0
        depth = 0                                     # 会话内轮深 → 位置派生块数
        sid = None
        prev_rid = None
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("====="):
                    continue
                total += 1
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "user":           # 每轮以 user 消息为请求边界
                    continue
                sid = sid or str(d.get("sessionId") or "unknown")
                depth += 1
                content = d.get("message", {}).get("content", "")
                chars = len(content) if isinstance(content, str) else len(json.dumps(content))
                est_tok = max(16, chars // 4)
                nblocks = min(8, math.ceil(est_tok / 16))  # 估计轮增量块数（粗）
                hashes = ["%s:%d" % (sid, j) for j in range(depth * 4 + nblocks)]
                ev = Event(
                    session_id=sid,
                    request_id=str(d.get("uuid") or "r%d" % total),
                    parent_request_id=(str(d["parentUuid"]) if d.get("parentUuid")
                                       else prev_rid),
                    arrive_ts_ms=_iso_ms(d["timestamp"]) if d.get("timestamp") else total,
                    think_time_ms=0,
                    prefix_block_hashes=hashes,
                    new_prefill_tokens=est_tok,
                    gen_tokens=512,                   # 无 out 字段 → 固定估
                    tool_output_blocks=0,
                    hash_provenance="derived",
                )
                prev_rid = ev.request_id
                if validate_inclusion(ev):
                    kept.append(ev)
                else:
                    dropped += 1
        return kept, {"total_lines": total, "kept": len(kept), "dropped": dropped}


SOURCES = {
    a.name: a for a in (
        MooncakeAdapter, BailianAdapter, WekaAdapter, CacheWiseAdapter, TraceLabAdapter,
    )
}
