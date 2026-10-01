# 阶段 B 公共前缀：增量审查、修订与续开发申请

本轮发现远端已有占用区块 60–67 的暂停批次，故没有新增模型调用、没有新建或恢复批次。已完成全部可做的离线修订、既有前缀审查、EG 输入接线核验、精确续开发申请和归档。本轮不是完成 8 个新前缀，也不能改变 e40ce10 实验的来源。

## 1. 版本和执行边界

- 研究分支：research/chapter6-minimal-mechanism-20260930。
- 用户审查基准：e40ce1023846b293327c94859aafc3f7a58bba97。
- 远端增量：be06a83 归档暂停前缀，bac0b9e 补充成本和覆盖审查；均未修改当时执行源码。
- 本轮执行安全/预算/统计修订：4d952a3；状态对齐及字节保存修订：e9195d7。
- 新源码清单摘要：cb54f9b8ea7c63d83e83c9f3e3e6ec5662843a819bebc61585d47a7e4cbd67ea。
- 结果标签：chapter6-phase-b-prefix-review-20261001。
- 旧批次 manifest：29cc55e3443fa84c2156671c9b087585df6c6b224b81ba2d4cafa24421bf0550。
- 旧检查点 manifest：27c1503b2758c52ccc68ef9c72bad104f87f64a26c52301cdc365b9ab0886ea3。

区块 60–67 已在阶段 A 之后被上述批次冻结。65 实际调用，其余七块虽未调用，仍属于冻结且暂停的矩阵。用户明确禁止复用冲突区块、补抽、替代批次及自动重跑，因此没有生成第二个运行 manifest、没有移用余额。USER_SCOPE.json 是用户范围的审计记录，不是可执行批准文件。原始授权在 supporting-evidence.zip 中保持原样。REVIEW_MANIFEST.json 只绑定审查产物，不冒充运行 manifest。

## 2. 必要修订与验收

C 的评价预算从错误分项纠正为 proposal 147,456、seed 6,912、合计 154,368；校验器逐项验收，不能仅靠合计正确过关。G/P/F 分别决定保留与删除，G 无增量不再否决 P/F 的研究。METHOD 明确项目追赶收益是续期辅助证据，不能直接与 incumbent 全局收益率比较；共同目标、成熟窗口及局部到全局预测的验证是 C 前设计事项，本轮不执行 F。D 保留收益区间下限达到 0.3 pp 的标准；功效需指定高于 0.3 pp 的预期效应，以其与门槛的差值规划。预期效应、方差、N 和联合功效规则仍待冻结。

执行修订包括：搜索入口拒收 Test；检查点函数在选择前截断 3 个 seed 加时间前缀；恢复的 wall deadline 扣除已保存时间；派发器遇到既有异常终态也重建 halt。durable 请求测试证明未知请求不重发，30 个已知 token 不会在重启后归零。

四组必要测试最终 **46 passed**，原始 JUnit 见 OFFLINE_TESTS.xml，源码冻结门通过。e40ce10 仅重新收集得到 68 项；旧报告 66 passed 是未同步文字，不能混作本轮实测数。未重复无关全仓测试，未执行 TSP Test 或新增数值评价。

## 3. 8 个任务与实际成本

| 区块 | 任务终态 | 完成提案 | 请求 | 已知 tokens | 未知请求 | 第 8 步 | 第 24 步 |
|---:|---|---:|---:|---:|---:|---|---|
|60|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|61|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|62|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|63|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|64|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|65|sent_unknown|16|33|127,470|1|ready，分支可用|preparation_incomplete|
|66|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|
|67|not_started|0|0|0|0|preparation_incomplete|preparation_incomplete|

完整任务 **0**；基础设施中断 **1**；预算终止 **0**；未启动 **7**。16 个完成提案均有效，已观察无效/截断为 0；另一个仅发 planner 的槽结果未知，不能判为无效。正常响应 32 个均返回 MiniMax-M3；1 个 sent_unknown 不重试、不按零成本。任务 wall 1,253.407 秒，搜索评价原记录 912 个实例（16×48 proposal + 3×48 seed，seed 只在 65 上评价）。本次审查未重跑数值评价。

已知成本低于本批 2,000,000 token 上限，但精确总用量及余额未知。21,302 是未知请求的保守预留而非实测用量，1,872,530 仅是 cap 减已知成本。请求前预留和已完成 usage 可从 CALL_AUDIT.json 检查；未解封 351 个余下请求槽。未知原因只记录为未分类基础设施中断，不能据此诊断为 429 或额度耗尽。

## 4. 检查点完整性与分支覆盖

16 个计划检查点均有状态和哈希，其中 **1 个 ready、15 个 preparation_incomplete**。唯一 ready 是 b65-step08，只使用 node 0–10（3 seed + 8 proposals），与保存的时间前缀逐字段一致；没有用第 9–16 步或 Test 修改选择。15 个未形成状态不是 branch_unavailable，也不能作自然分支失败率的分母。

| 检查点 | incumbent | 分支 | validation loss 差 | probe 距离 | 结构证据 |
|---|---|---|---:|---:|---|
|b65-step08|node 3，0.0415595383|node 5，0.0484327751|0.0068732368（0.6873237 pp）|0.1328976|源码和结构哈希不同，AST 节点 42/52；regret、return-distance 权重及 cluster-density 项不同|

分支满足有效、非同一程序、loss 差 (0,0.035]、probe 距离 >0.08，并在合格者中按 loss、固定 node id 选择。行为/结构差异只是操作性证据，不代表发现局部最优盆地或真实决策模式。COMMON_SEEDS.json 保存三个初始程序、名称和摘要，已与区块 65 实际 seed 核对；未启动区块没有 seed 评价。

COVERAGE_DETAIL.json 覆盖全部 16 个计划检查点，区分“截至要求步数的已观察提案”与“完整任务成本”，包含失败、截断、状态、结构哈希和 checkpoint 摘要，避免把第 8 步成绩配上第 24 步未来信息。

## 5. EG 历史输入审查

冻结摘要函数使用 11 个当时可见节点：质量统计覆盖全部有效节点，详细卡片仅包含按 validation 排序的 node **3、5、4、1、7**。五条卡片都有 intent、标签和质量；11 个策略假设字段全部缺失/null。observed_failures 为空，因为无无效程序，且现有函数不把有效但落后的尝试写成失败经验；unseen tags 也为空，不能解释为搜索方向已经覆盖。

本轮从该真实检查点调用实际 planner_prompt 构造器，保存未发送的 E0_PROMPT_b65-step08.txt、EG_PROMPT_b65-step08.txt。核对 EG 摘要确实进入 planner 请求文本，E0 没有这段历史；两组输出四字段 schema、模型、provider、priority(f) 接口相同。该接线核验不调用模型、不构成 EG 续开发或正负效果证据。

历史卡缺少完整失败经验与显式策略假设，问题如实保留。若续开发前改变 EG 摘要，应另冻提示/协议/源码版本并保留本前缀，不应回填或伪造历史假设。当前 prompt 预览和 SOURCE_SHA256 一同可复核。

## 6. 续开发申请（不启动）

CONTINUATION_REQUEST.json 保留原 128 行完整计划：全部 16 个检查点 × I/B/E0/EG × 两次重复。当前只有 b65-step08 的 **8 个任务具备状态输入**；其余 120 行为 checkpoint_unavailable，不能补抽。

| 待审批的可执行部分 | 精确上限 |
|---|---:|
|任务|8|
|每任务 proposal / 请求 / tokens / wall|8 / 16 / 100,000 / 900 秒|
|合计 proposal|64|
|合计请求|128|
|合计 tokens|800,000|
|串行任务 wall 合计|7,200 秒|
|搜索实例评价|3,072|
|第 4/8 步冻结候选记录|16|

这比原全矩阵 1,024 proposals/2,048 requests/12,800,000 tokens 小，不能借补抽恢复满矩阵。条件 B−I 比较只在 ready 且有合格 B 的状态内进行；全状态 B 策略仅在 ready 但无分支时引用对应 I 结果作预定回退，不额外发请求、不把 literal B 改名为成功；缺失检查点对所有策略仍缺失。当前仅一个区块，不能估计跨区块优势。

**尚不满足立即派发条件**：旧 halt 必须先有明确处置，新续开发代码/提示及预算必须另行冻结并获授权，服务能力不能仅由历史成功推定。此文件只准备申请，不创建 continuation approved 文件。是否修订 EG 提示需在任何续开发结果出现前决定。

## 7. 能与不能说明的结论

本轮可核实真实调用、有限状态/分支覆盖、成本与中断保存，以及后续请求的数据条件。公共前缀 validation 不是独立 Test，不能确认 EG>E0、B>I、P/F 或 H1–H3。没有设置事后改善/分支数量门槛；暂停来自冻结服务规则和明确区块冲突，不来自不利质量结果。

**续开发未运行，Test 未开放，C/D/E 未运行。** 本轮未启动任何模型子进程；交付前检查没有本研究遗留后台调用进程。锁文件只是原始证据，不能当作活跃进程。

## 8. 文件与复核

manifest.json、checkpoint_manifest.json 和 checkpoints/ 均是旧批次字节副本，raw-study.zip 与 supporting-evidence.zip 保持旧归档字节。新 review-sources.zip 保存修订后的源码、协议、清单和审查脚本。ARCHIVE_INDEX.json 列逐成员哈希，HASHES.json 绑定所有交付文件；REMOTE_REVIEW_FILES.json 同时列出应从固定提交读取的方法文件。verify_remote.py 下载本轮文件和三个 ZIP，逐项核验 manifest、数据/检查点哈希、请求/响应/候选覆盖、成本和封闭 Test；无需下载整个仓库。

原始归档 214 个成员（212 文件+2 目录），源支持归档 78 文件。成功请求的原始独立 sent 时间未保留；单个错误没有业务原因，验收摘要也不足以证明当前额度，这是审计限制，不进行猜测补全。

继续真实调用需要用户明确处理已经占用且 halt 的批次；当前指令没有授权解除这些限制。本轮先完整交付可复核资料，保留全部中断与缺失状态。
