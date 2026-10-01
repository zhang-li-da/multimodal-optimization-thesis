# 阶段 B 本地 OpenCode MiniMax-M3 公共前缀重跑

本批次是独立于历史 halted study 的 OpenCode 重跑，使用 `minimax-cn-coding-plan/MiniMax-M3`、串行请求、无自动重试和 search-only 数据。正式 3+3 planner/coder 服务验收通过。

## 结果

区块 65 完成 2 个有效 proposal：4 个 planner/coder 请求均返回 `MiniMax-M3`，已知用量 **15,152 tokens**。第 3 个 planner 请求在 180 秒工作负载后进入 `sent_unknown`，没有 usage 或业务错误码；该成本保留为未知，不按零处理。全局暂停后其余 7 个区块未启动。

验证集 incumbent loss 从 `0.05861685599` 降到 `0.04641643148`，但只有一个区块和两个 proposal，不能支持策略效果、分支开发效果或任何 H1-H3 结论。16 个计划检查点全部 `preparation_incomplete`，没有可用于续开发的 ready checkpoint；Test 未读取、未物化。

## 处理

历史区块 60--67 的 halted study 未修改；历史 `sent_unknown` 请求未重试。本批次在新的 manifest 下运行，发现未知服务中断后停止派发。归档包括所有请求、响应、状态、候选、checkpoint、成本和本地服务验收摘要。

这份结果证明本地 OpenCode 调用曾成功完成 MiniMax-M3 的正式短验收和两个搜索 proposal；它不证明完整实验完成，也不证明当前方法有效。后续若恢复实验，必须先决定如何处理本批未知请求，并冻结新的超时/恢复协议，不能把未知请求直接当作失败后重发。
