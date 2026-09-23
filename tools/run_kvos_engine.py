"""tools/run_kvos_engine.py —— 真引擎 KVOS 驱动器（K1-2 复核装备）。

[CLOUD] 需要 CUDA + nanovllm；Mac 跑不了。

干什么：把 LLMEngine 内部的 BlockManager 用 kvos.engine_plane.DualPlaneAllocator
包起来（猴子补丁，不改引擎文件），指定策略臂与 context 容量，跑一批 prompt，
报告钩子活动与 context plane 占用。

用途：r* ± 0.2 关键点用真引擎复核 TTFT/TBT（PROTOCOL：全网格在模拟器扫，
关键点真机复核）。策略名对 PROTOCOL §8 v1.2：A/B/C/D_online/D_oracle/E/F/G/H。

用法：
    HF_HOME=/root/autodl-tmp/hf python tools/run_kvos_engine.py \
        --model <模型目录> --arm A --context-blocks 4096 --requests 64
"""

import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--arm", default="A", help="A/B/C/D_online/D_oracle/E/F/G/H")
    ap.add_argument("--context-blocks", type=int, default=None,
                    help="context plane 容量上限（块数）；缺省=不设限（≈上游原行为）")
    ap.add_argument("--requests", type=int, default=64)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo_root)

    import random
    from nanovllm import LLM, SamplingParams
    from kvos import arms
    from kvos.engine_plane import DualPlaneAllocator

    llm = LLM(args.model, enforce_eager=True)
    # 引擎内部路径：LLMEngine -> scheduler -> block_manager（云机首验时核对属性名）
    eng = llm.llm_engine if hasattr(llm, "llm_engine") else llm.engine
    bm = getattr(getattr(eng, "scheduler", eng), "block_manager", None)
    if bm is None:
        raise RuntimeError("找不到 block_manager 属性路径，需要按云机实际结构改这行")

    policy = arms.make(args.arm, seed=args.seed)   # oracle 臂在真引擎无未来信息 → 仅对照
    alloc = DualPlaneAllocator(bm, policy, context_capacity=args.context_blocks)

    rng = random.Random(args.seed)
    prompts = ["Explain KV cache block %d in LLM serving." % i
               for i in range(args.requests)]
    sp = SamplingParams(temperature=0.0, max_tokens=32)
    llm.generate(prompts, sp, use_tqdm=False)

    print("arm=%s context_capacity=%s tick=%d" % (args.arm, args.context_blocks, alloc.tick))
    print("context resident=%d  meta tracked=%d  evicted=%d"
          % (len(alloc.context_ids), len(alloc.meta), len(alloc.evict_tick)))
    sat = getattr(policy, "saturation", lambda: None)()
    if sat is not None:
        print("CLOCK bit-saturation=%.3f" % sat)


if __name__ == "__main__":
    main()
