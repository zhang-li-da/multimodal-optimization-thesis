# E2 r3 Technical Report

This is the frozen 2x2 component experiment for scheduling priority (P-S) and eviction protection (P-E). It uses MiniMax-M3, four fresh TSP data blocks (48-51), two seeds per arm, and 32 searches in total. Every search completed 32 steps, every task has an independent test record, and the archive contains 2,048 persisted model requests and 7,284,180 known tokens. No new model calls were made during analysis.

The arm means are: P00 5.4720% test gap, P10 5.2480%, P01 5.1924%, and P11 5.2911%. The lower P01 mean is descriptive only because the independent unit is the data block and there are four blocks.

The block-paired factor effects are:

| Factor | Mean difference (percentage points) | Descriptive interval |
|---|---:|---:|
| P-S scheduling priority | -0.063 | [-0.202, +0.071] |
| P-E eviction protection | -0.118 | [-0.323, +0.061] |
| P-S x P-E interaction | +0.323 | [-0.422, +0.973] |

Negative values favor the factor-on condition. The intervals include zero and do not establish a practically meaningful gain. The interaction estimate is especially uncertain. The current result therefore does not prove that either protection component improves the method, and it does not justify claiming that the complete Chapter 6 scheme is correct or effective.

The factors did change the controller trajectory. P-S scheduled 28 protected slots in P10 and 30 in P11; P-E changed eviction eligibility, with 12, 10, and 10 pool evictions in P10, P01, and P11 respectively. Protected slots produced 5 and 7 local improvements in P10 and P11, but only 2 and 0 global improvements. These process changes establish that the factors were exercised; they do not establish positive opportunity-cost-adjusted value.

The appropriate conclusion is a negative or inconclusive component result: retain the archive, report the search pipeline as operational, and do not promote P-S or P-E to a confirmed method contribution. A follow-up should either simplify to the strongest fixed baseline or run a separately frozen confirmation study whose minimum practical gain and sample size are set before inspecting its outcomes.

The full numeric analysis, block pairs, costs, status records, and mechanism counters are in `E2_R3_ANALYSIS.json`. The immutable raw archive is `raw-study.zip`; `ARCHIVE_AUDIT.json` and `ARCHIVE_INDEX.json` record its file hashes.
