# 执行重复与开发收益的事后诊断

以下只汇总成本可核验运行。它是描述性关联分析，不改变预定主指标，也不证明重启是质量变化的因果原因。

| 模型 | 任务 | 方法 | 运行 | 严格质量改进 | 其中被判为重复 | 重启 / 候选 | 平均每候选 token |
|---|---|---|---:|---:|---:|---:|---:|
| MiniMax-M3 | binpack | niche | 9 | 4 | 0 | 23 / 177 | 2683 |
| MiniMax-M3 | binpack | relational | 10 | 1 | 0 | 104 / 150 | 3381 |
| MiniMax-M3 | classification | niche | 10 | 17 | 17 | 30 / 198 | 2671 |
| MiniMax-M3 | classification | relational | 10 | 13 | 13 | 116 / 152 | 3343 |
| MiniMax-M3 | tsp | niche | 10 | 12 | 10 | 26 / 192 | 2753 |
| MiniMax-M3 | tsp | relational | 10 | 6 | 4 | 107 / 140 | 3585 |
| qwen3.7-plus | binpack | niche | 10 | 3 | 0 | 37 / 248 | 2163 |
| qwen3.7-plus | binpack | relational | 10 | 1 | 0 | 136 / 170 | 3115 |
| qwen3.7-plus | classification | niche | 10 | 16 | 16 | 35 / 224 | 2408 |
| qwen3.7-plus | classification | relational | 10 | 7 | 7 | 122 / 149 | 3478 |
| qwen3.7-plus | tsp | niche | 9 | 18 | 13 | 27 / 185 | 2613 |
| qwen3.7-plus | tsp | relational | 10 | 13 | 8 | 98 / 140 | 3647 |

原控制器虽把质量进展计作 useful_gain，但调度另行对所有 collision 施加惩罚。相同候选可以同时触发进展与碰撞，说明这两个信号不应直接视为相反。实际改进幅度、同模式互补性与 probe 误并需要进一步分开判断。

下一版必须通过质量保护消融、重启率对照和随机关系对照来检验机制解释。不能仅凭这些计数就宣布新版会改善性能。
