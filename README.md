> **Research fork** of [GeeeekExplorer/nano-vllm](https://github.com/GeeeekExplorer/nano-vllm)
> (pinned at `bb823b3`, MIT © Xingkai Yu — original README kept intact below).
> This fork is the engine layer of **KVOS**: a controlled measurement platform for
> KV-cache eviction policies under agent-style long-session workloads.

# KVOS research fork

**Question under study:** which signal — recency, frequency, or topology (prefix-tree
blast radius) — should dominate KV-block eviction at which cache/working-set ratio (c/N)?
Hypotheses (H1–H9), the trace schema, the metric chain, and three kill criteria are
**pre-registered** before any policy benchmark runs.

**Status (2026-09-23):** engine verified and version-pinned. Deviations from upstream so
far are minimal and logged: a true-greedy sampler branch (`temperature=0` → argmax, in
`nanovllm/layers/sampler.py`), and the **`kvos/` replay simulator** — a pure-stdlib,
GPU-free prototype of the dual-plane allocator, the four policy hooks, and the eight-arm
eviction suite (`python3 -m kvos.selftest` runs the K0-style gates; see `kvos/README.md`
for the module map). Engine-side wiring into `block_manager.py` comes later (T5 gate).

## Verified evidence (measured, not claimed)

| Check | Setup | Result |
|---|---|---|
| Correctness vs vLLM | Qwen2.5-0.5B-Instruct `7ae55760`, `enforce_eager`, batch=1 | token-for-token identical to vLLM 0.11.0 (`18553c5`) |
| Decode throughput | single request, RTX PRO 6000 96GB, eager | ~105 tok/s (vLLM ~131, HF transformers ~45) |
| Baseline gap | — | ~80% of vLLM; the remaining ~25% is the optimization budget |

Recorded as experiment E-001 (2026-09-16). Raw numbers; warmup runs discarded.

## Roadmap (K0–K4)

- **K0** — dual-plane allocator (live plane pinned / context plane evictable), four policy
  hooks (`on_allocate` / `on_access` / `on_evict` / `on_commit`), deterministic trace
  replayer, logprob-parity gate (≤1e-4)
- **K1** — eight-arm eviction grid over c/N: A hash-block LRU · B radix-tree LRU ·
  C +ARC frequency · D +blast-radius fanout (online & oracle) · E Belady bound ·
  F pure random · G skip-shared heuristic · H CLOCK (already implemented in `kvos/arms/`).
  Metric chain: refault distance → recomputed tokens → PCIe bytes → P99 TTFT/TBT
- **K2** — COW fork for branched generation + HBM→host lookahead offload
- **K3** — block-size / fragmentation study, migration cost model, MoE expert paging repro
- **K4** — tech memo + minimal upstream PR (pluggable evictor interface)

## Reproduce the baseline

```bash
pip install -e .        # needs torch 2.8.0+cu128, transformers 5.10.4, flash-attn
huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct --local-dir ~/huggingface/Qwen2.5-0.5B
# then point `path` in bench.py at your model dir; enforce_eager=True for a fair A/B
python bench.py
```

Correctness gate: greedy-decode identical prompts through HF transformers and vLLM;
the first 128 generated tokens must match before any performance number is reported.

*The pre-registration protocol and lab notebook (Chinese) live in a companion workspace
and will be published with the K4 technical memo.*

---

# Original upstream README

<p align="center">
<img width="300" src="assets/logo.png">
</p>

<p align="center">
<a href="https://trendshift.io/repositories/15323" target="_blank"><img src="https://trendshift.io/api/badge/repositories/15323" alt="GeeeekExplorer%2Fnano-vllm | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>
</p>

# Nano-vLLM

A lightweight vLLM implementation built from scratch.

## Key Features

* 🚀 **Fast offline inference** - Comparable inference speeds to vLLM
* 📖 **Readable codebase** - Clean implementation in ~ 1,200 lines of Python code
* ⚡ **Optimization Suite** - Prefix caching, Tensor Parallelism, Torch compilation, CUDA graph, etc.

## Installation

```bash
pip install git+https://github.com/GeeeekExplorer/nano-vllm.git
```

## Model Download

To download the model weights manually, use the following command:
```bash
huggingface-cli download --resume-download Qwen/Qwen3-0.6B \
  --local-dir ~/huggingface/Qwen3-0.6B/ \
  --local-dir-use-symlinks False
```

## Quick Start

See `example.py` for usage. The API mirrors vLLM's interface with minor differences in the `LLM.generate` method:
```python
from nanovllm import LLM, SamplingParams
llm = LLM("/YOUR/MODEL/PATH", enforce_eager=True, tensor_parallel_size=1)
sampling_params = SamplingParams(temperature=0.6, max_tokens=256)
prompts = ["Hello, Nano-vLLM."]
outputs = llm.generate(prompts, sampling_params)
outputs[0]["text"]
```

## Benchmark

See `bench.py` for benchmark.

**Test Configuration:**
- Hardware: RTX 4070 Laptop (8GB)
- Model: Qwen3-0.6B
- Total Requests: 256 sequences
- Input Length: Randomly sampled between 100–1024 tokens
- Output Length: Randomly sampled between 100–1024 tokens

**Performance Results:**
| Inference Engine | Output Tokens | Time (s) | Throughput (tokens/s) |
|----------------|-------------|----------|-----------------------|
| vLLM           | 133,966     | 98.37    | 1361.84               |
| Nano-vLLM      | 133,966     | 93.41    | 1434.13               |


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=GeeeekExplorer/nano-vllm&type=Date)](https://www.star-history.com/#GeeeekExplorer/nano-vllm&Date)