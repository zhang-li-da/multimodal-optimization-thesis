# Chapter 6 v1.1 quality-protection factorial: preregistration

**Status:** frozen before any v1.1 live search calls. The exact source commit, source fingerprint, preregistration hash, ordered job manifest, and result status are recorded alongside the run.
**Question:** Do explicit parent/neighborhood improvement credit and a restart rule that recognizes recent productive local development improve multi-algorithm search outcomes, and does the effect survive an input+output token ceiling?

## Hypotheses

- **H1 (quality protection):** Within the current global-best quality envelope, bounded credit for a candidate that improves its parent or nearest same-behavior neighbor protects productive development. Final archives use a common shared-seed quality envelope.
- **H2 (restart correction):** A strategy family with recent competitive local development is not declared saturated solely because recent candidates collide under the fixed probe descriptor.
- **H3 (cost robustness):** Any effect above appears under both a fixed number of proposal slots and a fixed total token ceiling; generated-candidate count, token consumption, and partial planner calls are reported.
- **H4 (algorithm-set use):** A selector trained only from validation-instance outcomes and public instance descriptors can use a quality-gated program set on held-out test instances. The test-instance oracle is reported only as an unattainable upper bound.

These are screening hypotheses. Five paired blocks per cell do not establish small effects or doctoral-level novelty.

## Factorial design

The four relational controller cells are `relational` (quality protection off, restart correction off), `relational_qp` (on/off), `relational_rr` (off/on), and `relational_qp_rr` (on/on). `niche` is an additional contextual baseline. Quality protection credits a candidate only if it improves its parent or nearest same-behavior neighbor by more than `1e-4` loss while remaining inside the current global best validation loss plus the pre-existing task tolerance. To bound local optimism, each strategy family may receive at most two such local credits before a global gain or non-collision competitive gain resets its credit count. Quality protection removes the collision penalty only for a collision with a recorded qualifying local gain. Restart correction separately treats only nonproductive collisions as saturation evidence and prevents the no-growth restart trigger when the selected family has a credited competitive local improvement among its last two valid outcomes. Probe-neighbor ties are broken by lower validation loss and then node ID.

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

Each provider executes its subsequence of the fixed shuffled job list in order. Failures are not automatically retried; interrupted in-flight requests remain flagged. Finished-search checkpoints permit report-only recovery without further model calls. Local-credit recency for restart correction additionally expires after two subsequent candidate outcomes, preventing indefinitely stale progress from blocking restart. Non-text model responses retain returned token usage and count as invalid proposals. Anthropic input accounting includes reported cache-read and cache-creation input tokens.

**Pre-search amendment:** the initial preparation commit `fb5bb6f` and its dry-run manifest produced no search calls. Before live search, review restricted the deployable selector to TSP, bounded local credits, fixed cache keys to include benchmark profile/block, and added request-interruption safeguards. The source commit in the actual run manifest supersedes that preparation commit. The two connectivity-only calls remain outside all outcomes.

## Outcomes and analysis

Run-level outcomes are validation-selected best test loss, number of behaviorally distinct test algorithms in the common validation-quality-gated archive, validation-gated archive size, generated valid proposals, proposal count, total input+output tokens, partial attempts, parent/neighborhood improvement counts, productive/unproductive collisions, restarts, and wall/CPU time. TSP gap and bin-pack volume-bound gap are analyzed separately.

The independent test-set quality gate is the best shared handwritten seed's test loss plus the task's frozen quality tolerance. It is used only in the post-search audit and never enters the prompts, archive, selector fit, or search decisions. For TSP algorithm-set utility, validation outcomes train a standardized ridge loss predictor (alpha=10; fixed before search) on public coordinate-geometry features. The frozen selector ranks archive algorithms before any test outcome is read. Compare against the single archive rule with the best validation mean and report selector feature/prediction time per test instance. Invalid test execution receives the prespecified loss penalty of 1.0. The test-instance oracle over the same archive is an upper bound only, not a deployable result. For online bin packing, no full-sequence selector is evaluated because it would expose future arrivals; report the best validation-selected algorithm and archive oracle upper bound only. The test behavior-mode audit is descriptive and does not determine selector membership.

For each model/task/resource cell, report every paired-block value, means, medians, paired differences to niche, and 95% percentile bootstrap intervals over the five blocks. The 2×2 contrasts use paired block-level factorial main effects and interaction. No confirmatory p-value or broad stability claim is planned from this screening sample. Missing cost usage excludes a run from token-efficiency comparisons but not from descriptive failure reporting. The primary metric for both tasks is validation-selected best test loss; TSP additionally reports the prespecified co-primary algorithm-set metric, selector test loss versus the validation-selected single archive rule. Interval endpoints use the full percentile distribution of the 5^5 paired bootstrap resamples.

## Exclusions and interpretation

- No model, task, block, controller, or resource regime is selected after inspecting its outcome.
- Test instances never influence search or selector training; oracle statistics are explicitly non-deployable.
- Repeated proposals within a search run are mechanism observations, not independent algorithm replicates.
- This design does not compare complete MLEvolve, SeaEvo, AdaEvolve, GEPA, or FunSearch systems.
- Positive screening results motivate a separately frozen confirmation; null or negative results are reported and narrow the claim.
