"""tools/bench_baseline.py —— vLLM 对照吞吐（bench.py 的对照组，收编进仓库）。

[CLOUD] 需要 CUDA + vllm；Mac 跑不了。

用法（云机，T4 卡）：
    HF_HOME=/root/autodl-tmp/hf python tools/bench_baseline.py --model <模型目录>
    # warmup 铁律：脚本内置首轮热身丢弃，计时取第二轮（E-001 教训：首跑含 JIT 预热）

口径：与 bench.py 同负载（seed=0, 256 条, prompt/gen ≤1024, temp=0.6,
ignore_eos）；--enforce-eager 与 E-001 基线对齐（默认开，公平 A/B）。
"""

import argparse
import os
import time
from random import randint, seed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("KVOS_MODEL",
                    "~/huggingface/Qwen2.5-0.5B-Instruct"))
    ap.add_argument("--num-seqs", type=int, default=256)
    ap.add_argument("--enforce-eager", action="store_true", default=True)
    ap.add_argument("--no-eager", dest="enforce_eager", action="store_false")
    args = ap.parse_args()

    from vllm import LLM, SamplingParams  # noqa: 延迟 import，Mac 上无 vllm

    seed(0)
    max_len = 1024
    path = os.path.expanduser(args.model)
    llm = LLM(model=path, enforce_eager=args.enforce_eager, max_model_len=4096)

    prompt_token_ids = [
        dict(prompt_token_ids=[randint(0, 10000) for _ in range(randint(100, max_len))])
        for _ in range(args.num_seqs)
    ]
    sampling_params = [
        SamplingParams(temperature=0.6, ignore_eos=True, max_tokens=randint(100, max_len))
        for _ in range(args.num_seqs)
    ]

    llm.generate([dict(prompt_token_ids=[0])],
                 [SamplingParams(temperature=0.0, max_tokens=8)], use_tqdm=False)  # warmup
    t = time.time()
    llm.generate(prompt_token_ids, sampling_params, use_tqdm=False)
    t = time.time() - t
    total = sum(sp.max_tokens for sp in sampling_params)
    print("vLLM | Total: %dtok, Time: %.2fs, Throughput: %.2ftok/s" % (total, t, total / t))


if __name__ == "__main__":
    main()
