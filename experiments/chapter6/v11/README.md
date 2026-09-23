# Chapter 6 v1.1 quality-protection study

## Completed screening study

The preregistered 200-run factorial search and deterministic replay are complete. All 200 runs passed the replay checks. The full revised controller had lower mean test loss than `niche` in 0/8 model-task-budget cells; two paired bootstrap intervals were entirely in the higher-loss direction. Treat this as negative screening evidence against a stable advantage for this v1.1 controller, not as confirmation that the broader research direction is infeasible.

The run set contains 1,448 evaluated candidates and 4,861,576 recorded input/output tokens. Two provider timeouts left usage incomplete, and one malformed model response was retained. These outcomes were not replaced or rerun based on their results. The complete limits and interpretation are in the [Chinese technical report](results/screening-20260923-r1/TECHNICAL_REPORT_ZH.md).

- [Technical report](results/screening-20260923-r1/TECHNICAL_REPORT_ZH.md)
- [Analysis summary](results/screening-20260923-r1/REPORT.md)
- [Paired test-loss plot](results/screening-20260923-r1/paired_test_loss.png)
- [Factorial-effects plot](results/screening-20260923-r1/factorial_effects.png)
- [Selector-utility plot](results/screening-20260923-r1/selector_utility.png)
- [Run and archive manifest](results/screening-20260923-r1/EVIDENCE_MANIFEST.json)
- [SHA-256 checksums](results/screening-20260923-r1/SHA256SUMS.txt)
- [Qwen raw-run archive](results/screening-20260923-r1/qwen-raw-runs.zip)
- [MiniMax raw-run archive](results/screening-20260923-r1/minimax-raw-runs.zip)
- [Frozen preregistered source archive](results/screening-20260923-r1/preregistered-source.zip)

To replay the archived evidence without model API calls, extract both raw-run archives into the same run directory and extract `preregistered-source.zip` into a separate source directory. The source fingerprint includes frozen documentation, so the verifier must import the evaluator from that exact source snapshot. From the repository root, set `PYTHONPATH` to the extracted source directory and run:

```powershell
$env:PYTHONPATH = '<frozen-source-directory>'
python experiments/chapter6/v11/verify_screening.py <combined-run-directory>
```

This directory implements the next mechanism experiment from the thesis Chapter 6 review. It tests whether search should protect quality-improving development within a program branch or behavior neighborhood, and whether restart decisions should distinguish productive collisions from unproductive repetition.

`preregistration.md` fixes the hypotheses, factors, two tasks, model IDs, five paired blocks, data split sizes, and two resource regimes. `run_factorial.py` writes a manifest tied to a clean source commit and launches all 200 planned runs. Each provider is limited to one active run. Search candidates, evaluator outcomes, token usage, errors, and checkpoints are retained under the ignored `screening-*` run directory. `analyze_factorial.py` reports every planned block, paired contrasts, 2×2 effects, and descriptive percentile bootstrap intervals without confirmatory p-values.

The learned algorithm selector is fitted from validation outcomes and public TSP geometry features. Test outcomes are only used after predictions have been frozen; its test-instance oracle is labeled as a non-deployable upper bound. Online bin packing is reported without a selector because full-sequence features would reveal future arrivals. Quality protection also has a fixed per-family local-credit cap. The study is a screening experiment, not a replication of MLEvolve, SeaEvo, AdaEvolve, or a final confirmation of thesis-level novelty.

From the repository root, run the deterministic tests first:

```powershell
python -m pytest experiments/chapter6/demo/test_science.py experiments/chapter6/demo/test_classification.py experiments/chapter6/v11/test_v11.py -q
```

After committing the frozen protocol and source, launch the model runs with the OpenCode providers configured on that machine:

```powershell
python -m chapter6_demo.v11.run_factorial
```

Analyze the completed run set with:

```powershell
python -m chapter6_demo.v11.analyze_factorial experiments/chapter6/v11/screening-20260923-r1
```

The provider connectivity preflight in `preflight.json` is infrastructure-only and is excluded from the experimental outcomes.
