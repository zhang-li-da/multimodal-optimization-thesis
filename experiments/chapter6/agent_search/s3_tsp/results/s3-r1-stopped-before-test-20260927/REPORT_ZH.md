# S3 MiniMax M3 TSP 搜索策略实验报告

实验 `s3-tsp-minimax-strategy-screen-20260927-r1` 计划 48 个任务，完成 test 评价 0 个；数据块为独立重采样单位。

本实验是单模型、TSP14 的固定预算策略筛查。下表的 test gap 越低越好；不报告确认性 p 值。

| 组别 | 已测试/计划 | 平均 Test gap | 平均已知 tokens | 有效提案/槽位 | 保护槽位 | 落后分支槽位 | 保护局部改进 |
|---|---:|---:|---:|---:|---:|---:|
| SP | 0/8 | NA | 0 | 0/0 | 0 | 0 | 0 |
| WR | 0/8 | NA | 56474 | 64/64 | 0 | 0 | 0 |
| FB_U | 0/8 | NA | 0 | 0/0 | 0 | 0 | 0 |
| FB_P | 0/8 | NA | 0 | 0/0 | 0 | 0 | 0 |
| TS_P | 0/8 | NA | 29693 | 31/32 | 20 | 18 | 10 |
| AD_P | 0/8 | NA | 0 | 0/0 | 0 | 0 | 0 |

## 配对区块差异

负值表示差异定义中的左侧方法 test gap 较低。区间是按完整数据块重采样的 95% 描述性区间。

| 对比 | 完整配对数 | 平均差 (百分点) | 95% 区间 | 左侧胜/平/负 |
|---|---:|---:|---:|---:|
| FB_P_minus_FB_U | 0 | NA | NA | 0/0/0 |
| TS_P_minus_FB_P | 0 | NA | NA | 0/0/0 |
| AD_P_minus_FB_P | 0 | NA | NA | 0/0/0 |
| SP_minus_FB_P | 0 | NA | NA | 0/0/0 |
| WR_minus_FB_P | 0 | NA | NA | 0/0/0 |

## 解释边界

- This is a single-model TSP14 strategy screen, not a confirmatory multi-task study.
- Confidence intervals resample data blocks; proposal and instance counts are not independent search replicates.
- No p-values or post-hoc stopping are used. Missing jobs remain visible and are not replaced.
- A prespecified screening signal requires at least 6 complete blocks, mean gain of at least 0.3 percentage points, a descriptive interval below zero, and no more than 10% mean token increase.

每个区块的各组结果、运行状态和成本均保留在 `S3_ANALYSIS.json` 与原始归档中。
