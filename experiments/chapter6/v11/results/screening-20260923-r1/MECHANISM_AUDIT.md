# Post-search mechanism audit

This audit is descriptive and was added after the live run started. It cannot establish a causal performance improvement.
Fixed-history comparisons replay the same saved programs under four controllers; the model responses under changed prompts remain unknown.
Restart triggers may overlap. A local-only collision improves a parent or same-behavior neighbor inside the quality envelope, without refreshing the global best.

| Model | Task | Budget | Method | Runs | Local-only collisions | Credited | Retained in A | Later parent | Restarts | Untried trigger | Stalled trigger | Saturation trigger |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MiniMax-M3 | binpack | slots8 | niche | 5 | 4 | 4 | 0 | 0 | 5 | 0 | 0 | 0 |
| MiniMax-M3 | binpack | slots8 | relational | 5 | 1 | 1 | 0 | 0 | 29 | 25 | 16 | 0 |
| MiniMax-M3 | binpack | slots8 | relational_qp | 5 | 1 | 1 | 0 | 0 | 30 | 25 | 17 | 0 |
| MiniMax-M3 | binpack | slots8 | relational_qp_rr | 5 | 3 | 3 | 0 | 0 | 29 | 25 | 18 | 0 |
| MiniMax-M3 | binpack | slots8 | relational_rr | 5 | 3 | 3 | 0 | 0 | 32 | 25 | 19 | 0 |
| MiniMax-M3 | binpack | tokens30000 | niche | 5 | 8 | 8 | 0 | 0 | 5 | 0 | 0 | 0 |
| MiniMax-M3 | binpack | tokens30000 | relational | 5 | 2 | 2 | 2 | 0 | 25 | 25 | 14 | 0 |
| MiniMax-M3 | binpack | tokens30000 | relational_qp | 5 | 0 | 0 | 0 | 0 | 26 | 25 | 14 | 0 |
| MiniMax-M3 | binpack | tokens30000 | relational_qp_rr | 5 | 0 | 0 | 0 | 0 | 25 | 25 | 12 | 0 |
| MiniMax-M3 | binpack | tokens30000 | relational_rr | 5 | 0 | 0 | 0 | 0 | 28 | 25 | 16 | 0 |
| MiniMax-M3 | tsp | slots8 | niche | 5 | 2 | 2 | 2 | 1 | 5 | 0 | 0 | 0 |
| MiniMax-M3 | tsp | slots8 | relational | 5 | 0 | 0 | 0 | 0 | 31 | 25 | 20 | 0 |
| MiniMax-M3 | tsp | slots8 | relational_qp | 5 | 0 | 0 | 0 | 0 | 30 | 25 | 19 | 0 |
| MiniMax-M3 | tsp | slots8 | relational_qp_rr | 5 | 4 | 4 | 2 | 0 | 29 | 25 | 20 | 0 |
| MiniMax-M3 | tsp | slots8 | relational_rr | 5 | 0 | 0 | 0 | 0 | 31 | 25 | 21 | 0 |
| MiniMax-M3 | tsp | tokens30000 | niche | 5 | 3 | 3 | 1 | 1 | 5 | 0 | 0 | 0 |
| MiniMax-M3 | tsp | tokens30000 | relational | 5 | 1 | 1 | 1 | 0 | 27 | 25 | 15 | 0 |
| MiniMax-M3 | tsp | tokens30000 | relational_qp | 5 | 1 | 1 | 0 | 0 | 24 | 22 | 16 | 0 |
| MiniMax-M3 | tsp | tokens30000 | relational_qp_rr | 5 | 2 | 2 | 2 | 0 | 23 | 22 | 12 | 0 |
| MiniMax-M3 | tsp | tokens30000 | relational_rr | 5 | 0 | 0 | 0 | 0 | 24 | 23 | 15 | 0 |
| qwen3.7-plus | binpack | slots8 | niche | 5 | 13 | 13 | 0 | 0 | 5 | 0 | 0 | 0 |
| qwen3.7-plus | binpack | slots8 | relational | 5 | 4 | 4 | 0 | 0 | 35 | 25 | 28 | 0 |
| qwen3.7-plus | binpack | slots8 | relational_qp | 5 | 5 | 5 | 0 | 0 | 31 | 25 | 24 | 0 |
| qwen3.7-plus | binpack | slots8 | relational_qp_rr | 5 | 3 | 3 | 0 | 0 | 32 | 25 | 27 | 0 |
| qwen3.7-plus | binpack | slots8 | relational_rr | 5 | 2 | 2 | 0 | 0 | 34 | 25 | 29 | 0 |
| qwen3.7-plus | binpack | tokens30000 | niche | 5 | 10 | 10 | 0 | 0 | 9 | 0 | 0 | 0 |
| qwen3.7-plus | binpack | tokens30000 | relational | 5 | 0 | 0 | 0 | 0 | 28 | 25 | 18 | 0 |
| qwen3.7-plus | binpack | tokens30000 | relational_qp | 5 | 0 | 0 | 0 | 0 | 30 | 25 | 25 | 0 |
| qwen3.7-plus | binpack | tokens30000 | relational_qp_rr | 5 | 0 | 0 | 0 | 0 | 30 | 25 | 25 | 0 |
| qwen3.7-plus | binpack | tokens30000 | relational_rr | 5 | 0 | 0 | 0 | 0 | 30 | 25 | 21 | 0 |
| qwen3.7-plus | tsp | slots8 | niche | 5 | 3 | 3 | 2 | 0 | 5 | 0 | 0 | 0 |
| qwen3.7-plus | tsp | slots8 | relational | 5 | 1 | 1 | 1 | 0 | 26 | 25 | 13 | 0 |
| qwen3.7-plus | tsp | slots8 | relational_qp | 5 | 1 | 1 | 1 | 0 | 28 | 25 | 17 | 0 |
| qwen3.7-plus | tsp | slots8 | relational_qp_rr | 5 | 2 | 2 | 2 | 0 | 28 | 25 | 11 | 0 |
| qwen3.7-plus | tsp | slots8 | relational_rr | 5 | 2 | 2 | 2 | 0 | 29 | 25 | 15 | 0 |
| qwen3.7-plus | tsp | tokens30000 | niche | 5 | 4 | 4 | 1 | 0 | 10 | 0 | 0 | 0 |
| qwen3.7-plus | tsp | tokens30000 | relational | 5 | 1 | 1 | 1 | 0 | 27 | 24 | 12 | 0 |
| qwen3.7-plus | tsp | tokens30000 | relational_qp | 5 | 3 | 3 | 3 | 0 | 26 | 23 | 11 | 0 |
| qwen3.7-plus | tsp | tokens30000 | relational_qp_rr | 5 | 1 | 1 | 1 | 0 | 25 | 23 | 11 | 0 |
| qwen3.7-plus | tsp | tokens30000 | relational_rr | 5 | 4 | 4 | 4 | 0 | 23 | 23 | 8 | 0 |

Full program examples, fixed-history decision differences and token under-utilization are in `mechanism_audit.json` and `mechanism_runs.csv`.
