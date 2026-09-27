# S3 r1/r2 工程失效与原始记录更正

日期：2026-09-27。本文补充两批已停止实验的中文说明；原 ZIP、源提交和成员哈希均保持不变，不把工程失败并入 r3 方法效果数据。

## r1：实现与对照语义审计后中止

完成三个搜索任务、96 个提案、192 个搜索请求，预检另 1 请求。审计发现普通 AD 两提案单元混入插入保护步骤的奖励/成本，以及未保护组分支池使用、提示私有证据等对照问题。因此在 test 之前停止；45 个未开始任务保留。源码 5e79f31，冻结 76a7fb6。

已知搜索 token 为 689,340，预检 211；全部已知。原始证据见 [r1 归档](s3-r1-stopped-before-test-20260927/raw-study.zip)。

## r2：线程共享全局评价器导致数据隔离失效

线程池同时使用旧 snapshot_evaluator 的全局 patch.object(benchmarks, "instances", ...)；不同数据块在 probe 阶段相互覆盖。四个任务各五个程序的离线重执行证实 probe 行为不一致，不是 1e−16 量级数值问题。这个错误由运行器的并行接法引入，不能解释为 MiniMax 模型问题。

14 个任务完成；4 个为 infrastructure_incomplete（包括写文件失败与审计停止）；30 个未开始。共持久化 534 个提案，1,074 个搜索请求记录，其中 1,073 个 raw response 可核验，已知 token 3,798,870；一请求无持久化响应，用量未知，不自动重发。预检另 1 请求、210 token。全部在 test 前停止，整批仅作工程诊断。

原 r2 REPORT_ZH.md 前六行中文在写入时被替换成问号，不能正确说明原因；本页恢复说明，保留旧文本及原 ZIP，避免改变已发布证据哈希。参见 [原始归档](s3-r2-thread-isolation-failed-20260927/raw-study.zip)与[线程隔离诊断](../studies/s3-tsp-minimax-strategy-screen-20260927-r2/thread-isolation-audit.json)。

## r3 的独立处理

r3 使用新数据块 32–39、显式快照评价器和独立进程；已完成 48 次搜索及全部测试。没有复用 r1/r2 的质量读出，没有把未执行任务当成零成本成功。完整台账将 r1/r2/r3 分开，参见 [r3 技术报告](../../s3_tsp_r3/results/s3-minimax-strategy-20260927/TECHNICAL_REPORT_ZH.md)。
