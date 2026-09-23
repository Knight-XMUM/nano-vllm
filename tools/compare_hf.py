"""tools/compare_hf.py —— L2 正确性门禁：nano-vllm vs HF transformers 逐 token 对拍。

[CLOUD] 需要 CUDA + transformers + 本仓库 nanovllm；Mac 上跑不了（无 GPU/无 transformers）。

用法（云机）：
    cd /root/proj/nano-vllm
    HF_HOME=/root/autodl-tmp/hf python tools/compare_hf.py \
        --model /root/autodl-tmp/hf/hub/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae557604adf67be50417f59c2c2f167def9a775

口径（E-002 验收标准，铁律 3）：
  两侧均严格贪心（nano: temperature=0 走 argmax 分支；HF: do_sample=False），
  各生成 n 个 token 逐位比对，打印 agreement x/n 与首个分叉位置。
  门禁：一致率 >= 90%（铁律 3 的 128-token 口径；HF 自身数值路径差异已知会造成
  ~token 8 起的小分叉，E-001 记录，属已知偏差不是回归）。
"""

import argparse


def hf_generate(model_path: str, prompt: str, n: int):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(
        model_path, torch_dtype=torch.bfloat16, device_map="cuda"
    )
    ids = tok(prompt, return_tensors="pt").input_ids.cuda()
    out = model.generate(
        ids, do_sample=False, max_new_tokens=n,
        pad_token_id=tok.eos_token_id,
    )
    return tok, out[0][ids.shape[1]:].tolist()


def nano_generate(model_path: str, prompt: str, n: int):
    from nanovllm import LLM, SamplingParams

    llm = LLM(model_path, enforce_eager=True)
    out = llm.generate(
        [prompt], SamplingParams(temperature=0.0, max_tokens=n), use_tqdm=False
    )
    return out[0]["token_ids"], out[0]["text"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="HF 模型目录（含 snapshots 全路径）")
    ap.add_argument("--prompt", default="Give me a short introduction to large language model serving.")
    ap.add_argument("--n", type=int, default=128, help="比对 token 数（铁律 3 默认 128）")
    args = ap.parse_args()

    tok, hf_ids = hf_generate(args.model, args.prompt, args.n)
    nano_ids, nano_text = nano_generate(args.model, args.prompt, args.n)

    m = min(len(hf_ids), len(nano_ids))
    agree = sum(1 for a, b in zip(hf_ids[:m], nano_ids[:m]) if a == b)
    first_diff = next((i for i in range(m) if hf_ids[i] != nano_ids[i]), None)

    print("HF  :", tok.decode(hf_ids))
    print("nano:", nano_text)
    print("token agreement: %d/%d, first divergence: %s" % (agree, m, first_diff))


if __name__ == "__main__":
    main()
