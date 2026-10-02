# 阶段 B 续开发执行报告

状态：`paused_infrastructure_incomplete`。本报告记录 r3、r4、r5 三个不可重试的恢复批次；它们使用同一冻结任务矩阵的不同源码修订，不能合并为一个完整策略比较。

## 执行范围

- 模型：`minimax-cn-coding-plan / MiniMax-M3`，本地 OpenCode 配置，串行调用。
- 计划矩阵：128 个任务（13 个可用检查点 × 4 行动 × 2 重复，以及 3 个不可用第 24 步检查点对应的 24 个 `preparation_incomplete` 任务）。
- 每个可执行任务：8 个提案、最多 16 次请求、100,000 tokens、900 秒 wall。
- Test 数据没有复制或读取；没有 Test 评价。
- 未知用量请求不按零成本处理，失败请求不重试。

## 结果

三个恢复批次共发出 **83 次请求**，其中 **80 个响应有可核对 usage**，已知 tokens **323,277**；另有 **3 个 sent_unknown/未知用量请求**。共完成 **40 个提案**，形成 **2 个完整 8 提案任务**和 **3 个基础设施中断任务**。其余计划任务未启动。

| 批次 | 终态任务 | 完成提案 | 请求 | 已知 tokens | 未知请求 | 停止原因 |
|---|---:|---:|---:|---:|---:|---|
| r3 | 1 个中断 | 4 | 9 | 45,194 | 1 | HTTP 529 provider overload；初始审计器故障已离线修复 |
| r4 | 1 完整、1 中断 | 14 | 29 | 106,636 | 1 | 单任务 transport timeout；审计文件 immutable 接线故障已修复 |
| r5 | 2 完整、1 中断 | 22 | 45 | 171,447 | 1 | HTTP 529 `overloaded_error`，触发全局暂停 |

### 科学解释

这批数据证明修订后的 runner 能够从冻结检查点生成真实 I/B/E0/EG 请求、保存 planner/coder 响应、评价 search 数据并在服务故障时停止。它没有形成四种行动的平衡样本，也没有独立 Test，因此不能证明 I、B、E0 或 EG 的质量优势，不能确认 G/P/F 或完整方法成立。

已完成任务中，EG 和 E0 各有完整 8 提案任务；另一个 E0 任务和两个 EG/E0 前缀任务因服务基础设施中断。由于任务顺序是冻结的，不能补抽或根据结果挑选检查点。

## 失败审计

- r3：HTTP 529，`business_code=overloaded_error`，未知用量，自动重试关闭。
- r4：transport timeout，未知用量，任务 wall/deadline 约束有效，自动重试关闭。
- r5：HTTP 529，`business_code=overloaded_error`，未知用量；全局 halt 写入后未继续派发。
- 所有未派发任务保留 `not_started`，不计入终态完成；`CONTINUATION_AUDIT.json` 的 `all_tasks_accounted` 为 `false`。

## 后续门槛

本轮不开放 Test，不生成候选冻结批准文件，不宣布方法有效。若要继续，必须在服务可用性获得独立验收后，以新的、显式授权的恢复批次仅派发尚未启动任务；不得重试上述 3 个未知请求。正式比较仍需完成所有行动和重复的终态审查，再执行 Test gate。
