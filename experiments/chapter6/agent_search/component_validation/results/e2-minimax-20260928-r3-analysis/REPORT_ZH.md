# E2 r3 Offline Analysis

Study `chapter6-component-validation-20260928-e0-e2-r3`: 32/32 jobs have independent test results.

| Arm | Tested | Mean test gap | Mean known tokens | Branch entries | Protected slots | Evictions |
|---|---:|---:|---:|---:|---:|---:|
| P00 | 8/8 | 5.4720% | 228202 | 30 | 0 | 14 |
| P10 | 8/8 | 5.2480% | 229494 | 28 | 28 | 12 |
| P01 | 8/8 | 5.1924% | 225518 | 26 | 0 | 10 |
| P11 | 8/8 | 5.2911% | 227309 | 25 | 30 | 10 |

## Factor effects

Negative values favor the factor-on condition; intervals resample data blocks.

| Factor | Blocks | Mean difference (pp) | Interval |
|---|---:|---:|---:|
| P_S_scheduling_priority | 4 | -0.063 | [-0.202, +0.071] |
| P_E_eviction_protection | 4 | -0.118 | [-0.323, +0.061] |
| P_S_by_P_E_interaction | 4 | +0.323 | [-0.422, +0.973] |

This report reads the archive only and makes no model calls. Full mechanism counts and block pairs are in E2_R3_ANALYSIS.json.
