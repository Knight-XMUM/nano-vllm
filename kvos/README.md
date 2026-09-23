# kvos/ —— KVOS 回放模拟器（K0 基建）

把 KV cache 当 OS 内存管的实验台。**纯 Python 标准库**——不要 GPU、不要 torch，
Mac 上就能跑（这很重要：模拟器逻辑和引擎逻辑分开验，出 bug 知道赖谁）。

```bash
cd nano-vllm
python3 -m kvos.selftest    # K0 式门禁：应全部 PASS
```

## 模块地图（建议阅读顺序 = 数据流动顺序）

```
出题人           考卷格式            公寓楼账本            管理员按钮            八个考生
synth.py    →   trace.py      →   planes.py      ←    hooks.py      ←    arms/
                                        ↓                                     ↓
放映机            replayer.py  ←——— 驱动请求时钟，逐条记事件日志 ———— 按四钩子调策略
                                        ↓
阅卷组            analysis.py —— S(W)/Kneedle/bootstrap/refault 统计
                                        ↓
门禁             selftest.py —— K0-1/2 精神的模拟器侧版本
```

| 文件 | 干什么 | 对应协议/计划 |
|---|---|---|
| `trace.py` | canonical 事件 schema：加载/校验/落盘/oracle 派生 | PROTOCOL §4.1–4.2 |
| `planes.py` | 双平面块表：live（pin）/ context（可驱逐）分账 | PLAN §14 |
| `hooks.py` | 策略接口：on_allocate / on_access / on_evict / on_commit | PROTOCOL §8 |
| `arms/` | 八臂一臂一文件（A~H），看哪个臂就读哪个文件 | PROTOCOL §8 v1.2 |
| `replayer.py` | 回放循环：同一份 trace 原样喂给每个策略 | §4.4 / K0-2 |
| `analysis.py` | S(W) 曲线、Kneedle 拐点、bootstrap CI、删失统计 | §5–§6 |
| `synth.py` | 五维合成 trace 生成器（只用于诊断，不做结论） | §4.3 |
| `selftest.py` | 六条门禁：确定性/不变量/饱和探针/八臂/分析链/schema | K0-1、K0-2 精神 |

## 三个最重要的设计决定（读代码前记住）

1. **时钟是"第几个请求"**，不是墙钟不是 token 步（PROTOCOL §3）。think_time
   影响会话链更替，不影响 tick。
2. **首个 miss 之后整段后缀必 miss**——nano-vllm 是链式哈希（父块哈希链进子块），
   父块不在子块就永远查不到。A 臂驱逐中间块的代价就藏在这里。
3. **驱逐只发生在请求之间**。live plane 在请求处理期间 pin 住，evict 只在
   commit 后对 context plane 执法——K0-1 不变量由 replayer 内建 assert 锁死。

## 状态

- [x] 模拟器侧八臂 + 回放器 + 分析链 + 门禁（2026-09-23，selftest 全绿）
- [ ] 真 trace adapter（K0-2 正身，等 5 个公开 trace 源接入）
- [ ] 引擎侧双平面/四钩子接进 `nanovllm/engine/block_manager.py`（T5 之后）
- [ ] logprob 机制闸门 K0-3（需要真实引擎，云机做）
