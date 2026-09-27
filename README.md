# 多模态智能优化博士论文项目材料

本仓库汇集博士论文前五章对应文章材料、第六章 agent 原型与验证记录，以及总体技术报告，供跨设备查阅和复核。

## 最新完成：S3 方向试用与六组真实对照（2026-09-27）

已完成 **MiniMax M3 × 六组 × 八个新区块 = 48 次真实搜索**，每次 32 提案；全部 48 次独立测试完成。1,527/1,536 个生成程序有效（99.414%）。探索方向可以获有限资格，落后父代得到 366 次保护机会、362 次有效评价；但三个主要比较均未达到冻结筛查标准。

| 组 | 平均 TSP14 Test gap |
|---|---:|
| SP 单路径开发 | **5.0969%** |
| WR 广泛探索 | 5.9095% |
| FB_U 固定分配、无保障 | 5.5840% |
| FB_P 固定分配、有保障 | 5.7748% |
| TS_P 时间调度、有保障 | 5.3643% |
| AD_P 反馈调度、有保障 | 5.8486% |

保护 FB_P−FB_U 为 +0.1909 个百分点，AD_P−FB_P 为 +0.0738；TS_P−FB_P 为 −0.4105，但区间跨零。执行链和可复核性已建立，完整方法优势尚未成立。无需重新调用模型即可查看本轮结果和演示。

- [完整技术报告、全部区块差值与缺陷](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/TECHNICAL_REPORT_ZH.md)
- [已实现架构、公式和前序章节文章映射](docs/chapter6/S3_ARCHITECTURE_ZH.md)、[S3 之后的开题章节修订](docs/proposal/PROPOSAL_CHAPTER6_S3.md)
- [全部 48 次真实轨迹离线演示](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/demo/index.html)、[下载/解压/复核与讲解脚本](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/REPRODUCE_ZH.md)
- [原始证据九个分包](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/RAW_PARTS.json)、[48 次搜索完整重执行](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/EXECUTION_AUDIT.json)、[独立测试重执行](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/TEST_AUDIT.json)
- [50/100 城市迁移评价：冻结程序、无新模型调用](experiments/chapter6/agent_search/studies/s3-transfer-20260927/TRANSFER_ANALYSIS.json)。参考是启发式上界，不是最优值；结果未建立稳定保护优势，不属于跨任务确认。
- [r1/r2 工程失败说明](experiments/chapter6/agent_search/s3_tsp/results/S3_ABORTED_BATCHES_ZH.md)与[分批成本台账](experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/COST_LEDGER.json)。旧失败不混入 r3 效果统计，原始文件和历史标签保留。

本轮源码标签 chapter6-s3-tsp-r3-source-20260927（51e5d5e），协议标签 chapter6-s3-tsp-r3-frozen-20260927（62cf707）；规模迁移另于 343b13f 冻结。S3 r3 使用 3,069 个搜索请求、11,037,795 token，模型搜索已经结束。结果使用独立标签，见本目录发布记录。

## 历史：S0 输出校准与 S1 真实竞争验证（2026-09-26）

本轮已调用本机 OpenCode 的 **MiniMax-M3 coding plan**，不是只更新文档。S0 r1 工程中止1请求；r2完成84请求但planner验收14/18不通过；另行冻结的r3完成48请求，planner/coder均18/18、端到端6/6，达到进入S1的工程门槛。两次校准不合并。

S1已完成**两控制器×三个新区块×两搜索种子=12次，每次32提案**，使用同提案上限、串行 MiniMax M3 调用；12/12 搜索和12/12 独立 test 读出均已归档。本批只诊断自然 B 竞争机会，不是 SP/WR/FB/TS/AD 五策略效果确认。

- [S0 r2负结果与完整成本](experiments/chapter6/s0_output_calibration/results/s0-minimax-output-calibration-20260926-r2/REPORT_ZH.md)
- [S0 r3独立验收与原始结果](experiments/chapter6/agent_search/results/s0-minimax-output-acceptance-20260926-r3/REPORT_ZH.md)
- [S1冻结协议](experiments/chapter6/agent_search/s1_observability/protocol.final.json)、[执行限制披露](experiments/chapter6/agent_search/s1_observability/EXECUTION_DEVIATIONS_ZH.md)
- [S1完整技术报告](experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/report/REPORT_ZH.md)、[离线回放](experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/demo/index.html)、[原始归档](experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/raw-study.zip)
- [S1复现说明](experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/REPRODUCE.md)、[发布元数据](experiments/chapter6/agent_search/results/s1-minimax-observability-20260926-r1/PUBLICATION.json)
- [五策略纯状态机与较大TSP工程准备，均非真实策略效果结果](experiments/chapter6/agent_search/ENGINEERING_PREPARATION_ZH.md)
- [PR #5](https://github.com/zhang-li-da/multimodal-optimization-thesis/pull/5)：版本分支 `experiment/chapter6-agent-search-s0-20260926`，不自动合并旧分支。

本批共完成 764 次 S1 模型请求（3,515,905 个已知 tokens）；FIFO 平均 Test gap 为 4.613%，组合排序为 5.017%，R−FIFO 为 +0.4039 个百分点，六个配对为 3 胜、3 负。多分支和评分差异实际出现，但该质量差异不支持组合排序优势。研究设计/历史版本中的“本次无模型调用”只指其原始提交，不描述本批真实运行。最终方法优势与博士章节收口仍待公平策略效果和机制归因证据。

## 研究设计来源：智能体探索—开发协同（2026-09-26）

第六章的主体更新为**智能体在广泛外层决策空间中的方向探索、局部开发保护与反馈资源分配**；TSP、机器学习工程和数学优化是验证载体。最终可只输出一个最佳方案，算法集合部署或集成不再是章节成立的必要条件。此为待实施设计，不是新实验结果。

- [研究定位与设计修订总入口](docs/chapter6/reviews/agent-search-20260926/README.md)
- [方向定义、拟议架构与可证伪假设](docs/chapter6/reviews/agent-search-20260926/RESEARCH_DESIGN.md)
- [逐阶段实验、预算及所需证据](docs/chapter6/reviews/agent-search-20260926/EXPERIMENT_PLAN.md)
- [MLEvolve / MLE-Bench 实验与源码对齐核查](docs/chapter6/reviews/agent-search-20260926/BENCHMARK_ALIGNMENT.md)
- [新版开题研究内容](docs/proposal/PROPOSAL_CHAPTER6_AGENT_SEARCH.md)、[冻结与结果验收](docs/chapter6/reviews/agent-search-20260926/ACCEPTANCE.md)

新增单路径、广泛重启、固定多方向、时间调度与反馈调度比较，并分别设计保护消融、TSP 规模/模块空间扩展及跨任务确认。已有 v1.2.3 结果与 demo 仍按原版本解释；本次没有新增模型调用。

## 历史版本：v1.2.3 MiniMax M3 真实筛查与开题回放

已完成 **MiniMax M3 × FIFO/完整排序 × 8 个新区块＝16 次真实搜索**，每次 8 个提案；16 次独立测试在全部搜索结束后执行。原双模型草案保留，本轮仅提供单模型证据。FIFO 平均 Test gap **5.9833%**，完整排序 **6.1019%**，R−F 为 **+0.1186 个百分点**；3 胜、2 平、3 负，未建立完整排序优势。

本轮关键发现是 **103/128 个提案解析失败，18 次 B 续开发都没有多分支可选，128 次同历史 F/R 决策和提示完全一致**。因此，质量差值不能归因于已观察到的排序动作。下一步先解决任务形态输出在 1800/2000-token 限制下的完整性，并在新协议中检验多分支可观测性；其后按上述新设计开展探索—开发策略与机制归因。集合用途保留为可选拓展。

- [完整技术报告、全部配对与缺陷](experiments/chapter6/v12_3/results/minimax-20260926-r1/REPORT_ZH.md)
- [v1.2.3 已实现架构和方法边界](docs/chapter6/V123_ARCHITECTURE.md)、[对应历史开题稿](docs/proposal/PROPOSAL_CHAPTER6_V123.md)；当前研究定位见上方新版设计
- [离线 HTML 回放](experiments/chapter6/v12_3/results/minimax-20260926-r1/demo/index.html)：下载后直接打开，包含 18 次历史、16 次新版和合成机制用例
- [4 分 20 秒字幕演示 MP4](experiments/chapter6/v12_3/results/minimax-20260926-r1/demo-evidence/video/chapter6_demo_4m20s.mp4)、[演示讲解指南](docs/chapter6/V123_DEMO_GUIDE.md)
- [原始请求/响应、程序与评价 ZIP](experiments/chapter6/v12_3/results/minimax-20260926-r1/raw-study.zip)、[离线复核命令](experiments/chapter6/v12_3/README.md)

冻结标签为 chapter6-v1.2.3-minimax-protocol-20260926-r1；结果另用 chapter6-v1.2.3-minimax-screening-20260926-r1 标签。全程保留旧结果、旧源码与独立评审；本批次没有改参数补跑。模型调用已结束，回放与复核不需要 API。

## 历史版本：v1.2.2 启动准备与离线集成验证

依据 PR #1 的复核意见补齐了独立搜索入口、请求与状态恢复、测试集隔离，以及 ZIP/AST 哈希兼容。控制器仍使用 v1.2.1 的两臂规则；本轮新增模型调用 0，16 次真实搜索仍未冻结、未执行。阅读[启动层架构](docs/chapter6/V122_READINESS.md)、[代码与复现命令](experiments/chapter6/v12_2/README.md)及[本轮技术报告](experiments/chapter6/v12_2/results/readiness-20260925/REPORT_ZH.md)。新分支为 `experiment/v1.2.2-runner-readiness`。

## v1.2.1 离线机制修正

本轮没有新增模型调用。两开发组已统一普通 niche 调度，W 不参与决策，只有 B 内选择规则不同；FIFO、尝试深度和成功深度已明确区分。旧 r2 的18次模型搜索保持归档；新版本只完成本地测试、旧日志回放与数值复核，尚不能证明关系排序提高性能。

阅读[本轮中文报告](experiments/chapter6/v12_1/results/mechanism-only-20260924/REPORT_ZH.md)、[复核记录](docs/chapter6/reviews/V12_R2_INDEPENDENT_REVIEW.md)、[机制代码与复现命令](experiments/chapter6/v12_1/README.md)。[16次后续搜索协议](experiments/chapter6/v12_1/preregistration.md)仍是草案，未执行。新分支为 `experiment/v1.2.1-isolated-branch-policy`，最新审计修订标签为 `chapter6-v1.2.1-mechanism-only-20260924-r1`；早期机制标签保留不改写。

新增 [v1.2.1 独立审阅](docs/chapter6/reviews/v121/REVIEW.md)与[详细后续实验流程](docs/chapter6/reviews/v121/EXPERIMENT_PLAN.md)：包含 Linux 复核、ZIP/AST 哈希兼容诊断、在线启动验收、16 次搜索矩阵，以及后续族证据归因和算法集合用途实验。本次仍为 0 新模型调用；任务矩阵是设计草案，不是已执行或冻结的搜索。

## 从哪里开始

- [博士论文总体架构与第六章技术报告](docs/report/博士论文总体架构与第六章技术报告.md)：章节结构、方法架构、实验结果和当前缺陷。
- [当前开题研究内容](docs/proposal/PROPOSAL_CHAPTER6_AGENT_SEARCH.md)；[早期开题草稿](docs/proposal/PROPOSAL_CHAPTER6.md)作为历史材料保留。
- [技术路线](docs/chapter6/TECHNICAL_ROUTE.md)、[完整实验设计](docs/chapter6/EXPERIMENT_DESIGN.md)、[创新性审查](docs/chapter6/NOVELTY_AUDIT.md)、[冻结协议](docs/chapter6/PREREGISTRATION.md)。
- [v1.2 当前架构与证据边界](docs/chapter6/V12_ARCHITECTURE.md)：A/B 双档案、分支调度、实验结果与后续验收门槛。
- `papers/`：第三至第五章相关论文的原始材料压缩包，以及可直接阅读的主要 PDF。第一、二章目前是综述性基础章节，没有单独指定论文。
- `experiments/chapter6/demo/` 与 `experiments/chapter6/validation/`：便于浏览的代码和结果摘要。
- `experiments/chapter6/archives/`：完整 demo 和追加验证交付包，包含运行日志、提示/响应、候选程序、usage 和复核材料。

## 历史证据状态（v1.2 r2；本轮见上）

原型已完成真实模型驱动的受限程序生成、执行、记忆更新和独立重放审核。v1.2 机制门槛通过：两模型合计发生 30 次分支后续评价、30 个有效子代；但关系控制器整体未优于固定开发基线，且普通调度及 W 引用存在混杂，未隔离关系排序效果。因此目前支持有限分支开发执行链的工程可行性，不支持关系调度的稳定质量优势，也不构成博士创新性已确立的证据。v1.1 结果作为先前负结果独立保留；r1 v1.2 因基线实现错误作废，不计入分析。

## 论文对应关系

- 第三章：多模态多目标进化算法综述；联合空间 DWD 指标。
- 第四章：MSLS-MA（离散 MMTSP）；RMC-CMSA（连续多模态优化）。
- 第五章：HDADE（高维多模态多目标优化）。
- 第六章：智能体外层决策空间中的多模态搜索与探索—开发协同；现有受限程序搜索是机制原型。MLEvolve 是启发与待接入的对照，尚未在本仓库完成其端到端复现。

## 在另一台电脑获取

```powershell
git clone https://github.com/zhang-li-da/multimodal-optimization-thesis.git
cd multimodal-optimization-thesis
git switch --track origin/experiment/chapter6-agent-search-s3-tsp-20260927
```

仓库为公开仓库，查阅论文和实验材料不需要 GitHub 登录。旧标签 `chapter6-v1.2-screening-20260924` 保留原始 r2 发布；最新离线审计修订使用 `chapter6-v1.2.1-mechanism-only-20260924-r1`，早期机制标签不改写；模型 API 搜索需要各自的服务凭据，凭据不包含在仓库中。若只需下载文件，可使用 GitHub 页面上的 **Code → Download ZIP**。

## 注意事项

完整实验归档体积较大，但单个文件均适合 GitHub 普通仓库；仓库不包含 API 密钥或个人凭据。模型供应商账号本身不随仓库共享。论文材料按原始文件归档，书目信息和发表状态以正式出版记录为准。
