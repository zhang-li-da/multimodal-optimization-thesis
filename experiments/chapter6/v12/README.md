# Chapter 6 v1.2 bounded branch development

This version tests the execution chain behind local improvement credit: a competitive child must be retained in a separate, small development pool and actually receive a bounded follow-up proposal. The output archive and development pool have separate roles.

The preregistered design uses a 14-city TSP, three controllers (`niche`, `niche_fixed_dev`, and `relational_branch`), two coding-plan models, three paired blocks, and eight proposals per run. This gives 18 short screening runs. The strongest simple baseline is niche search with the same fixed local-development opportunity schedule. No full relation graph, witness sampling, or online-binpack selector is included.

Run invariant tests:

```powershell
python -m pytest experiments/chapter6/v12/test_v12.py experiments/chapter6/v11/test_v11.py -q
```

Create and execute the frozen run list from a clean committed tree:

```powershell
python -m chapter6_demo.v12.run_v12 --dry-run
python -m chapter6_demo.v12.run_v12
```

The v1.2 benchmark profile creates disjoint probe, validation, and test instances. Model-facing planner prompts omit controller identifiers and use a fixed evidence schema. The run manifest and per-run records retain treatment identity for analysis. API errors and incomplete usage are not silently replaced.

This is a mechanism screen, not a doctoral-level effectiveness confirmation. The protocol requires actual branch opportunities and evaluated follow-up children before interpreting any quality difference.
