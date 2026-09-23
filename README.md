# Chapter 6 v1.2 bounded branch-development screen

The latest v1.2 screen completed 18 paired model searches. It verified that competitive parent-relative improvements can enter a separate bounded development pool and receive real follow-up proposals. Relation-guided scheduling did not outperform the strong `niche + fixed development` baseline: mean test gap was 6.379% versus 5.569%. This is a small mechanism screen, not a confirmatory performance result. See the [technical report](experiments/chapter6/v12/results/screening-20260924-r2/TECHNICAL_REPORT_ZH.md), [architecture](docs/chapter6/V12_ARCHITECTURE.md), [v1.2 protocol](experiments/chapter6/v12/preregistration.md), [evidence package](experiments/chapter6/v12/results/screening-20260924-r2/README.md), and [version history](experiments/chapter6/v12/VERSION_HISTORY.md). The package preserves the invalid r1 batch separately for audit; its results are not pooled with r2.

# 多模态智能优化博士论文项目材料

本仓库汇集博士论文前五章对应文章材料、第六章 agent 原型与验证记录，以及总体技术报告，供跨设备查阅和复核。

## 从哪里开始

- [博士论文总体架构与第六章技术报告](docs/report/博士论文总体架构与第六章技术报告.md)：章节结构、方法架构、实验结果和当前缺陷。
- [开题章节草稿](docs/proposal/PROPOSAL_CHAPTER6.md)：第六章拟研究问题与开题表述。
- [技术路线](docs/chapter6/TECHNICAL_ROUTE.md)、[完整实验设计](docs/chapter6/EXPERIMENT_DESIGN.md)、[创新性审查](docs/chapter6/NOVELTY_AUDIT.md)、[冻结协议](docs/chapter6/PREREGISTRATION.md)。
- [v1.2 当前架构与证据边界](docs/chapter6/V12_ARCHITECTURE.md)：A/B 双档案、分支调度、实验结果与后续验收门槛。
- `papers/`：第三至第五章相关论文的原始材料压缩包，以及可直接阅读的主要 PDF。第一、二章目前是综述性基础章节，没有单独指定论文。
- `experiments/chapter6/demo/` 与 `experiments/chapter6/validation/`：便于浏览的代码和结果摘要。
- `experiments/chapter6/archives/`：完整 demo 和追加验证交付包，包含运行日志、提示/响应、候选程序、usage 和复核材料。

## 当前证据状态

原型已完成真实模型驱动的受限程序生成、执行、记忆更新和独立重放审核。v1.2 机制门槛通过：两模型合计发生 30 次分支后续评价、30 个有效子代；但关系引导未优于固定开发基线。因此目前支持有限分支开发执行链的工程可行性，不支持关系调度的稳定质量优势，也不构成博士创新性已确立的证据。v1.1 结果作为先前负结果独立保留；r1 v1.2 因基线实现错误作废，不计入分析。

## 论文对应关系

- 第三章：多模态多目标进化算法综述；联合空间 DWD 指标。
- 第四章：MSLS-MA（离散 MMTSP）；RMC-CMSA（连续多模态优化）。
- 第五章：HDADE（高维多模态多目标优化）。
- 第六章：受限程序空间上的执行反馈多模态搜索原型与待验证方法。MLEvolve 是启发与相关框架，不是本仓库实验中已完整复现的对照。

## 在另一台电脑获取

```powershell
git clone https://github.com/zhang-li-da/multimodal-optimization-thesis.git
cd multimodal-optimization-thesis
git switch --track origin/experiment/v1.2-bounded-branch-development
```

仓库为公开仓库，查阅论文和实验材料不需要 GitHub 登录。发布标签 `chapter6-v1.2-screening-20260924` 对应本轮报告与归档；模型 API 搜索需要各自的服务凭据，凭据不包含在仓库中。若只需下载文件，可使用 GitHub 页面上的 **Code → Download ZIP**。

## 注意事项

完整实验归档体积较大，但单个文件均适合 GitHub 普通仓库；仓库不包含 API 密钥或个人凭据。模型供应商账号本身不随仓库共享。论文材料按原始文件归档，书目信息和发表状态以正式出版记录为准。
