# Chapter 6 v1.2 bounded branch-development screening

**Status:** protocol and controller must be committed before model calls. The run manifest records the exact source fingerprint, benchmark split fingerprints, ordered job list, and result hashes.

## Research question

Under a fixed proposal budget, does an explicit, expiring branch pool turn competitive parent-relative improvements into actual follow-up development, and does relation-guided branch choice add value over `niche + fixed-count development`?

This is a mechanism screen. It does not test the full thesis method, establish broad performance, or claim novelty over MLEvolve, SeaEvo, AdaEvolve, FunSearch, or GEPA.

## Controllers

All arms use the same 14-city TSP task, three shared hand-written seeds, probe/validation/test splits, fixed eight generated-proposal slots, planner/coder models, temperature, and canonical prompt schema. Controller identifiers and irrelevant switches are not included in model-facing prompts.

1. `niche`: existing quality/behavior archive parent selection, without a development queue.
2. `niche_fixed_dev`: the same niche archive plus a FIFO development pool. This is the strong simple baseline. Every other proposal slot is reserved for one FIFO branch when available.
3. `relational_branch`: relation/tag feedback chooses a competitive branch on the same reserved slots. The branch selector combines the selected family posterior with the branch's parent-relative gain divided by one plus prior development attempts.

The two development arms share the same branch admission and capacity rules. A branch is admitted only if it is valid, improves its actual parent by more than `1e-4`, remains within `0.035` validation loss of the best observed program, and is not an exact observed-rule reproduction on both probe behavior and per-instance validation-loss vector. "Exact reproduction" is an operational finite-evidence definition, not semantic equivalence.

The output archive A remains quality/behavior filtered. The separate development pool B holds at most three candidates. Each candidate has two follow-up proposal opportunities; an opportunity is consumed when a proposal is evaluated, including invalid proposals. Expired entries cannot be renewed by rediscovery of an already observed signature. Overflow evicts the lowest gain-per-use entry, with deterministic age tie-breaking. Branch depth counts successive admitted parent-relative improvements.

Every valid candidate is classified into one mutually exclusive evidence category: `known_rule_reproduction`, `competitive_local_improvement`, `competitive_novel_behavior_without_parent_gain`, `low_quality_novel_behavior`, `low_quality_repeat`, `unproductive_repeat`, or `other_valid_candidate`; invalid outputs are `invalid_program`. These are finite-probe/validation audit labels, not claims about the full program semantics.

## Experimental design

| Axis | Frozen levels |
|---|---|
| Models | Qwen3.7-Plus; MiniMax-M3 |
| Task | Euclidean 14-city TSP with fixed 24-move 2-opt refinement |
| Controllers | `niche`; `niche_fixed_dev`; `relational_branch` |
| Blocks | 0, 1, 2 paired across controller/model; search seed equals block |
| Search budget | 8 generated proposal slots; no token cap; actual input/output tokens reported |
| Data per block | 12 probe, 36 validation, 60 test instances across uniform/clustered/grid families; all split seeds are disjoint from v1.1 and each other |
| Total | 3 controllers × 2 models × 3 blocks = 18 runs |

The main mechanism outcomes are branch admissions, scheduled branch opportunities, valid development attempts, parent-improving continuations, realized branch depth, and exact-reproduction rejection. Primary quality is validation-selected test gap relative to the exact optimum. Archive mode count and per-instance oracle are descriptive secondary outcomes. Test labels never affect search, parent selection, branch membership, or reporting archive.

The block is the unit of replication. Proposals and test instances within a run are not independent algorithm replicates. Report every block, paired differences, and descriptive bootstrap intervals only; do not use confirmatory significance language. API failure or incomplete usage remains in place and does not trigger result-based reruns. No performance conclusion is drawn if the branch mechanism fails its predeclared trigger checks.

## Mechanism gates before interpretation

The software fixture must demonstrate all of the following before model calls: a truly competitive parent-relative improvement enters B; its parent is selected for a later proposal; one actual attempt consumes exactly one continuation; an exact known-rule reproduction is classified and not admitted; a low-quality novel behavior is classified and not admitted; and the same fixed prompt schema omits controller identifiers.

For live runs, at least four branch-development opportunities across the two branch arms and at least one valid parent-relative child evaluation in each model are required to describe whether follow-up development occurred. If the trigger floor is not reached, the run is reported as an under-triggered implementation screen, not evidence for or against branch development's performance effect.

## Interpretation and exclusions

- No post-outcome replacement or rerun.
- The primary comparison for added relation information is `relational_branch` versus `niche_fixed_dev`.
- `niche` provides a no-development reference.
- The study cannot isolate all prompt-mediated model stochasticity; controller names are hidden and prompt fields are fixed, but parent, target family, and action are necessarily policy outputs.
- The small three-block design is only a feasibility/mechanism screen. A positive result requires a separate, larger frozen confirmation.
