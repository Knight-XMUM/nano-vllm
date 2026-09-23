"""五个公开 trace 源的注册表（K0-2 下载清单，READING.md §trace 区）。

字段映射是**待核骨架**：真文件到手后只需要改 FIELD_MAP 的值，
不需要动解析逻辑。映射标 None 的字段 = 源里可能没有/还没核实。

纪律提醒：adapter 输出必须先过 K0-4 复核（stats.check_reproduction），
expected_stats() 返回 None 的源 = 未复核，禁止进主分析。
"""

from __future__ import annotations

from kvos.adapters.base import JsonlAdapter


class MooncakeAdapter(JsonlAdapter):
    """Mooncake FAST'25 toolagent_trace.jsonl（github.com/kvcache-ai/Mooncake）。"""
    name = "mooncake"
    source_url = "github.com/kvcache-ai/Mooncake FAST25-release/traces/toolagent_trace.jsonl"
    notes = "字段名待真实文件核实；工具调用负载，tool_output_blocks 映射待核"
    FIELD_MAP = {
        "session_id": "session_id",
        "request_id": "request_id",
        "parent_request_id": "parent_request_id",
        "arrive_ts_ms": "arrive_ts_ms",
        "think_time_ms": None,               # 待核：若无则由相邻请求间隔推
        "prefix_block_hashes": "block_hashes",   # 待核：native 块哈希字段名
        "new_prefill_tokens": "input_length",
        "gen_tokens": "output_length",
        "tool_output_blocks": None,          # 待核
        "hash_provenance": None,             # 源侧 native → 填 "native" 待核
    }


class CacheWiseAdapter(JsonlAdapter):
    """CacheWise coding traces（github.com/cachewise-project/cachewise-coding-traces）。"""
    name = "cachewise"
    source_url = "github.com/cachewise-project/cachewise-coding-traces"
    notes = "编码 agent 负载；M1 法官 trace；字段名待核"
    FIELD_MAP = {
        "session_id": "conversation_id",
        "request_id": "request_id",
        "parent_request_id": None,
        "arrive_ts_ms": "timestamp",
        "think_time_ms": None,
        "prefix_block_hashes": None,         # 待核：可能要按块大小重切
        "new_prefill_tokens": "input_tokens",
        "gen_tokens": "output_tokens",
        "tool_output_blocks": None,
        "hash_provenance": None,
    }


class BailianAdapter(JsonlAdapter):
    """阿里 ATC'25 Qwen 百炼 trace（github.com/alibaba-edu/qwen-bailian-usagetraces-anon）。"""
    name = "bailian"
    source_url = "github.com/alibaba-edu/qwen-bailian-usagetraces-anon"
    notes = "大厂线上 trace；匿名化可能把会话 ID 打散，session 口径待核"
    FIELD_MAP = {
        "session_id": "chat_id",
        "request_id": "request_id",
        "parent_request_id": None,
        "arrive_ts_ms": "timestamp",
        "think_time_ms": None,
        "prefix_block_hashes": None,         # 线上 trace 多半无块哈希 → derived
        "new_prefill_tokens": "input_tokens",
        "gen_tokens": "output_tokens",
        "tool_output_blocks": None,
        "hash_provenance": None,
    }


class TraceLabAdapter(JsonlAdapter):
    """TraceLab 编码 agent trace（UW，4300 会话/35 万步/43 万工具调用）。"""
    name = "tracelab"
    source_url = "arxiv 2606.30560 开源 trace（链接待核）"
    notes = "规模自报：4300 会话；K0-4 复核重点 = 会话数与工具调用数"
    FIELD_MAP = {
        "session_id": "session_id",
        "request_id": "step_id",
        "parent_request_id": None,
        "arrive_ts_ms": "timestamp",
        "think_time_ms": None,
        "prefix_block_hashes": None,
        "new_prefill_tokens": None,          # 待核：可能要给 token 计数器
        "gen_tokens": None,
        "tool_output_blocks": None,
        "hash_provenance": None,
    }


class WekaAdapter(JsonlAdapter):
    """Weka kv-cache-tester（native block hash —— T7 首选，哈希不用重切）。"""
    name = "weka"
    source_url = "Weka kv-cache-tester trace（链接待核，READING.md 五源之一）"
    notes = "native block hash 直接可用，hash_provenance 应为 native"
    FIELD_MAP = {
        "session_id": "session_id",
        "request_id": "request_id",
        "parent_request_id": None,
        "arrive_ts_ms": "timestamp",
        "think_time_ms": "think_time_ms",
        "prefix_block_hashes": "prefix_block_hashes",
        "new_prefill_tokens": "new_prefill_tokens",
        "gen_tokens": "gen_tokens",
        "tool_output_blocks": "tool_output_blocks",
        "hash_provenance": None,             # 解析时强制 "native"
    }


SOURCES = {
    a.name: a for a in (
        MooncakeAdapter, CacheWiseAdapter, BailianAdapter,
        TraceLabAdapter, WekaAdapter,
    )
}
