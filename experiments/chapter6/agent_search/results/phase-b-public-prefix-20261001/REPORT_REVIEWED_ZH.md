# 阶段 B 公共前缀：暂停批次与完整审查

本批次实际调用 MiniMax-M3，完成 16 个提案后因第 17 个提案的 planner 请求留下 `sent_unknown` 而停止。只启动了冻结顺序中的区块 65，其余 7 个区块未启动；16 个计划检查点都有状态记录，只有 `b65-step08` 是可用搜索状态。**没有完成 8 区块实验，不进入续开发。** Test、续开发和 G/P/F 自然搜索均未启动。

本报告复核并更正首版 `REPORT_ZH.md` 的 seed 成本表述与 EG 信息口径，首版保留以记录修订。原始 ZIP 字节不变。

## 冻结与授权

分析基准为 `6d491ac`，执行源码为 `e40ce1023846b293327c94859aafc3f7a58bba97`。协议模板 `candidate_data_are_frozen: false` 是准备前状态，未改写模板；批次 manifest 以 `FROZEN_PENDING_EXECUTION` 绑定 8 份 probe/validation 快照、任务清单、源摘要、模型参数和执行顺序。快照共 384 个搜索实例（每块 12 probe + 36 validation），未物化新 Test。

- 批次 manifest：`29cc55e3443fa84c2156671c9b087585df6c6b224b81ba2d4cafa24421bf0550`。
- checkpoint manifest：`27c1503b2758c52ccc68ef9c72bad104f87f64a26c52301cdc365b9ab0886ea3`。
- 授权仅含 `public_prefix`，384 请求、2,000,000 tokens；每任务 24 提案、48 请求、250,000 tokens、3,600 秒，并发 1。
- 固定顺序为 65、63、66、60、67、62、64、61；temperature 0.7；planner/coder 最大输出 16,384/8,192。
- 复用已通过的 MiniMax-M3 工作负载验收，新增验收调用为 0。32 个正常响应返回模型全部为 MiniMax-M3，不可变服务版本未知。请求/响应保存可取得的模型、时间、请求标识、端点和 usage；成功请求的 sent 时间没有独立保留，不能宣称该字段齐全。

历史盘点及下载核验沿用 e40ce10 和用户本轮复核；未宣称重新验证整个仓库全部历史归档。旧源文件、协议、索引和 ZIP 未修改。

## 中断与真实成本

`016-planner` 在 UTC 2026-09-30 16:06:27 记录发送标记，16:09:28 任务终止并暂停分派。原始证据仅保存 `IndeterminateCall`、`unclassified infrastructure interruption`；不能归因为限流、额度耗尽或具体服务器错误。约 181 秒与 timeout 上限接近只是观测。

运行中的 `sent_unknown` 是发送前的保守标记，正常响应后更新为 `response_persisted`。早期进度播报误把进行中请求称作失败，现纠正：**只有最后一次请求真正未知，前 32 次全部正常返回；没有重试未知请求，亦未在真正未知终态后继续分派。**

| 资源 | 实际记录 | 说明 |
|---|---:|---|
| 完成提案 | 16 | 另 1 槽仅发 planner，结果缺失 |
| 有效生成 | 16/16 已完成提案 | 已尝试槽中已观察有效为 16/17；未知不能判成无效 |
| 请求 | 33/384（8.5938%） | 32 正常、1 未知 |
| tokens | 已知 127,470，总数未知 | 已知占 cap 6.3735%，不是最终使用率 |
| 未使用请求槽 | 351 | halt 后不可自动重启 |
| token 余额 | 精确值未知 | 1,872,530 只是 cap 减已知成本；未知请求预留 21,302 不是实测成本 |
| proposal 实例评价 | 768 | 16 × 48 |
| seed 实例评价 | 144 | **仅区块 65** 的 3 个 seed × 48 |
| 搜索实例评价合计 | 912 | 未启动区块没有 seed 评价 |
| 活动任务 wall | 1,253.407 秒 | 不含离线准备、索引校验和归档 |
| 评价器 wall / CPU | 8.169 / 8.078 秒 | 19 个 seed/候选的累计记录 |
| Test / 续开发请求 | 0 / 0 | 关闭 |

`ANALYSIS.json` 是冻结通用分析器原样输出，其中 `continuation_jobs:128` 是计划矩阵遍历数，`missing:128` 表示无文件，均不是已执行任务。实际分类见 `PREFIX_AUDIT.json`：计划 128、授权 0、执行 0。runner 的 `model_request_attempts:32` 只汇总完成提案；durable 日志含中断请求，共 **33** 次，成本以此为准。

## 全部区块与检查点

| 区块 | 完成提案 | 第 8 步 | 第 24 步 | 原因 |
|---:|---:|---|---|---|
| 60 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 61 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 62 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 63 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 64 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 65 | 16 | ready，分支可用 | 不可用 | 第 17 个提案中断，未到 24 步 |
| 66 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |
| 67 | 0 | 不可用 | 不可用 | 全局暂停，未启动 |

逐 checkpoint 成绩、程序差异、历史信息与冻结摘要见 `COVERAGE.csv`、`PREFIX_AUDIT.json`。15 个不可用状态均为 `preparation_incomplete`，不是“已观察到没有合格分支”。原 checkpoint manifest 在缺失状态默认 `branch_unavailable:false`，不能解释为分支可用；补充审查显式使用 `checkpoint_unavailable`。不得以 1/16 估计自然分支合格率。

唯一 ready 检查点只含 3 个 seed 和前 8 个提案，复核无后续信息泄漏：incumbent node 3 的 validation gap 为 **4.1559538%**；分支 node 5 为 **4.8432775%**，落后 **0.6873237 pp**，probe 行为距离 **0.1328976**。其 loss 差在 (0,0.035]，行为距离严格大于 0.08，按最小 loss、再 node id 选取。两程序源码/结构 SHA 均不同，AST 节点 42/52；主要差异为 regret、return-distance 权重和 cluster-density 项。它是可供诊断的候选，尚不证明方向优质或值得开发。所有已完成提案的父代均复核为当时 incumbent，SP 规则未漂移。

## EG 可获得的真实历史

`EG_HISTORY_PREVIEW_b65-step08.json` 在唯一可用检查点调用**冻结的本地摘要函数**，只是离线预览，没有传给模型，不是 EG 真实行动或反事实性能。来源为 node 0--10，共 11 个有效程序；包含 incumbent/best loss 0.0415595、median loss 0.0663290，以及前五程序的 intent、tags、validation loss。它们没有四字段策略假设，该字段为 null。

`observed_failures` 为空，因为原函数只记录无效生成，不记录有效但落后的尝试；`not_yet_observed_operator_tags` 也为空，因为所有预定义标签已出现。因此现有摘要能提供质量和前五程序信息，**不能声称提供了充分失败经验或未充分尝试的策略变化**。标签出现不等于方向覆盖。后续若改摘要，必须重新冻结 G 规格；不能把本预览当作 G 的正负性能结论。其它 15 个 checkpoint 无可用历史。离线预览模型调用为 0，未来 EG 的摘要输入 token 和维护成本仍须记账。

## 论文结论与停止决定

1. G/P/F 都没有本批独立增量证据，仍待验证，不能据此保留或删除。
2. 没有新 Test 或配对区块区间，不能判断优于强简单基线或达到 0.3 pp。
3. 已验证 SP 父代、分支资格、数据隔离与停止机制；行为/结构差异只是描述，不是收益因果解释。
4. 只观测 TSP14 窄 priority 接口上的 SP 短轨迹，不能推广预算、宽空间或其它载体。此次是服务完整性和覆盖失败，不是方法被证伪。
5. 可在第六章报告一个未经挑选状态的候选分支与中断保存事实；不可报告 H1--H4 支持、平均收益、协同、跨任务效果。
6. 批次 halt 后不重启、不补抽，不只对唯一 checkpoint 申请续开发；不进入 C/D/E。后续先解决服务可用性并明确执行安排及额度。方法保留 SP/合理固定多分支为基线，按剩余可检验主张逐组件筛查；证据不足时不扩大自然搜索或跨任务实验。

本轮运行前与归档修正后的相关门测试均为 34 passed，源哈希门通过。`audit_prefix.py` 另从原始请求/响应检查 usage、父代、前缀截断、分支选择及 Test 隔离，未调用评价器或模型。科学性修订约束见 `NEXT_STAGE_CORRECTIONS_ZH.md`，未追溯改变本批运行器。

## 可复核交付

`raw-study.zip` 保留首次原始字节，含 214 个成员（212 文件、2 目录），覆盖本批全部文件，包括 8 个任务终态、16 个 checkpoint、未知请求和历史锁文件；锁文件存在不代表仍有进程。`supporting-evidence.zip` 保存源码、协议、验收摘要、授权副本。两个 ZIP 均有成员哈希索引；`HASHES.json` 绑定交付文件。`DEMO_REAL.json` 只投影真实轨迹，没有合成收益。

在仓库根目录，用解压的原始 ZIP 重算并核验下载文件：

```powershell
python -m experiments.chapter6.agent_search.results.phase-b-public-prefix-20261001.audit_prefix --study <解压目录> --output <新输出目录>
python experiments/chapter6/agent_search/results/phase-b-public-prefix-20261001/verify_delivery.py --directory <下载目录>
```
