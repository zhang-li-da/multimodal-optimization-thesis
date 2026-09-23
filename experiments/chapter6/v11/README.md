# Chapter 6 v1.1 quality-protection study

This directory implements the next mechanism experiment from the thesis Chapter 6 review. It tests whether search should protect quality-improving development within a program branch or behavior neighborhood, and whether restart decisions should distinguish productive collisions from unproductive repetition.

`preregistration.md` fixes the hypotheses, factors, two tasks, model IDs, five paired blocks, data split sizes, and two resource regimes. `run_factorial.py` writes a manifest tied to a clean source commit and launches all 200 planned runs. Each provider is limited to one active run. Search candidates, evaluator outcomes, token usage, errors, and checkpoints are retained under the ignored `screening-*` run directory. `analyze_factorial.py` reports every planned block, paired contrasts, 2×2 effects, and descriptive percentile bootstrap intervals without confirmatory p-values.

The learned algorithm selector is fitted from validation outcomes and public instance features. Test outcomes are only used for final evaluation; its test-instance oracle is labeled as a non-deployable upper bound. The study is a screening experiment, not a replication of MLEvolve, SeaEvo, AdaEvolve, or a final confirmation of thesis-level novelty.

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
python -m chapter6_demo.v11.analyze_factorial experiments/chapter6/v11/screening-20260923
```

The provider connectivity preflight in `preflight.json` is infrastructure-only and is excluded from the experimental outcomes.
