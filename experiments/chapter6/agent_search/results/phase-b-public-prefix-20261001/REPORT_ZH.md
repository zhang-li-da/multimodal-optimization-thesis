# 阶段 B 公共前缀执行与覆盖审查

本批次只执行阶段 B 的 SP 公共前缀，不执行续开发，也不开放 Test。冻结批次绑定提交 `e40ce10`、MiniMax-M3、provider `minimax-cn-coding-plan`、8 个新区块（60--67）、每块最多 24 个 proposal、串行并发 1。冻结 manifest SHA-256 为 `29cc55e3443fa84c2156671c9b087585df6c6b224b81ba2d4cafa24421bf0550`。授权只覆盖 `public_prefix`：最多 384 次请求和 2,000,000 tokens。

## 执行结果

区块按冻结顺序从 65 开始。该任务完成了 16 个 proposal；第 17 个 proposal 的 planner 请求为 `sent_unknown`。由于该请求的发送状态和成本无法确定，运行器按预定义服务完整性规则暂停全批，未重试，也未启动其余 7 个区块。已发起请求 33 次，其中 32 次有完整 usage，共计已知 127,470 tokens；第 33 次成本未知。已知 proposal evaluator 评价 768 个实例，另有 8 个区块共享 seed 的 144 个本地评价，共 912 个搜索期评价。无 Test 评价、无续开发请求。

| 区块 | 公共前缀状态 | 第 8 步 | 第 24 步 | 停止/缺失原因 |
|---:|---|---|---|---|
| 60 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 61 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 62 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 63 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 64 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 65 | `sent_unknown` | `ready` | `preparation_incomplete` | 第 17 个 proposal 的 planner 请求为 `sent_unknown` |
| 66 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |
| 67 | `not_started` | `preparation_incomplete` | `preparation_incomplete` | 全局暂停前未启动 |

`b65-step08` 是唯一可用检查点。validation incumbent 为 node 3，loss `0.0415595383`。按冻结规则，node 5 是合格分支（validation loss `0.0484327751`）：相对 incumbent 差 `0.0068732368`，即 `+0.6873` 个百分点；probe 行为距离 `0.1328976`，超过严格阈值 `0.08`；源码 SHA-256 和结构 SHA-256 均不同。因此该检查点的 B 分支可用，但仍不能据此推断后续开发收益。

`b65-step24` 没有完成冻结所需的第 24 步，不能选择分支。其余 15 个检查点没有可观测的 incumbent 或候选分支成绩，按协议保留缺失状态，不补抽区块、不换检查点。

## EG 历史信息边界

本批次授权的策略只有 SP。EG 没有生成请求、没有维护历史摘要、没有产生候选，因此 EG 实际获得的历史信息为“无”，其摘要调用、输入 token 和维护成本均为 0。该结果不能用于 EG 与 E0 的质量比较，也不能说明历史信息生成有效或无效。

## 门后结论

完整性审查通过了“所有 8 个区块、16 个检查点均有记录、未知请求未被重试、缺失状态未填零、Test 保持关闭”的数据保存要求，但没有通过“16 个检查点均完成公共前缀”的研究覆盖要求。当前证据只支持：SP 在 block 65 的第 8 步产生了一个满足预注册分支资格的候选；它不支持新方向质量、开发期限、机会成本或 G/P/F 任何组件的比较结论。

因此本批次不申请续开发额度，不运行 `continue-all`、`release-test` 或 `test-all`。应先归档本批次并报告 provider 完整性问题；不得把未启动的 7 个区块写成已完成，也不得用本批次选择阶段 C 方法。

原始记录在 `raw-study.zip`，分析器输出在 `ANALYSIS.json`，16 个检查点及状态摘要在 `CHECKPOINT_MANIFEST.json`。这些文件均绑定上述冻结 manifest；历史结果目录未修改。
