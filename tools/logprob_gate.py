"""tools/logprob_gate.py —— K0-3 机制闸门：机制开关前后 logprob 对拍。

[CLOUD] 需要 CUDA + torch + nanovllm；Mac 跑不了。

原理：机制改动（COW/offload/驱逐回收）最隐蔽的 bug 是数值漂移——token 序列
没变但 logprob 悄悄变了。闸门 = 同一批 prompt 在 机制关/机制开 两态各跑一遍，
逐 token 比对 chosen-token logprob：max|Δ| <= 1e-4 且 0 个 token 分叉才放行
（容差与口径见 PROTOCOL K0-3，不得另造）。

两阶段用法：
  # 阶段一：各态分别采样（机制开关经环境变量传入引擎侧实现，本脚本只管记录）
  KVOS_LOGPROB_DUMP=/tmp/base.jsonl  python tools/logprob_gate.py --dump --model <path>
  KVOS_LOGPROB_DUMP=/tmp/mech.jsonl  KVOS_MECHANISM=1 python tools/logprob_gate.py --dump --model <path>
  # 阶段二：对拍
  python tools/logprob_gate.py --check /tmp/base.jsonl /tmp/mech.jsonl

实现：monkeypatch 包一层 Sampler.forward——不改引擎文件，开/关态由外层决定。
"""

import argparse
import json
import os
import sys

TOL = 1e-4


def dump(model_path: str, out_path: str, n_prompts: int = 8, max_tokens: int = 64):
    import torch
    from nanovllm import LLM, SamplingParams
    from nanovllm.layers import sampler as sampler_mod

    rec = open(out_path, "w")

    orig_forward = sampler_mod.Sampler.forward

    def wrapped(self, logits, temperatures):
        ids = orig_forward(self, logits, temperatures)
        lp = torch.log_softmax(logits.float(), dim=-1)
        chosen = lp.gather(-1, ids.unsqueeze(-1)).squeeze(-1).tolist()
        rec.write(json.dumps({"token_ids": ids.tolist(), "logprobs": chosen}) + "\n")
        rec.flush()
        return ids

    sampler_mod.Sampler.forward = wrapped
    try:
        llm = LLM(model_path, enforce_eager=True)
        prompts = ["KVOS gate probe %d: explain caching." % i for i in range(n_prompts)]
        llm.generate(prompts,
                     SamplingParams(temperature=0.0, max_tokens=max_tokens),
                     use_tqdm=False)
    finally:
        sampler_mod.Sampler.forward = orig_forward
        rec.close()
    print("dumped -> %s" % out_path)


def check(path_a: str, path_b: str) -> int:
    la = [json.loads(x) for x in open(path_a)]
    lb = [json.loads(x) for x in open(path_b)]
    if len(la) != len(lb):
        print("FAIL: 行数不等 %d vs %d（采样步数不一致）" % (len(la), len(lb)))
        return 1
    max_diff, diverged = 0.0, 0
    for ra, rb in zip(la, lb):
        for ta, tb, pa, pb in zip(ra["token_ids"], rb["token_ids"],
                                  ra["logprobs"], rb["logprobs"]):
            if ta != tb:
                diverged += 1
            else:
                max_diff = max(max_diff, abs(pa - pb))
    ok = diverged == 0 and max_diff <= TOL
    print("logprob gate: max|Δ|=%.2e (tol %.0e), token 分叉=%d -> %s"
          % (max_diff, TOL, diverged, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true", help="采样并写 dump（需 --model/--out）")
    ap.add_argument("--check", nargs=2, metavar=("A", "B"), help="对拍两个 dump 文件")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=os.environ.get("KVOS_LOGPROB_DUMP", "/tmp/lp.jsonl"))
    args = ap.parse_args()
    if args.check:
        sys.exit(check(*args.check))
    if args.dump:
        if not args.model:
            ap.error("--dump 需要 --model")
        dump(args.model, args.out)
        return
    ap.error("需要 --dump 或 --check")


if __name__ == "__main__":
    main()
