# v1.2 r2 evidence package — implementation errata

The 18 archived searches are historical mechanism evidence. This revision adds **zero model calls**. Mean test gaps remain 5.759% (niche), 5.569% (fixed development), and 6.379% (relational controller). There were 30 evaluated branch children, 6 parent improvements and 5 simultaneous global-validation improvements.

The two development arms used different ordinary schedulers. The relational path also used W-only references 28 times. Its 25 restarts included 23 targeting untried labels, and only 4 development slots had multiple eligible branches. Thus r2 compares whole controllers and does not isolate branch ranking. The fixed policy was available-list index rotation, not FIFO; the old maximum-depth field (4) includes failed attempts, while maximum successful-chain depth is 3.

Read [TECHNICAL_REPORT_ZH.md](TECHNICAL_REPORT_ZH.md), [current offline tools](../../../v12_1/README.md), and [review response](../../../../../docs/chapter6/reviews/V12_R2_INDEPENDENT_REVIEW.md). The [original protocol](../../preregistration.md) remains unchanged; these are post-hoc corrections, not retrospective preregistration.

## Contents and provenance

- `manifest.json` freezes the 18 jobs and source fingerprint.
- `alibaba-raw-runs.zip` and `minimax-raw-runs.zip` preserve prompts, responses, usage, programs, checkpoints, and evaluations.
- `preregistered-source.zip` preserves source commit `ba7386a7feafd338a204c64985185e67527b775f`.
- `analysis/analysis.json`, CSVs and plots preserve the original numeric analysis. `branch_max_depth` is the historical mixed field, and `initial_best_seed_test_gap` is a test-selected seed upper bound, not a validation-selected seed baseline.
- `analysis/verification.json` is the original local strict replay record (18 runs, 198 validation and 132 test evaluations); it does not certify every OS.
- The two Markdown reports and their generator now include errata; edits are versioned. `analysis_code/` records report/verification tooling.
- `excluded-r1/` preserves the invalid first batch for audit only; never pool it with r2.
- `SHA256SUMS.txt` covers every package file except itself.

The original result tag `chapter6-v1.2-screening-20260924` is retained. See [version history](VERSION_HISTORY.md) for GitHub source mappings. The corrected controller lives in a separate v1.2.1 module and leaves the original experiment fingerprint unchanged.

## Replay without changing published records

From the repository root, use a new workspace directory:

```powershell
$package = "experiments/chapter6/v12/results/screening-20260924-r2"
$replay = "audit-local/r2"
New-Item -ItemType Directory -Force "$replay/runs" | Out-Null
Copy-Item "$package/manifest.json" "$replay/manifest.json"
Expand-Archive "$package/alibaba-raw-runs.zip" -DestinationPath "$replay/runs" -Force
Expand-Archive "$package/minimax-raw-runs.zip" -DestinationPath "$replay/runs" -Force
python -m chapter6_demo.v12.verify_v12 $replay
python -m chapter6_demo.v12.analyze_v12 $replay
```

These commands evaluate recorded programs on CPU, without calling a model. The original verifier uses exact floating-point equality. For a new OS, use the [numeric contract and coordinate snapshot](../../../v12_1/NUMERIC_REPLAY.md); report all deviations and any decision differences. An offline replay cannot predict new stochastic model responses.

## Limits

This is a small TSP mechanism screen, with three paired blocks per model. It establishes neither stable performance, the independent benefit of relational ranking, a deployable selector, nor doctoral novelty. Independent witness sampling, a full conditional relation graph, and independent selector training are absent. W was inherited in r2 and is explicitly disabled only in v1.2.1. The future 16-run design is not executed or pooled here.
