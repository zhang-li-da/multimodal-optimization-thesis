# Chapter 6 v1.2 bounded branch development

This version tests the execution chain behind local improvement credit: a competitive child must be retained in a separate, small development pool and actually receive a bounded follow-up proposal. The output archive and development pool have separate roles.

The frozen r2 screen is complete. The mechanism trigger gate passed (30 branch follow-up evaluations, 30 valid children), but relation-guided scheduling did not outperform the matched `niche + fixed development` baseline. See the [technical report](results/screening-20260924-r2/TECHNICAL_REPORT_ZH.md), [architecture note](../../../docs/chapter6/V12_ARCHITECTURE.md), [manifest](results/screening-20260924-r2/manifest.json), and [release package instructions](results/screening-20260924-r2/README.md). The earlier r1 batch is excluded because the `niche` controller incorrectly created a development pool.

The preregistered design uses a 14-city TSP, three controllers (`niche`, `niche_fixed_dev`, and `relational_branch`), two coding-plan models, three paired blocks, and eight proposals per run. This gives 18 short screening runs. The strongest simple baseline is niche search with the same fixed local-development opportunity schedule. No full relation graph, witness sampling, or online-binpack selector is included. See [version history](VERSION_HISTORY.md) for the initial invalid batch and corrected source freeze.

Run invariant tests:

```powershell
python -m pytest experiments/chapter6/v12/test_v12.py experiments/chapter6/v11/test_v11.py -q
```

Verify the archived runs and regenerate the analysis/report (uses the recorded replay file and does not call an LLM):

```powershell
python experiments/chapter6/v12/verify_v12.py experiments/chapter6/v12/screening-20260924-r2
python experiments/chapter6/v12/analyze_v12.py experiments/chapter6/v12/screening-20260924-r2
```

The package under `results/screening-20260924-r2/` contains the frozen manifest, verification record, generated tables and figures, Chinese report, raw runs grouped by model, frozen source snapshot, and SHA-256 checksums. The r2 raw logs are archived there so the working run directory can remain excluded from ordinary source commits.

Create and execute a new stochastic replication in a fresh output directory (the archived r2 evidence is already complete):

```powershell
python -m chapter6_demo.v12.run_v12 --dry-run --output experiments/chapter6/v12/screening-replication-20260924
python -m chapter6_demo.v12.run_v12 --output experiments/chapter6/v12/screening-replication-20260924
```

Do not use the launcher's default output path for a new batch; it names the invalid r1 staging directory. The r2 configuration and result artifacts are fixed by the published manifest.

The v1.2 benchmark profile creates disjoint probe, validation, and test instances. Model-facing planner prompts omit controller identifiers and use a fixed evidence schema. The run manifest and per-run records retain treatment identity for analysis. API errors and incomplete usage are not silently replaced.

This is a mechanism screen, not a doctoral-level effectiveness confirmation. The protocol requires actual branch opportunities and evaluated follow-up children before interpreting any quality difference.
