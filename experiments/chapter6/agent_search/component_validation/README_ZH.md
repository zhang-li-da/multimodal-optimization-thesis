# 第六章组件验证：E0—E2

本目录是从 S3 r3 `6a50c04` 独立分出的组件验证实现。E0 修复方向档案与投资池的接线；E2 使用 20/6/6 固定槽位，对 P-S 调度优先和 P-E 防淘汰做 2×2 归因。所有数据快照覆盖新区块 40—47，模型为本机 OpenCode 的 `minimax-cn-coding-plan / MiniMax-M3`。

## 当前状态

- E0：完成。5 个组件/fixture 测试通过；零额度方向不会占用活动池，试用不会因行为变化重置，四组固定槽位可回放。
- E2 v1：已冻结并启动，32/32 任务在首个 planner 请求因 HTTP 429 终止；无完整搜索、无 test 结果。
- E2 r2：保留 v1 失败后改为单进程的新冻结批次。32/32 仍在 provider failure 终止，少数任务完成 1—2 个提案后再被限流；没有完整搜索、没有 test 结果。
- E1：尚未启动真实延续搜索。按照协议，不能用 E2 的基础设施失败填充 E1，也不能把离线 fixture 当作真实模型证据。

两个 E2 失败批次的原始请求、状态、部分响应、成本和 manifest 均保留；分析报告明确把提供方失败与方法效果分开。当前没有可支持 P-S/P-E 质量效应的真实 test 数值。

## 复现

```powershell
python -m pytest -q experiments/chapter6/agent_search/component_validation
python -m experiments.chapter6.agent_search.component_validation.study_r2 verify --study experiments/chapter6/agent_search/component_validation/studies/e0-e2-minimax-20260927-r2-frozen
python -m experiments.chapter6.agent_search.component_validation.analyze --study experiments/chapter6/agent_search/component_validation/studies/e0-e2-minimax-20260927-r2-frozen --output experiments/chapter6/agent_search/component_validation/results/e2-r2
```

真实搜索必须使用冻结 manifest、通过 MiniMax M3 预检，并且只能在提供方解除限流后另开明确版本。失败请求不自动重试，不覆盖 v1/r2 归档。
