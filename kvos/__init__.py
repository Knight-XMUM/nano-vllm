"""kvos —— KVOS 回放模拟器（K0 基建，L1 负载层 + L4 证据层的纯 Python 部分）。

把 KV cache 当 OS 内存管：live plane（pin）/ context plane（可驱逐）分账，
驱逐策略抽象为四钩子，八臂对拍由同一份 canonical trace 驱动。

设计约束（写死，不许漂移）：
- 纯标准库，不依赖 torch/CUDA —— Mac 上可跑可测，结果与引擎无关；
- 时钟 = 请求到达序号（PROTOCOL §3），不数墙钟不数 token 步；
- 策略只能作用于 context plane（§3/§14 契约）；
- 本包只做 K0 基建与模拟器侧对拍；真实引擎钩子接在 nanovllm/ 侧（T7 后段）。
"""

__version__ = "0.1.0"
