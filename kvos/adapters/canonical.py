"""canonical 直通 adapter：源文件已是 JSONL v1 时直接走 trace.load_events。"""

from kvos.adapters.base import Adapter
from kvos.trace import load_events


class CanonicalAdapter(Adapter):
    name = "canonical"
    source_url = "(already kvos-trace-v1)"

    def parse_file(self, path):
        return load_events(path)
