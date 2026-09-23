# v1.2 r2 evidence package

This is the public result package for the preregistered Chapter 6 v1.2 bounded branch-development screen. It contains all 18 valid r2 runs and their analysis outputs. The earlier r1 batch is excluded because its `niche` controller incorrectly enabled the development pool; it is preserved separately under [`excluded-r1/`](excluded-r1/), with its source snapshot and raw runs, and is not combined with r2. See the [version history](../../VERSION_HISTORY.md) for the freeze and correction commits.

## Result in one paragraph

The branch-development mechanism passed its predeclared trigger gate: 30 follow-up evaluations were scheduled, all 30 child programs were valid, and 6 improved their immediate parent. This establishes that the identify-retain-continue chain ran in real model searches. Mean validation-selected test gap was 5.759% for `niche`, 5.569% for `niche_fixed_dev`, and 6.379% for `relational_branch`. The relation-guided arm was lower in 1 of 6 paired model-block comparisons against fixed development, so this screen does not support an added performance benefit from relation-guided scheduling. Each arm has only six runs; intervals are descriptive and no confirmatory significance claim is made.

Read [TECHNICAL_REPORT_ZH.md](TECHNICAL_REPORT_ZH.md) for the full methods, per-run values, paired contrasts, limitations, and interpretation. The [manifest](manifest.json) fixes the job list, data split fingerprints, source commit, and source fingerprint. The archived [verification record](analysis/verification.json) reports deterministic replay of 18/18 runs, including 198 validation-program and 132 test-program evaluations and a single planner schema. The verification record was generated before this release; it was not recomputed during report packaging.

## Contents

- `manifest.json`: frozen r2 jobs, environment metadata, and fingerprints.
- `analysis/`: replay verification, analysis JSON, per-run and paired CSVs, report, and plots.
- `alibaba-raw-runs.zip`: nine Qwen runs with prompts, model responses, usage, generated programs, checkpoints, and logs.
- `minimax-raw-runs.zip`: nine MiniMax runs with the same artifacts.
- `preregistered-source.zip`: source snapshot at `ba7386a7feafd338a204c64985185e67527b775f`, the source commit recorded by the manifest.
- `excluded-r1/`: audit-only manifest, run status, raw model-run archives, and source snapshot for the invalid initial batch.
- `SHA256SUMS.txt`: SHA-256 hashes for every package file other than the checksum file itself.

Raw-run ZIPs are below GitHub's 100 MB per-file limit. Model account credentials are not included.

## Replay and regenerate

From a clone of this repository at the v1.2 result tag, extract both model archives into the package's `runs/` directory so the job folders sit directly under `runs/`:

```powershell
$result = "experiments/chapter6/v12/results/screening-20260924-r2"
New-Item -ItemType Directory -Force "$result/runs" | Out-Null
Expand-Archive "$result/alibaba-raw-runs.zip" -DestinationPath "$result/runs" -Force
Expand-Archive "$result/minimax-raw-runs.zip" -DestinationPath "$result/runs" -Force
python experiments/chapter6/v12/verify_v12.py $result
python experiments/chapter6/v12/analyze_v12.py $result
```

The verifier reruns all archived program evaluations and controller decisions, so it can take several minutes on CPU. Regenerating the report and plots does not call a model API. To rerun the model search itself, credentials for the configured OpenCode providers are required; do not put them in this repository. The frozen manifest is the authoritative run order and configuration.

The deterministic replay validates the archived code and controller trajectory; it cannot reproduce stochastic model responses without access to the exact provider model version and service behavior. API responses and token usage are therefore retained as the primary search record.

## Interpretation limits

This is a small mechanism screen on 14-city TSP with two model endpoints and three paired blocks. It does not establish generality across tasks, stable quality gains, deployable multi-algorithm selection, or doctoral-level novelty. `test_oracle_gain_upper_bound_only` is not a deployable selector result. The full relation graph, independent witness sampling, W trajectory memory, and independent selector training set were not implemented in this version.

The preregistered source snapshot is distinct from the later report-generation scripts. The frozen source commit and content fingerprint are included in the manifest; report code changes do not alter the experimental source fingerprint.
