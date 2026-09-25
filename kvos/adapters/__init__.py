"""kvos.adapters —— 真 trace → canonical JSONL v1 的适配层（K0-2/K0-4）。

职责边界：每个 adapter 只做"字段映射 + 单位换算"，不做策略、不改语义。
K0-4 纪律：adapter 输出必须先过 `stats.check_reproduction`（复现源论文自报
命中率/规模统计），复现不了说明解析器有 bug，该 trace 禁止进主分析。

用法：
    from kvos import adapters
    ad = adapters.get("mooncake")            # 或 cachewise / bailian / tracelab / weka
    events, st = ad.parse_file("raw.jsonl")  # -> canonical Event 列表
    report = adapters.stats_report(events)   # K0-4 复核材料
"""

from kvos.adapters.base import Adapter, JsonlAdapter
from kvos.adapters.canonical import CanonicalAdapter
from kvos.adapters.sources import SOURCES
from kvos.adapters.stats import stats_report, check_reproduction


def get(name: str) -> Adapter:
    if name == "canonical":
        return CanonicalAdapter()
    if name not in SOURCES:
        raise KeyError("未知 adapter %r，可选：%s" % (name, sorted(SOURCES)))
    return SOURCES[name]()


def names():
    return ["canonical"] + sorted(SOURCES)
