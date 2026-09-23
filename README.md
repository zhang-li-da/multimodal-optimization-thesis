# Chapter 6 v1.1 screening results

The preregistered 200-run study is complete. Its screening evidence does not support a stable test-quality advantage for the full revised controller: it beat the niche baseline in 0/8 model-task-budget cells. Read the [Chinese technical report](experiments/chapter6/v11/results/screening-20260923-r1/TECHNICAL_REPORT_ZH.md), the [v1.1 study page](experiments/chapter6/v11/README.md), and the [evidence manifest](experiments/chapter6/v11/results/screening-20260923-r1/EVIDENCE_MANIFEST.json). This is a negative mechanism-screening result, not a final confirmation of the thesis chapter or a claim that multimodal algorithm discovery is infeasible.

# 多模态智能优化博士论文项目材料

本仓库汇集博士论文前五章对应文章材料、第六章 agent 原型与验证记录，以及总体技术报告，供跨设备查阅和复核。

## 从哪里开始

- [博士论文总体架构与第六章技术报告](docs/report/博士论文总体架构与第六章技术报告.md)：章节结构、方法架构、实验结果和当前缺陷。
- [开题章节草稿](docs/proposal/PROPOSAL_CHAPTER6.md)：第六章拟研究问题与开题表述。
- [技术路线](docs/chapter6/TECHNICAL_ROUTE.md)、[完整实验设计](docs/chapter6/EXPERIMENT_DESIGN.md)、[创新性审查](docs/chapter6/NOVELTY_AUDIT.md)、[冻结协议](docs/chapter6/PREREGISTRATION.md)。
- `papers/`：第三至第五章相关论文的原始材料压缩包，以及可直接阅读的主要 PDF。第一、二章目前是综述性基础章节，没有单独指定论文。
- `experiments/chapter6/demo/` 与 `experiments/chapter6/validation/`：便于浏览的代码和结果摘要。
- `experiments/chapter6/archives/`：完整 demo 和追加验证交付包，包含运行日志、提示/响应、候选程序、usage 和复核材料。

## 当前证据状态

原型已经完成真实模型驱动的受限程序生成、执行、记忆更新和回放审核。pilot 有个别改善信号；冻结追加验证中，预注册质量—多样性门槛为 0/6 个模型—任务单元通过。因此目前支持工程可行性与研究问题的科学性，不支持宣称 v1 已稳定优于简单小生境，也不构成博士创新性已确立的证据。详细边界见技术报告。

## 论文对应关系

- 第三章：多模态多目标进化算法综述；联合空间 DWD 指标。
- 第四章：MSLS-MA（离散 MMTSP）；RMC-CMSA（连续多模态优化）。
- 第五章：HDADE（高维多模态多目标优化）。
- 第六章：受限程序空间上的执行反馈多模态搜索原型与待验证方法。MLEvolve 是启发与相关框架，不是本仓库实验中已完整复现的对照。

## 在另一台电脑获取

```powershell
git clone https://github.com/zhang-li-da/multimodal-optimization-thesis.git
```

若仓库设为私有，需要先用有权限的 GitHub 账号登录。若只需下载文件，可使用 GitHub 页面上的 **Code → Download ZIP**。

## 注意事项

完整实验归档体积较大，但单个文件均适合 GitHub 普通仓库；仓库不包含 API 密钥或个人凭据。模型供应商账号本身不随仓库共享。论文材料按原始文件归档，书目信息和发表状态以正式出版记录为准。
