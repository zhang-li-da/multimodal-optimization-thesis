# 第六章：执行结果驱动的多模态算法发现 Demo

这是已经接通真实国内模型的研究 agent：模型先规划启发式，再生成可执行的 Python 评分函数，agent 执行候选、保留质量约束的小生境、记录轨迹与终端关系，并决定下一次继续、引用、重启或重新激活。

已完成 74 次正式运行、888 次真实候选生成尝试。先看 [交付结论](DELIVERY.md) 和 [交互演示](dashboard.html)；完整统计见 [实测报告](results/measured_report.md)，逐次数据见 [runs.csv](results/runs.csv)。工程可行性已得到支持，执行记忆相对简单小生境的稳定优势仍需进一步验证。

以下命令从工作区根目录运行。查看保存结果无需配置 API。新环境先运行 python -m pip install -r chapter6_demo/requirements.txt。

## 新运行（使用现有 OpenCode 账号）

    python -m chapter6_demo.discovery --task tsp --method relational --steps 12 --seed 10 --provider alibaba-token-plan-cn --model qwen3.7-plus --output chapter6_demo/runs/my_tsp_demo

装箱任务把 --task 改为 binpack。也可使用：

    python -m chapter6_demo.discovery --task binpack --method relational --steps 12 --seed 10 --provider minimax-cn-coding-plan --model MiniMax-M3 --output chapter6_demo/runs/my_binpack_demo

机器学习分类规则任务把 --task 改为 classification。Iris/Wine/Breast Cancer 的数据来自 sklearn 内置数据集；划分为拟合、probe、验证、最终测试四部分。标准化、类别统计和近邻特征仅使用拟合集，模型生成的 priority(f) 按类别评分并输出分类结果。此任务不需要下载数据或训练大型模型。

程序从本机 OpenCode 的 auth.json 和模型元数据读取已授权账号；凭据只在内存中使用，不写进实验结果。接口失败会如实记录为失败候选；不会静默退化为离线生成。已有相同配置的完成运行直接读取，未完成运行从 checkpoint 恢复。改变配置或源代码后请用新的输出目录。

压缩包应整体解压，保留 chapter6_demo 与 _analysis 两个同级目录；_analysis/models_dev.json 是实验使用的公开模型目录快照。Python 与依赖版本记录在 artifacts/environment_versions.json，可供精确回放时核对。

已做真实连通性检查的提供方：阿里 Token Plan 的 Qwen3.7-Plus、MiniMax 中国 Coding Plan 的 MiniMax-M3、智谱 Coding Plan 的 GLM-5.3-Flash。模型 ID 按本机 OpenCode/公开模型目录核对。

## 复现实验协议

    python -m chapter6_demo.run_suite --tasks tsp binpack --methods quality niche terminal relational --seeds 0 1 2 3 4 --steps 12 --workers 4 --output chapter6_demo/runs/new_factorial

四种方法使用相同任务、生成模型、planner/coder 接口、候选数、每次回复上限和初始规则：

| 方法 | 小生境父代选择 | 执行记忆分配 |
|---|---|---|
| quality | 关闭 | 关闭 |
| niche | 开启 | 关闭 |
| terminal | 关闭 | 开启 |
| relational | 开启 | 开启 |

terminal 的其他经验来源是单个质量父代和轨迹 W；relational 使用多模式质量档案 A 和轨迹 W。因此主实验考察的是两组预先定义的机制，不声称隔离了每一个提示词。relational_no_w 可进一步去掉轨迹通道。

所有方法最终使用同一个质量门槛与行为去重函数读取所有候选。多样性收益不能只来自最后展示了不同数量的程序。实际输入/输出 token、CPU、特征评分次数、2-opt delta 检查次数都保留；本实验采用相同调用上限，并不假称每次实际计算成本完全相同。

## 从日志生成报告与界面

    python -m chapter6_demo.analyze chapter6_demo/runs/pilot_qwen chapter6_demo/runs/pilot_minimax chapter6_demo/runs/pilot_no_w chapter6_demo/runs/pilot_classification --output chapter6_demo/results
    python -m chapter6_demo.dashboard chapter6_demo/runs/pilot_qwen chapter6_demo/runs/pilot_minimax chapter6_demo/runs/pilot_no_w chapter6_demo/runs/pilot_classification

analysis.json 含按任务的探索性 bootstrap 区间、成对差值、2×2 因子效应和事后 token 前缀诊断。PNG/PDF 图由 matplotlib 生成，可用于研究汇报。dashboard.html 是完全离线的交互页面，展示实际候选代码、策略谱系、跨分支引用、终端汇聚、质量曲线，以及路线、装箱和分类混淆矩阵。

## 科学问题与既有工作的联系

- MSLS-MA：离散结构的相似性应由路线边集刻画；复用质量与多样性并重、局部开发、阶段转换和重启的研究思想。TSP 内核为所有候选执行同样的 24 次 2-opt delta 检查，不声称逐行复现 MSLS-MA。
- RMC-CMSA：A 保存竞争行为代表，W 保存中间轨迹，M 保存意图到实际终端行为、成本和失败类型的关系；选择规则使用这些证据重新分配尝试。
- HDADE：质量空间和高维行为空间分开评价。本版不直接加入随机子空间投影，避免未经验证的距离保持假设。
- DWD：本版不直接套用依赖真实参考集的 DWD；TSP 用 Held–Karp 精确最优目标值，装箱用体积下界。行为模式由固定 probe 上的决策关系定义。

算法候选是模型生成的函数，不是随机换一个 TSP 路线或从预置参数列表挑选。搜索空间仍有限制：当前语言允许数值表达式、局部赋值和条件判断；不生成任意训练管线。生成程序通过有界 AST 解释器执行，不使用 Python exec/eval，因此没有文件、网络、反射、导入和无限循环能力。

## 验证

    python -m pytest chapter6_demo/test_science.py chapter6_demo/test_classification.py -q
    node chapter6_demo/browser_check.mjs

对全部最终档案程序做无 API 的确定性回放：

    python -m chapter6_demo.verify_experiments chapter6_demo/runs/pilot_qwen chapter6_demo/runs/pilot_minimax chapter6_demo/runs/pilot_no_w chapter6_demo/runs/pilot_classification

测试覆盖精确求解器与穷举一致性、路线和装箱可行性、等价改名/重构与单调变换、固定执行的可重复性、受限解释器、跨意图重复判定、质量门槛、记忆对调度的实际影响、以及测试集不进入提示。浏览器检查使用本机 Edge 的无头模式，验证界面切换、候选选择和渲染。

## 结果边界

三天内交付的是可运行的原型和初步机制证据。它不能单独证明博士章节的完整创新性，更没有复现 MLEvolve、SeaEvo、AdaEvolve。判断关系记忆是否有效，应查看相对 niche 的增量、独立测试质量及实际成本；不应只看对质量基线更为多样。

早期参数/路线原型已停用：它把求解器随机路线差异混入算法差异，成本计数也不完整，因此 runs/smoke.json 只保留作开发记录，不纳入正式统计。development_* 也属于改进协议前的开发数据；pilot_* 才是冻结后的真实实验。

前 62 次 TSP/装箱实验的冻结核心保存在 artifacts/core_701c32df；随后加入分类任务，新核心版本写入分类实验的 manifest。已有结果都能用当前 evaluator 精确回放。源版本不同会阻止把旧 checkpoint 与新代码混用；若要原样恢复旧核心，可在独立目录把冻结文件放入 chapter6_demo 包中使用。
