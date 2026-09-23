# Chapter 6 v1.1 quality-protection experiment report

Study: `screening-20260923-r1`. Preregistered source commit: `7b4f9d2a8059fe1aadc92ddd09f1fde1bd9cb454`.
Manifest SHA-256: `2ca14d7e95de1173773bcd5e8c1f76d52cfd1d29314507cdb5f4ff67364bd19a`; source fingerprint: `3ff293fa5fe8eac7a2982fe029090ebdf211e6565eabd2159bdb02ac25741bc3`.

## Design and inferential scope

This is a live screening experiment of parent/neighborhood quality protection and restart correction in a 2×2 controller factorial, with niche search as a contextual baseline. It crosses TSP and online bin packing, Qwen3.7-Plus and MiniMax-M3, five paired data blocks, and fixed 8-slot versus 30,000 input+output token budgets (200 planned runs). The API model writes bounded heuristic code; deterministic evaluators execute it on separate probe, validation, and test instances. The validation-fitted per-instance selector is TSP-only because full-sequence bin-packing descriptors would reveal future arrivals.

The unit of replication is the paired block-level run. Generated candidates and test instances are not treated as independent algorithm replications. Intervals are percentile bootstrap intervals over five paired blocks; they are descriptive and do not establish small effects or doctoral-level novelty. No confirmatory p-values are reported.

## Completion and data integrity

Results files present: 200/200. Status counts: `{"completed": 200}`.
Integrity problems detected: 0.

See `run_level.csv` for all planned jobs, including absent/failed runs; `group_summary.csv` for per-cell means, medians, intervals, and block values; `paired_contrasts.csv` for relational-minus-niche paired contrasts and factorial main effects/interactions.

## Primary outcomes by cell

Lower test loss is better. The archive mode count requires held-out test quality eligibility against the shared-seed test threshold; the validation-fitted selector is reported separately. A test-instance oracle is an unattainable upper bound only.

| Model | Task | Budget | Method | Test loss mean [95% interval] | Test modes mean [95% interval] | Selector gain mean [95% interval] | n |
|---|---|---|---|---:|---:|---:|---:|
| qwen3.7-plus | binpack | slots8 | niche | 0.1101 [0.1055, 0.1146] | 2.6000 [2.0000, 3.4000] | — | 5 |
| qwen3.7-plus | binpack | slots8 | relational | 0.1101 [0.1055, 0.1146] | 2.4000 [2.0000, 2.8000] | — | 5 |
| qwen3.7-plus | binpack | slots8 | relational_qp | 0.1101 [0.1055, 0.1146] | 2.4000 [2.0000, 2.8000] | — | 5 |
| qwen3.7-plus | binpack | slots8 | relational_rr | 0.1101 [0.1055, 0.1146] | 2.8000 [2.2000, 3.4000] | — | 5 |
| qwen3.7-plus | binpack | slots8 | relational_qp_rr | 0.1101 [0.1055, 0.1146] | 2.6000 [2.2000, 3.0000] | — | 5 |
| qwen3.7-plus | binpack | tokens30000 | niche | 0.1101 [0.1055, 0.1146] | 3.8000 [2.6000, 5.2000] | — | 5 |
| qwen3.7-plus | binpack | tokens30000 | relational | 0.1101 [0.1055, 0.1146] | 3.0000 [2.2000, 4.0000] | — | 5 |
| qwen3.7-plus | binpack | tokens30000 | relational_qp | 0.1101 [0.1055, 0.1146] | 2.0000 [2.0000, 2.0000] | — | 5 |
| qwen3.7-plus | binpack | tokens30000 | relational_rr | 0.1101 [0.1055, 0.1146] | 2.4000 [2.0000, 2.8000] | — | 5 |
| qwen3.7-plus | binpack | tokens30000 | relational_qp_rr | 0.1101 [0.1055, 0.1146] | 2.0000 [2.0000, 2.0000] | — | 5 |
| qwen3.7-plus | tsp | slots8 | niche | 0.0512 [0.0434, 0.0610] | 4.8000 [4.0000, 6.0000] | -0.0065 [-0.0171, 0.0040] | 5 |
| qwen3.7-plus | tsp | slots8 | relational | 0.0427 [0.0313, 0.0569] | 4.0000 [3.2000, 4.8000] | -0.0023 [-0.0069, 0.0016] | 5 |
| qwen3.7-plus | tsp | slots8 | relational_qp | 0.0529 [0.0484, 0.0572] | 3.4000 [1.8000, 5.0000] | -0.0012 [-0.0027, -0.0001] | 5 |
| qwen3.7-plus | tsp | slots8 | relational_rr | 0.0529 [0.0474, 0.0584] | 3.4000 [2.2000, 4.8000] | -0.0002 [-0.0080, 0.0052] | 5 |
| qwen3.7-plus | tsp | slots8 | relational_qp_rr | 0.0521 [0.0445, 0.0580] | 3.6000 [2.6000, 4.4000] | -0.0021 [-0.0049, 0.0008] | 5 |
| qwen3.7-plus | tsp | tokens30000 | niche | 0.0548 [0.0480, 0.0605] | 4.2000 [3.6000, 4.8000] | 0.0038 [-0.0023, 0.0108] | 5 |
| qwen3.7-plus | tsp | tokens30000 | relational | 0.0555 [0.0509, 0.0603] | 3.0000 [2.0000, 4.2000] | -0.0017 [-0.0066, 0.0063] | 5 |
| qwen3.7-plus | tsp | tokens30000 | relational_qp | 0.0591 [0.0533, 0.0666] | 3.4000 [2.6000, 4.0000] | -0.0031 [-0.0061, -0.0004] | 5 |
| qwen3.7-plus | tsp | tokens30000 | relational_rr | 0.0591 [0.0522, 0.0692] | 4.6000 [3.2000, 6.4000] | -0.0001 [-0.0032, 0.0037] | 5 |
| qwen3.7-plus | tsp | tokens30000 | relational_qp_rr | 0.0630 [0.0541, 0.0718] | 2.8000 [1.6000, 4.2000] | -0.0012 [-0.0075, 0.0050] | 5 |
| MiniMax-M3 | binpack | slots8 | niche | 0.1101 [0.1055, 0.1146] | 5.2000 [3.6000, 6.6000] | — | 5 |
| MiniMax-M3 | binpack | slots8 | relational | 0.1101 [0.1055, 0.1146] | 4.4000 [3.0000, 5.8000] | — | 5 |
| MiniMax-M3 | binpack | slots8 | relational_qp | 0.1103 [0.1055, 0.1156] | 3.8000 [3.4000, 4.0000] | — | 5 |
| MiniMax-M3 | binpack | slots8 | relational_rr | 0.1101 [0.1055, 0.1146] | 3.4000 [2.4000, 4.8000] | — | 5 |
| MiniMax-M3 | binpack | slots8 | relational_qp_rr | 0.1101 [0.1055, 0.1146] | 3.8000 [2.2000, 6.0000] | — | 5 |
| MiniMax-M3 | binpack | tokens30000 | niche | 0.1101 [0.1055, 0.1146] | 2.6000 [2.2000, 3.0000] | — | 5 |
| MiniMax-M3 | binpack | tokens30000 | relational | 0.1101 [0.1055, 0.1146] | 3.2000 [2.2000, 4.2000] | — | 5 |
| MiniMax-M3 | binpack | tokens30000 | relational_qp | 0.1101 [0.1055, 0.1146] | 2.6000 [2.0000, 3.4000] | — | 5 |
| MiniMax-M3 | binpack | tokens30000 | relational_rr | 0.1101 [0.1055, 0.1146] | 2.6000 [2.0000, 3.4000] | — | 5 |
| MiniMax-M3 | binpack | tokens30000 | relational_qp_rr | 0.1101 [0.1055, 0.1146] | 3.4000 [2.2000, 4.8000] | — | 5 |
| MiniMax-M3 | tsp | slots8 | niche | 0.0578 [0.0498, 0.0674] | 3.4000 [2.2000, 4.6000] | -0.0028 [-0.0075, 0.0006] | 5 |
| MiniMax-M3 | tsp | slots8 | relational | 0.0608 [0.0499, 0.0722] | 3.4000 [1.6000, 5.2000] | 0.0017 [-0.0037, 0.0073] | 5 |
| MiniMax-M3 | tsp | slots8 | relational_qp | 0.0650 [0.0530, 0.0774] | 2.8000 [1.4000, 4.2000] | 0.0026 [-0.0043, 0.0123] | 5 |
| MiniMax-M3 | tsp | slots8 | relational_rr | 0.0552 [0.0511, 0.0605] | 2.8000 [1.4000, 4.2000] | 0.0015 [-0.0016, 0.0051] | 5 |
| MiniMax-M3 | tsp | slots8 | relational_qp_rr | 0.0595 [0.0520, 0.0681] | 2.2000 [1.2000, 3.2000] | 0.0027 [-0.0013, 0.0088] | 5 |
| MiniMax-M3 | tsp | tokens30000 | niche | 0.0472 [0.0367, 0.0564] | 4.0000 [2.4000, 5.6000] | -0.0089 [-0.0203, 0.0005] | 5 |
| MiniMax-M3 | tsp | tokens30000 | relational | 0.0612 [0.0538, 0.0698] | 2.0000 [1.0000, 3.2000] | 0.0006 [-0.0046, 0.0059] | 5 |
| MiniMax-M3 | tsp | tokens30000 | relational_qp | 0.0618 [0.0534, 0.0708] | 2.2000 [1.0000, 3.8000] | -0.0004 [-0.0011, 0.0000] | 5 |
| MiniMax-M3 | tsp | tokens30000 | relational_rr | 0.0641 [0.0534, 0.0749] | 1.8000 [1.0000, 2.6000] | 0.0004 [-0.0067, 0.0078] | 5 |
| MiniMax-M3 | tsp | tokens30000 | relational_qp_rr | 0.0622 [0.0538, 0.0708] | 2.4000 [1.6000, 3.4000] | -0.0006 [-0.0020, 0.0008] | 5 |

## Interpretation limits

Read the individual paired blocks and integrity columns before interpreting a cell average. An absent result, API request failure, missing token usage, reservation violation, or budget stop is retained and is not replaced based on its outcome. A missing provider usage value invalidates strict token-efficiency claims for that run. The `tokens30000` regime is a hard admission ceiling with UTF-8 byte-based conservative reservations; reservation violations are explicitly flagged and do not qualify as compliant runs.

The TSP algorithm-set selector uses validation outcomes and public instance features only. No full-sequence selector is evaluated for online bin packing. The per-test-instance oracle uses held-out outcomes and is not deployable. Positive screening patterns require a new independently frozen confirmation; null or mixed outcomes narrow the mechanism claim.

Integrity details: `[]`
