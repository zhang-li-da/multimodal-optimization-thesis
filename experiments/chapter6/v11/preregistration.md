# Chapter 6 v1.1 quality-protection factorial: preregistration

**Status:** frozen before any v1.1 live search calls. The exact source commit, source fingerprint, preregistration hash, ordered job manifest, and result status are recorded alongside the run.
**Question:** Do explicit parent/neighborhood improvement credit and a restart rule that recognizes recent productive local development improve multi-algorithm search outcomes, and does the effect survive an input+output token ceiling?

## Hypotheses

- **H1 (quality protection):** Within the shared seed quality envelope, giving credit to a candidate that improves its parent or nearest executed-behavior neighbor prevents productive same-mode development from being counted as an unproductive collision.
- **H2 (restart correction):** A strategy family with recent competitive local development is not declared saturated solely because recent candidates collide under the fixed probe descriptor.
- **H3 (cost robustness):** Any effect above appears under both a fixed number of proposal slots and a fixed total token ceiling; generated-candidate count, token consumption, and partial planner calls are reported.
- **H4 (algorithm-set use):** A selector trained only from validation-instance outcomes and public instance descriptors can use a quality-gated program set on held-out test instances. The test-instance oracle is reported only as an unattainable upper bound.

These are screening hypotheses. Five paired blocks per cell do not establish small effects or doctoral-level novelty.

## Factorial design

The four relational controller cells are `relational` (quality protection off, restart correction off), `relational_qp` (on/off), `relational_rr` (off/on), and `relational_qp_rr` (on/on). `niche` is an additional contextual baseline. Quality protection credits a candidate only if it improves its parent or nearest behavior neighbor while remaining inside the current best validation loss plus the pre-existing task tolerance. It removes the collision penalty only for a collision with such a recorded gain. Restart correction separately treats only nonproductive collisions as saturation evidence and prevents the no-growth restart trigger when the selected family has a competitive local improvement among its last two valid outcomes.

Tasks are TSP and online bin packing. The model cells are provider/model IDs `alibaba-token-plan-cn/qwen3.7-plus` and `minimax-cn-coding-plan/MiniMax-M3`. The new profile uses fixed generation rules but disjoint instance seeds from all pilot/confirmation splits: 3 families × 8 validation instances and 3 × 12 test instances per block; probe has 3 instances per family. Five independent data blocks (0–4) are paired across all controllers, budgets, and models. Candidate programs, usage, prompts, errors, and checkpoints are retained. Calls use the provider adapter's frozen temperature 0.7; provider-side stochastic seeds are not assumed to be controllable.

| Axis | Levels |
|---|---|
| Model | `alibaba-token-plan-cn/qwen3.7-plus`; `minimax-cn-coding-plan/MiniMax-M3` |
| Task | TSP; online bin packing |
| Controller | niche; relational 00, 10, 01, 11 |
| Resource regime | 8 proposal slots (same two-stage per-call output ceilings); 30,000 total input+output tokens, up to 32 proposals |
| Paired blocks | 5 new instance blocks; search seed equals block id |
| Total | 2 × 2 × 5 × 2 × 5 = 200 planned runs |

Each model provider is limited to one in-flight run at a time. The two providers may run concurrently. There is no automatic effect-based replacement of failed runs. API failures and unknown usage remain in status and invalidate strict token comparisons for that run. A planner call completed without enough reserved budget for its coder is retained as a partial paid proposal and consumes the budget.

## Outcomes and analysis

Run-level outcomes are validation-selected best test loss, number of behaviorally distinct test algorithms in the common validation-quality-gated archive, validation-gated archive size, generated valid proposals, proposal count, total input+output tokens, partial attempts, parent/neighborhood improvement counts, productive/unproductive collisions, restarts, and wall/CPU time. TSP gap and bin-pack volume-bound gap are analyzed separately.

The independent test-set quality gate is the best shared handwritten seed's test loss plus the task's frozen quality tolerance. It is used only in the post-search audit and never enters the prompts, archive, selector fit, or search decisions. For algorithm-set utility, validation outcomes train a standardized ridge loss predictor (alpha=10; fixed before search) on public per-instance features. It selects one archived rule per test instance. Compare against the single archive rule with the best validation mean. The test-instance oracle over the same archive is an upper bound only, not a deployable result. The test behavior-mode audit is descriptive and does not determine selector membership.

For each model/task/resource cell, report every paired-block value, means, medians, paired differences to niche, and 95% percentile bootstrap intervals over the five blocks. The 2×2 contrasts use paired block-level factorial main effects and interaction. No confirmatory p-value or broad stability claim is planned from this screening sample. Missing cost usage excludes a run from token-efficiency comparisons but not from descriptive failure reporting. The primary metric is validation-selected best test loss; the prespecified co-primary algorithm-set metric is selector test loss versus the validation-selected single archive rule. Both are reported by task and model.

## Exclusions and interpretation

- No model, task, block, controller, or resource regime is selected after inspecting its outcome.
- Test instances never influence search or selector training; oracle statistics are explicitly non-deployable.
- Repeated proposals within a search run are mechanism observations, not independent algorithm replicates.
- This design does not compare complete MLEvolve, SeaEvo, AdaEvolve, GEPA, or FunSearch systems.
- Positive screening results motivate a separately frozen confirmation; null or negative results are reported and narrow the claim.
