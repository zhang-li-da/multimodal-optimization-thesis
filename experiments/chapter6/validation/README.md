# 第六章追加验证与研究设计

本目录接续 chapter6_demo，检验冻结原型的稳定增量，并核查博士章节的创新性。旧 demo 与原始论文保持独立。

阅读顺序：

1. DELIVERY.md：本轮结果与结论（全部实验结束后生成）。
2. results/measured_report.md：真实追加实验、统计与成本。
3. NOVELTY_AUDIT.md：最近邻证据与尚未成立的主张。
4. TECHNICAL_ROUTE.md：已实现 v1 与待验证下一版的技术路线。
5. EXPERIMENT_DESIGN.md：完整任务、基线、消融、预算和统计设计。
6. PREREGISTRATION.md：正式实验发起前固定的判据。

## 数据与运行

主分析取 runs/confirm_v1 内的 Qwen 与 runs/confirm_minimax_recovery 内的 MiniMax。后者按全提供方修订重新运行；confirm_v1 内所有 MiniMax 记录仍保留为基础设施证据，不能择优拼接。修订登记在 artifacts/infrastructure_amendment.json。

运行冻结原型的一次新实验：

    python -m chapter6_validation.run --task tsp --method relational --partition 31 --provider alibaba-token-plan-cn --model qwen3.7-plus --token-cap 60000 --output chapter6_validation/runs/new_tsp_p31

现有实验统计：

    python -m chapter6_validation.analyze chapter6_validation/runs/confirm_v1 --recovery-root chapter6_validation/runs/confirm_minimax_recovery --output chapter6_validation/results

无 API 回放审计：

    python -m chapter6_validation.verify

机制及协议测试：

    python -m pytest chapter6_validation/test_protocol.py chapter6_validation/test_analysis.py chapter6_validation/test_witness.py chapter6_validation/program_regression_test.py -q

辅助检查：

    python -m chapter6_validation.classifier_context
    python -m chapter6_validation.witness_mechanism

上述分类器参照不属于同语言/同预算的搜索控制器对比，有限域 witness 检查也不证明新搜索算法有效。

生成可离线阅读的页面与图：

    python -m chapter6_validation.readers
    python -m chapter6_validation.review_page
    node chapter6_validation/browser_check.mjs

HTML 已随交付生成，查看无需安装 Pandoc。重新生成文字 HTML 需本机 Pandoc；浏览器检查脚本使用 Windows 上的 Edge。科学评价与分析是 Python 脚本，不依赖这些展示工具。

## 关键实现

benchmarks.py 使用新划分，复用旧语言与执行规则；budget.py 在每次调用前做保守准入，并验证真实 usage；run.py 直接调用旧 SearchState 和提示；protocol.py 固定任务、预算、指标、非劣界与统计门槛；analyze.py 按划分配对并控制 24 项检验；verify.py 审计所有记录并回放最终程序。

原版核心指纹为 c4b522edb87ae89299c283b7322c856d4e19af9cdf0dab9140618788790c1194；追加驱动器指纹为 8f93acbc027a64bad421eff1a3c80fb2587fd23d3a871119b61dad0e41113e73。冻结快照在 artifacts/frozen_v1。文档和统计脚本可补充解释，运行中的科学核心不作修改。

Python 依赖沿用 chapter6_demo/requirements.txt，统计另用 scipy（已由 scikit-learn 安装）。生成模型使用本机授权 OpenCode 账号；查看报告与回放不需要账号。HTML 报告全部可离线打开。
