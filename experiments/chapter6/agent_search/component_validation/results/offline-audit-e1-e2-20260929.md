# E1/E2 Offline Claim Audit

This audit reads only the frozen E1 and E2 r3 study directories. It does not
contact MiniMax, change an historical archive, or use Test data to choose a
checkpoint. Re-run it with:

```powershell
python -m experiments.chapter6.agent_search.component_validation.audit_e1_e2_claims `
  --output experiments/chapter6/agent_search/component_validation/results/offline-audit-e1-e2-20260929.json
```

## E2 r3 branch continuity

The archive contains 32 runs and 1,024 controller records. Counting the event
fields gives 113 branch-development events, 96 children classified as new
directions, and 18 branch children that improved their selected parent. None of
those 18 events awarded a new protection or development grant. Their admission
reasons are `no_remaining_trial_or_renewal` (16), `budget_window_exhausted`
(1), and `not_eligible` (1). The reviewed count of 112 branch-development
events is therefore off by one; the 96/18/0 and 16/1/1 subdivisions reproduce
the reported findings.

The audit treats `branch_development` and `local_improvement` as event fields,
and treats a grant awarded on the same event as a renewal proxy. This is a
diagnostic audit, not a causal estimate of a different controller.

## E1 C-B validation-to-Test transfer

The C-B arm has 16 completed continuation jobs. Eight jobs contain at least one
validation `global_improvement`. For each job, the script evaluates the frozen
start incumbent on the frozen independent Test split and compares it with the
archived selected program's `primary_test_gap`. Among the eight validation-
improved jobs, five have a worse final Test gap than their start incumbent.

The five cases are not treated as independent blocks: the jobs are paired within
the four data blocks and two source arms, and the result is descriptive evidence
of validation-to-Test transfer risk.

## E1 checkpoint construction and test boundary

The reusable offline functions are in `e1.py`:

- `load_search_snapshot(block)` loads only probe and validation data.
- `load_test_snapshot(block)` loads the independent Test data.
- `build_checkpoint(...)` re-evaluates the immutable source prefix on the new
  block, then chooses the incumbent and a quality-close,
  behavior-different branch using frozen tolerances.
- `prepare_checkpoints(...)` builds the eight deterministic checkpoints.
- `write_checkpoint_set(...)` writes immutable checkpoint files.

The runner consumes a checkpoint's `nodes`, `incumbent`, `branch`, and
continuation snapshot; it does not read Test data during search. Test evaluation
is performed after terminal search status by the study's `test-all` path and
uses `evaluator.evaluate_test(code, snapshot)`. The audit script uses the same
function for the frozen start-incumbent baseline.

The checkpoint schema records `source`, `continuation`, `config`, `nodes`,
`source_records`, `incumbent`, `branch`, `selection_rule`, and
`selection_frozen`. For example, `b44-fb_p-s16.json` has incumbent node 16,
branch node 12, and continuation block 44.

