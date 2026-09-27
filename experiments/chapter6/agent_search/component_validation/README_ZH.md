# 第六章组件验证：E0—E2

本目录是从 S3 r3 `6a50c04` 独立分出的组件验证实现。E0 修复方向档案与投资池的接线；E2 使用 20/6/6 固定槽位，对 P-S 调度优先和 P-E 防淘汰做 2×2 归因。所有数据快照覆盖新区块 40—47，模型为本机 OpenCode 的 `minimax-cn-coding-plan / MiniMax-M3`。

## 当前状态

- E0：完成。5 个组件/fixture 测试通过；零额度方向不会占用活动池，试用不会因行为变化重置，四组固定槽位可回放。
- E2 v1：已冻结并启动，32/32 任务因 provider failure 终止；原始归档含 44 个请求、12 个持久化响应、3 个已完成提案和 50,187 个已知 token。无完整搜索、无 test 结果。
- E2 r2：保留 v1 失败后改为单进程的新冻结批次。32/32 仍在 provider failure 终止；原始归档含 46 个请求、14 个持久化响应、6 个已完成提案和 56,483 个已知 token。没有完整搜索、没有 test 结果。
- E1：尚未启动真实延续搜索。按照协议，不能用 E2 的基础设施失败填充 E1，也不能把离线 fixture 当作真实模型证据。

## E1 离线准备入口

`e1.py` 和 `study_e1.py` 现在提供 E1 的离线准备链：从 S3 r3 的不可变
`checkpoint.json` 读取固定第 16 步历史，将其前缀程序重新评价在新区块
44--47 的 probe/validation 上，然后为每个新区块固定 `SP` 与 `FB_P` 两个
来源臂，共八个检查点。检查点选择在任何延续响应产生前完成：最低
validation 损失是 `C-I` 的起点；在质量容差内且行为距离超过 0.08 的最近
候选是 `C-B` 的起点；`C-E` 不提供父代。

```powershell
python -m experiments.chapter6.agent_search.component_validation.study_e1 prepare `
  --output experiments/chapter6/agent_search/component_validation/studies/e1-minimax-20260928-draft
```

该命令只读取归档和本地实例并写入 8 个检查点、48 个任务（192 个延续
提案），不会调用模型，也不会读取 test。`e1_runner.py` 的 fixture 入口
可验证三种延续的父代合同；真实搜索必须在独立冻结 manifest 和完整
planner/coder 服务验收后另行启动。离线准备本身不构成 E1 结果。

两个 E2 失败批次的原始请求、状态、部分响应、成本和 manifest 均保留；分析报告明确把提供方失败与方法效果分开。当前没有可支持 P-S/P-E 质量效应的真实 test 数值。

## 复现

```powershell
python -m pytest -q experiments/chapter6/agent_search/component_validation
python -m experiments.chapter6.agent_search.component_validation.study_r2 verify --study experiments/chapter6/agent_search/component_validation/studies/e0-e2-minimax-20260927-r2-frozen
python -m experiments.chapter6.agent_search.component_validation.analyze --study experiments/chapter6/agent_search/component_validation/studies/e0-e2-minimax-20260927-r2-frozen --output experiments/chapter6/agent_search/component_validation/results/e2-r2
```

真实搜索必须使用冻结 manifest、通过 MiniMax M3 预检，并且只能在提供方解除限流后另开明确版本。失败请求不自动重试，不覆盖 v1/r2 归档。
