# Agent demo 实测报告

这里汇总真实模型生成、受限程序执行和独立测试实例上的结果。

比较的是本 demo 内的控制器机制，不是 MLEvolve、SeaEvo 或 AdaEvolve 的完整复现。

候选数量和单次调用输出上限相同；输入 token 和运行时间不同，完整成本见 runs.csv。

| 模型 | 任务 | 控制器 | 次数 | 最佳测试损失↓ | 验证模式数↑ | 测试模式数↑ | 重复比例↓ | 总 token |
|---|---|---|---:|---:|---:|---:|---:|---:|
| MiniMax-M3 | binpack | niche | 3 | 0.1231 | 4.67 | 4.33 | 52.8% | 31056 |
| MiniMax-M3 | binpack | relational | 3 | 0.1231 | 5.00 | 4.67 | 66.7% | 39895 |
| MiniMax-M3 | tsp | niche | 3 | 0.0487 | 2.33 | 2.00 | 72.2% | 35386 |
| MiniMax-M3 | tsp | relational | 3 | 0.0714 | 2.00 | 2.00 | 33.3% | 43374 |
| qwen3.7-plus | binpack | niche | 5 | 0.1231 | 3.00 | 3.00 | 85.0% | 24667 |
| qwen3.7-plus | binpack | quality | 5 | 0.1231 | 2.60 | 2.60 | 86.7% | 23169 |
| qwen3.7-plus | binpack | relational | 5 | 0.1231 | 3.60 | 3.60 | 75.0% | 36726 |
| qwen3.7-plus | binpack | relational_no_w | 5 | 0.1231 | 2.40 | 2.40 | 96.7% | 36327 |
| qwen3.7-plus | binpack | terminal | 5 | 0.1231 | 3.00 | 3.00 | 90.0% | 36485 |
| qwen3.7-plus | classification | niche | 3 | 0.0685 | 3.33 | 2.33 | 100.0% | 27661 |
| qwen3.7-plus | classification | quality | 3 | 0.0685 | 3.00 | 1.33 | 100.0% | 26119 |
| qwen3.7-plus | classification | relational | 3 | 0.0549 | 3.00 | 1.00 | 91.7% | 39828 |
| qwen3.7-plus | classification | terminal | 3 | 0.0759 | 3.33 | 2.00 | 94.4% | 40648 |
| qwen3.7-plus | tsp | niche | 5 | 0.0425 | 2.80 | 2.60 | 81.7% | 31734 |
| qwen3.7-plus | tsp | quality | 5 | 0.0555 | 1.80 | 1.80 | 91.7% | 29575 |
| qwen3.7-plus | tsp | relational | 5 | 0.0461 | 2.00 | 1.60 | 48.3% | 41297 |
| qwen3.7-plus | tsp | relational_no_w | 5 | 0.0436 | 2.60 | 2.00 | 38.3% | 39952 |
| qwen3.7-plus | tsp | terminal | 5 | 0.0520 | 3.00 | 2.20 | 41.7% | 42333 |

TSP 损失为 Held–Karp 精确最优路线长度的相对 gap；装箱损失为箱数相对体积下界的 gap，该下界不一定可达到；classification 为 Iris/Wine/Breast Cancer 三个数据集错误率的平均。不同任务的损失不能直接横向平均。

## 完整方法相对小生境基线的增量

以下区间是逐任务、运行级 bootstrap 的探索性 95% 区间。区间包含 0 时不能宣称稳定增量；也没有进行跨指标多重检验控制。

- MiniMax-M3 / binpack，3 次运行：
  - 测试损失差（负值有利）：0.0000，区间 [0.0000, 0.0000]。
  - 验证模式数差（正值有利）：0.3333，区间 [-3.0000, 5.0000]。
  - 重复比例差（负值有利）：0.1389，区间 [-0.2500, 0.6667]。
- MiniMax-M3 / tsp，3 次运行：
  - 测试损失差（负值有利）：0.0226，区间 [-0.0015, 0.0384]。
  - 验证模式数差（正值有利）：-0.3333，区间 [-1.0000, 0.0000]。
  - 重复比例差（负值有利）：-0.3889，区间 [-0.7500, -0.1667]。
- qwen3.7-plus / binpack，5 次运行：
  - 测试损失差（负值有利）：0.0000，区间 [0.0000, 0.0000]。
  - 验证模式数差（正值有利）：0.6000，区间 [-1.4000, 2.4000]。
  - 重复比例差（负值有利）：-0.1000，区间 [-0.3167, 0.1167]。
- qwen3.7-plus / classification，3 次运行：
  - 测试损失差（负值有利）：-0.0135，区间 [-0.0222, -0.0039]。
  - 验证模式数差（正值有利）：-0.3333，区间 [-2.0000, 1.0000]。
  - 重复比例差（负值有利）：-0.0833，区间 [-0.1667, 0.0000]。
- qwen3.7-plus / tsp，5 次运行：
  - 测试损失差（负值有利）：0.0036，区间 [-0.0031, 0.0087]。
  - 验证模式数差（正值有利）：-0.8000，区间 [-1.4000, -0.2000]。
  - 重复比例差（负值有利）：-0.3333，区间 [-0.5167, -0.1667]。

## 同 token 上限的前缀诊断

从每组已完成日志中取所有控制器实际总 token 的最小值作为共同上限，只保留累计 token 未超限的完整迭代。它是事后轨迹前缀分析，不是重新运行的等预算实验；没有使用后续候选补入前缀，也不用于声称改变预算后的搜索轨迹仍相同。

| 模型 | 任务 | 控制器 | 保留候选均值 | 验证模式数均值 | 最佳验证损失 |
|---|---|---|---:|---:|---:|
| MiniMax-M3 | binpack | niche | 12.00 | 4.67 | 0.0857 |
| MiniMax-M3 | binpack | relational | 9.33 | 4.00 | 0.0857 |
| MiniMax-M3 | tsp | niche | 12.00 | 2.33 | 0.0579 |
| MiniMax-M3 | tsp | relational | 9.33 | 2.00 | 0.0624 |
| qwen3.7-plus | binpack | niche | 10.80 | 2.80 | 0.0857 |
| qwen3.7-plus | binpack | quality | 12.00 | 2.60 | 0.0857 |
| qwen3.7-plus | binpack | relational | 7.40 | 2.40 | 0.0857 |
| qwen3.7-plus | binpack | relational_no_w | 7.00 | 2.40 | 0.0857 |
| qwen3.7-plus | binpack | terminal | 7.00 | 2.20 | 0.0857 |
| qwen3.7-plus | classification | niche | 11.00 | 3.33 | 0.0237 |
| qwen3.7-plus | classification | quality | 11.67 | 3.00 | 0.0239 |
| qwen3.7-plus | classification | relational | 7.67 | 3.00 | 0.0239 |
| qwen3.7-plus | classification | terminal | 7.00 | 3.00 | 0.0263 |
| qwen3.7-plus | tsp | niche | 11.00 | 2.60 | 0.0372 |
| qwen3.7-plus | tsp | quality | 11.80 | 1.60 | 0.0534 |
| qwen3.7-plus | tsp | relational | 8.20 | 2.60 | 0.0629 |
| qwen3.7-plus | tsp | relational_no_w | 8.40 | 2.40 | 0.0482 |
| qwen3.7-plus | tsp | terminal | 8.20 | 2.60 | 0.0541 |

## 证据边界

1. 模式由固定 probe 上的执行决策关系定义；它是可操作的行为分组，不是已经枚举的全局算法最优模式。
2. 相同程序在固定实例上确定性执行；变量重命名和单调分数变换不会生成虚假模式。
3. 生成空间是有界 Python 启发式评分函数；这是程序生成，尚未覆盖任意模型训练管线或完整通用 agent。
4. 真实模型抽样即使固定本地 seed 仍不保证逐字重现；prompt、response、usage、程序和 checkpoint 已保存。
5. 若只优于 quality 而未稳定优于 niche，证据支持多模态搜索原型可运行，不能支持关系记忆独立有效。
6. 三天内的 demo 是可行性和初步机制证据，不能代替博士章节所需的最近邻复现、更多任务、严格成本控制和完整创新性论证。
