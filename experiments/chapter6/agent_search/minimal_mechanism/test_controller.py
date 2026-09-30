import json
from pathlib import Path

import pytest

from experiments.chapter6.agent_search.minimal_mechanism.controller import (
    ControllerConfig, MinimalMechanismController, fixed_baseline_action)


def _hypothesis(mechanism="local reranking"):
    return {"target_failure": "prior search misses useful edge ordering",
            "mechanism": mechanism,
            "expected_behavior_change": "change the selected edge pattern",
            "falsifiable_prediction": "validation loss falls by more than epsilon"}


def _record(node_id, loss, *, parent_id=None, cell="cell-a", fp=None, structure=None,
            hypothesis=None, valid=True):
    return {"node_id": str(node_id), "parent_id": parent_id,
            "validation_loss": loss, "loss": loss, "valid": valid,
            "behavior_cell_id": cell,
            "program_fingerprint": fp or f"program-{node_id}",
            "structure_fingerprint": structure or f"structure-{node_id}",
            "strategy_hypothesis": hypothesis or _hypothesis(),
            "failure_type": None if valid else "invalid_program"}


def _controller(*, budget=12, trial=2, renewal=1, lineage_cap=4,
                g=True, p=True, f=False, incumbent_loss=.10):
    seed = _record("seed", incumbent_loss, cell="cell-seed", fp="seed-fp", structure="seed-ast",
                   hypothesis=_hypothesis("seed heuristic"))
    config = ControllerConfig(proposal_budget=budget, pool_capacity=2,
                              trial_slots=trial, renewal_slots=renewal,
                              lineage_slot_cap=lineage_cap, exploration_period=8)
    return MinimalMechanismController(seed, config=config, use_g=g, use_p=p, use_f=f)


def _submit(state, step, mode, candidate, *, investment_id=None,
            frozen_branch=None, costs=None, parent_override=None):
    if mode == "B" and investment_id:
        frozen_branch = {"checkpoint_id": "fixture", "node_id": state.investments[investment_id]["best_node_id"],
                         "investment_id": investment_id,
                         "loss": state.investments[investment_id]["best_loss"]}
    decision = state.choose(step, forced_action=mode, frozen_branch=frozen_branch,
                            parent_override=parent_override)
    candidate = dict(candidate)
    candidate["parent_id"] = decision["parent_id"]
    event = state.observe(candidate, costs or {"input_tokens": 8, "output_tokens": 2,
                                               "requests": 2, "evals": 1})
    return decision, event


def _start_project(state, step=0, *, loss=.12, cell="cell-root"):
    _, event = _submit(state, step, "EG", _record(f"n{step}", loss, cell=cell,
        fp=f"fp-root-{step}", structure=f"ast-root-{step}"))
    investment_id = event["investment_id"]
    assert investment_id in state.investment_pool
    return investment_id


def test_g_changes_only_generation_context_and_charges_request_tokens():
    eg, e0 = _controller(g=True), _controller(g=False)
    eg_decision = eg.choose(0, forced_action="EG")
    e0_decision = e0.choose(0, forced_action="E0")
    assert eg_decision["parent_id"] is e0_decision["parent_id"] is None
    assert eg_decision["prompt_input"]["history"]["visibility"] == "search_and_validation_only"
    assert e0_decision["prompt_input"]["history"] is None
    schema = {"required": ["target_failure", "mechanism", "expected_behavior_change",
                            "falsifiable_prediction"],
              "candidate": "executable program with evaluator fingerprints and behavior evidence"}
    assert eg_decision["prompt_input"]["strategy_hypothesis_output_schema"] == schema
    assert e0_decision["prompt_input"]["strategy_hypothesis_output_schema"] == schema
    eg_prompt = dict(eg_decision["prompt_input"])
    e0_prompt = dict(e0_decision["prompt_input"])
    eg_prompt.pop("history")
    e0_prompt.pop("history")
    assert eg_prompt == e0_prompt
    outcome = _record("eg", .12, cell="cell-eg", fp="eg-fp", structure="eg-ast")
    eg_event = eg.observe(outcome, {"input_tokens": 30, "output_tokens": 9,
                                    "summary_input_tokens": 12, "summary_calls": 0,
                                    "summary_cpu_seconds": .002})
    assert eg_event["costs"]["total_tokens"] == 39
    assert eg_event["costs"]["summary_cpu_seconds"] == .002
    assert eg_event["component_effects"]["G"]["triggered"] is True


def test_four_phase_b_action_contracts_record_parentage():
    state = _controller()
    frozen_branch = _record("branch", .12, cell="branch-cell", fp="branch-fp",
                            structure="branch-ast")
    b, _ = _submit(state, 0, "B", _record("b-child", .119), frozen_branch=frozen_branch)
    assert b["parent_id"] == "branch"
    i, _ = _submit(state, 1, "I", _record("i-child", .099))
    assert i["parent_id"] == "seed"
    e0, _ = _submit(state, 2, "E0", _record("e0-child", .12))
    eg, _ = _submit(state, 3, "EG", _record("eg-child", .12))
    assert e0["parent_id"] is eg["parent_id"] is None
    assert e0["prompt_input"]["history"] is None
    assert eg["prompt_input"]["history"] is not None
    assert e0["prompt_input"]["strategy_hypothesis_output_schema"] == eg["prompt_input"]["strategy_hypothesis_output_schema"]
    assert [e["action"] for e in state.events if "component_effects" in e] == ["B", "I", "E0", "EG"]


def test_project_and_lineage_identity_survive_behavior_change_and_renewal():
    state = _controller()
    investment_id = _start_project(state)
    lineage_id = state.investments[investment_id]["lineage_id"]
    _submit(state, 1, "B", _record("worse", .121, cell="cell-worse",
             fp="worse-fp", structure="worse-ast"), investment_id=investment_id)
    _, improved = _submit(state, 2, "B", _record("better", .118, cell="cell-better",
             fp="better-fp", structure="better-ast"), investment_id=investment_id,
             parent_override="worse", costs={"input_tokens": 1, "output_tokens": 0})
    assert improved["investment_id"] == investment_id
    assert improved["lineage_id"] == lineage_id
    assert improved["local_project_gain"] == pytest.approx(.002)
    assert state.node_behavior_cell_id["better"] == "cell-better"
    assert state.node_investment_id["better"] == investment_id
    project = state.investments[investment_id]
    assert set(project["behavior_cells"]) == {"cell-root", "cell-worse", "cell-better"}
    assert project["renewal_count"] == 1
    assert investment_id in state.investment_pool
    assert state.lineages[lineage_id]["awarded_slots"] == 3
    maturity = state.project_reward_ledger[-1]
    assert maturity["local_gain"] == pytest.approx(.002)
    assert maturity["cost_tokens"] == 21  # exploration prompt plus two one-token branch proposals


def test_project_progress_uses_project_best_not_a_worse_parent():
    state = _controller()
    investment_id = _start_project(state)
    _submit(state, 1, "B", _record("worse", .125, cell="cell-2",
             fp="worse-fp", structure="worse-ast"), investment_id=investment_id)
    decision, event = _submit(state, 2, "B", _record("recovery", .119, cell="cell-3",
             fp="recovery-fp", structure="recovery-ast"), investment_id=investment_id,
             parent_override="worse")
    assert decision["parent_id"] == "worse"
    assert .125 - .119 > .12 - .119
    assert event["local_project_gain"] == pytest.approx(.001)
    assert state.investments[investment_id]["best_node_id"] == "recovery"


def test_duplicate_program_and_renaming_do_not_refresh_trial_or_lineage_budget():
    state = _controller()
    investment_id = _start_project(state)
    first_award = state.investments[investment_id]["trial_count"]
    lineage_id = state.investments[investment_id]["lineage_id"]
    renamed = _hypothesis("local reranking")
    renamed["name"] = "totally new direction label"
    _, event = _submit(state, 1, "B", _record("renamed-copy", .119, cell="cell-copy",
             fp="fp-root-0", structure="ast-root-0", hypothesis=renamed),
             investment_id=investment_id)
    assert event["duplicate_program"] is True
    assert event["investment_id"] == investment_id
    assert state.investments[investment_id]["trial_count"] == first_award == 1
    assert state.lineages[lineage_id]["awarded_slots"] == 2
    assert len(state.investment_pool) == 1
    renamed_code = _record("renamed-code", .118, cell="cell-renamed", fp="renamed-fp",
                           structure="ast-root-0", hypothesis=renamed)
    _, renamed_event = _submit(state, 2, "B", renamed_code, investment_id=investment_id)
    assert renamed_event["duplicate_program"] is False
    assert renamed_event["investment_id"] == investment_id
    assert state.investments[investment_id]["trial_count"] == 1


def test_exhausted_project_leaves_active_pool_but_remains_archived():
    state = _controller()
    investment_id = _start_project(state)
    _submit(state, 1, "B", _record("progress-1", .118, cell="cell-2",
             fp="p1", structure="s1"), investment_id=investment_id)
    _submit(state, 2, "B", _record("progress-2", .117, cell="cell-3",
             fp="p2", structure="s2"), investment_id=investment_id)
    assert investment_id in state.investment_pool
    # The renewal is spent without improving the project's previous best.
    _submit(state, 3, "B", _record("stalled", .118, cell="cell-4",
             fp="p3", structure="s3"), investment_id=investment_id)
    assert investment_id not in state.investment_pool
    assert state.investments[investment_id]["status"] == "archived"
    assert state.investments[investment_id]["exit_reason"] == "no_project_best_progress"
    assert state.reserved_proposals == 0


def test_no_future_budget_never_creates_an_unredeemable_commitment():
    state = _controller(budget=1, trial=1, renewal=1, lineage_cap=2)
    _, event = _submit(state, 0, "EG", _record("last", .12, cell="cell-last",
                            fp="last-fp", structure="last-ast"))
    assert event["investment_id"] is None
    assert state.investment_pool == {}
    assert state.reserved_proposals == 0
    archived = next(iter(state.investments.values()))
    assert archived["exit_reason"] == "no_redeemable_global_budget"
    assert state.direction_archive["cell-last"]["best_validation_loss"] == .12


def test_new_commitment_preserves_one_unreserved_global_proposal():
    state = _controller(budget=3, trial=2, renewal=1, lineage_cap=4)
    _submit(state, 0, "EG", _record("floor-root", .12, cell="floor-cell",
             fp="floor-fp", structure="floor-ast"))
    assert state.investment_pool == {}
    project = next(iter(state.investments.values()))
    assert project["exit_reason"] == "no_redeemable_global_budget"
    assert state.unreserved_future_proposals == 2
    assert state.reserved_proposals == 0


def test_renewal_requires_one_unreserved_proposal_after_its_commitment():
    state = _controller(budget=3, trial=1, renewal=1, lineage_cap=4,
                        incumbent_loss=.20)
    investment_id = _start_project(state, loss=.195)
    _, event = _submit(state, 1, "B", _record("floor-progress", .190,
        cell="floor-progress-cell", fp="floor-progress-fp",
        structure="floor-progress-ast"), investment_id=investment_id,
        costs={"input_tokens": 1, "output_tokens": 0})
    assert event["delayed_reward_matured"] is True
    renewal = next(item for item in reversed(state.events)
                   if item.get("event") == "renewal_decision")
    assert renewal["triggered"] is False
    assert renewal["reason"] == "no_redeemable_budget"
    assert investment_id not in state.investment_pool


def test_real_progress_can_reenter_same_project_without_resetting_lineage_cap():
    state = _controller(budget=16, trial=1, renewal=1, lineage_cap=5,
                        incumbent_loss=.20)
    investment_id = _start_project(state, loss=.195)
    lineage_id = state.investments[investment_id]["lineage_id"]
    _submit(state, 1, "B", _record("project-best", .190, cell="cell-2",
             fp="pb", structure="sb"), investment_id=investment_id)
    assert investment_id in state.investment_pool
    _submit(state, 2, "B", _record("stalled", .191, cell="cell-3",
             fp="stall", structure="stall-s"), investment_id=investment_id)
    assert investment_id not in state.investment_pool
    # The project best is also the current global incumbent, so I continues
    # that same investment. Its new best can reopen one bounded tranche.
    decision, event = _submit(state, 3, "I", _record("reentry", .189, cell="cell-4",
             fp="reentry-fp", structure="reentry-ast"))
    assert decision["parent_id"] == "project-best"
    assert event["investment_id"] == investment_id
    assert state.investments[investment_id]["lineage_id"] == lineage_id
    assert investment_id in state.investment_pool
    assert state.investments[investment_id]["renewal_count"] == 2
    assert state.lineages[lineage_id]["awarded_slots"] <= state.config.lineage_slot_cap


def test_sent_unknown_consumes_the_committed_slot_without_zero_reward_or_retry():
    state = _controller()
    investment_id = _start_project(state)
    state.choose(1, forced_action="B", frozen_branch={
        "checkpoint_id": "fixture", "node_id": state.investments[investment_id]["best_node_id"],
        "investment_id": investment_id, "loss": state.investments[investment_id]["best_loss"]})
    event = state.fail_pending(terminal_status="sent_unknown", costs={"input_tokens": 5,
                              "output_tokens": None, "requests": 1})
    assert event["terminal_status"] == "sent_unknown"
    assert state.proposals_used == 2
    assert state.global_reward_ledger[-1]["reward"] is None
    assert state.project_reward_ledger == []  # still-unmatured trial; missing outcome isn't zero
    assert state.investments[investment_id]["tranche_missing_outcomes"] == 1


def test_direction_merge_keeps_investment_and_lineage_identity():
    state = _controller()
    investment_id = _start_project(state)
    lineage_id = state.investments[investment_id]["lineage_id"]
    _submit(state, 1, "B", _record("child", .119, cell="cell-child",
             fp="child-fp", structure="child-ast"), investment_id=investment_id)
    state.merge_behavior_cells("cell-child", "cell-root", evidence="predeclared task-adapter rule")
    assert state.resolve_behavior_cell("cell-child") == "cell-root"
    assert state.node_behavior_cell_id["child"] == "cell-root"
    assert state.node_investment_id["child"] == investment_id
    assert state.investments[investment_id]["lineage_id"] == lineage_id
    assert state.investments[investment_id]["behavior_cells"] == ["cell-root"]


def test_fixed_baseline_is_32_16_16_for_64_proposals():
    actions = [fixed_baseline_action(i) for i in range(64)]
    assert actions.count("I") == 32
    assert actions.count("B") == 16
    assert actions.count("E0") == 16


def test_fixed_baseline_executes_i_i_b_e_slots_and_routes_unavailable_b_to_i():
    state = _controller(p=False, g=False, f=False)
    root, _ = _submit(state, 0, "E0", _record("archive-root", .12, cell="archive-cell",
                          fp="archive-fp", structure="archive-ast"))
    assert not state.investments and not state.investment_pool
    assert state.choose(1)["mode"] == "I"
    state.observe(_record("i-1", .099, cell="i-cell", fp="i-1-fp", structure="i-1-ast"))
    second = state.choose(2)
    assert second["mode"] == "B"
    assert second["parent_id"] == "seed"
    state.observe(_record("b-2", .098, cell="b-cell", fp="b-2-fp", structure="b-2-ast"))
    third = state.choose(3)
    assert third["mode"] == "E0"

    no_branch = _controller(p=False, g=False, f=False)
    _submit(no_branch, 0, "I", _record("i-0", .05, fp="i-0-fp", structure="i-0-ast"))
    _submit(no_branch, 1, "I", _record("i-1", .01, fp="i-1-fp", structure="i-1-ast"))
    unavailable = no_branch.choose(2)
    assert unavailable["mode"] == "I"


def test_summary_distinguishes_component_triggers_from_changed_actions():
    state = _controller()
    _start_project(state)
    investment_id = next(iter(state.investments))
    state.choose(1, forced_action="B", frozen_branch={
        "checkpoint_id": "fixture", "node_id": state.investments[investment_id]["best_node_id"],
        "investment_id": investment_id, "loss": state.investments[investment_id]["best_loss"]})
    state.observe(_record("child", .119), {"input_tokens": 8, "output_tokens": 2})
    counts = state.summary()["component_effect_counts"]
    assert counts["P"]["trial_admissions"] == 1
    assert counts["P"]["trial_admission_triggers"] == 1
    assert counts["P"]["trial_admission_grants"] == 1
    assert "renewal_triggers" in counts["P"]
    assert counts["G"]["triggered"] >= 1
    assert counts["G"]["request_context_changed"] >= 1
    assert counts["G"]["outer_action_change_applicable"] is False


def test_p_off_removes_project_trials_and_full_p_can_change_branch_commitment():
    full = _controller(trial=1, renewal=1, lineage_cap=4, p=True)
    ablated = _controller(trial=1, renewal=1, lineage_cap=4, p=False)
    full_id = _start_project(full)
    _submit(ablated, 0, "E0", _record("archive-root", .12, cell="cell-trial",
             fp="archive-fp", structure="archive-ast"))
    assert not ablated.investments
    assert not ablated.investment_pool
    assert ablated.reserved_proposals == 0
    _submit(full, 1, "B", _record("trial-progress", .118, cell="cell-trial",
             fp="trial-fp", structure="trial-ast"), investment_id=full_id,
             costs={"input_tokens": 1, "output_tokens": 0})
    _submit(ablated, 1, "I", _record("ablated-incumbent", .099, cell="i-cell",
             fp="ablated-i-fp", structure="ablated-i-ast"))
    assert full_id in full.investment_pool
    assert full.events[-1]["event"] == "renewal_decision"
    assert full.action_rate_history["E"] == [pytest.approx(.002 / 11)]
    assert full.action_rate_history["B"] == []
    full_decision = full.choose(2)
    ablated_decision = ablated.choose(2)
    assert full_decision["mode"] == "B" and full_decision["investment_id"] == full_id
    assert ablated_decision["mode"] == "B" and ablated_decision["investment_id"] is None
    assert full_decision["component_effects"]["P"]["one_step_masked_counterfactual_applies"] is True
    assert full_decision["component_effects"]["P"]["one_step_masked_outer_action_changed"] is False
    assert full_decision["component_effects"]["P"]["one_step_masked_parent_changed"] is False
    assert full_decision["component_effects"]["P"]["one_step_masked_investment_target_changed"] is True
    assert ablated_decision["component_effects"]["P"]["one_step_masked_counterfactual_applies"] is False
    assert not ablated.summary()["investments"]
    full_event = full.observe(_record("renewal-progress", .117),
                              {"input_tokens": 1, "output_tokens": 0})
    assert full_event["delayed_reward_matured"] is True
    assert full.action_rate_history["E"] == [pytest.approx(.002 / 11)]
    assert full.action_rate_history["B"] == [pytest.approx(.001)]


def test_p_reports_admission_eligibility_separately_from_funding():
    state = _controller(trial=2, renewal=1, lineage_cap=4)
    first = _start_project(state, 0, cell="cell-first")
    second = _start_project(state, 1, cell="cell-second")
    assert len(state.investment_pool) == 2

    _submit(state, 2, "E0", _record("unfunded-root", .12, cell="cell-third",
             fp="unfunded-fp", structure="unfunded-ast"))
    admission = [event for event in state.events
                 if event.get("event") == "project_admission_decision"][-1]
    assert admission["triggered"] is True
    assert admission["granted"] is False
    assert admission["reason"] == "active_pool_full"
    counts = state.summary()["component_effect_counts"]["P"]
    assert counts["trial_admission_triggers"] == 3
    assert counts["trial_admission_grants"] == 2
    assert counts["trial_admissions"] == 2
    assert first in state.investment_pool and second in state.investment_pool


def test_matured_e_processes_enter_opportunity_cost_without_self_comparison():
    state = _controller(trial=1, renewal=1, lineage_cap=4)
    current_id = _start_project(state)
    _, event = _submit(state, 1, "E0", _record("other-exploration", .12,
        cell="other-cell", fp="other-fp", structure="other-ast"))
    other_id = event["investment_id"]
    state.investments[other_id]["last_mature_e_roi"] = .5
    rate, source = state._alternative_roi(current_id)
    assert rate == .5
    assert source == "best_mature_alternative_roi"


def test_unknown_matured_exploration_yield_stays_unknown_to_f():
    state = _controller(trial=1, renewal=1, lineage_cap=4, f=True)
    investment_id = _start_project(state)
    project = state.investments[investment_id]
    decision = state.choose(1, forced_action="B", frozen_branch={
        "checkpoint_id": "fixture", "node_id": project["best_node_id"],
        "investment_id": investment_id, "loss": project["best_loss"]})
    assert decision["mode"] == "B"
    state.fail_pending(terminal_status="sent_unknown", costs={"requests": 2})
    maturity = state.project_reward_ledger[-1]
    assert maturity["action_family"] == "E"
    assert maturity["local_gain_per_token"] is None
    assert maturity["cost_tokens"] is None
    assert state.action_rate_history["E"] == []
    assert state.matured_action_rates == []
    state.action_rate_history["I"] = [.25]
    next_decision = state.choose(2)
    assert next_decision["mode"] == "I"


def test_exploration_floor_waits_until_the_eighth_proposal():
    state = _controller(budget=12, p=False, g=False, f=True)
    assert state.last_explore_step == 0
    for step in range(8):
        decision = state.choose(step)
        assert decision["mode"] == "I"
        state.observe(_record(f"interval-{step}", .10, cell=f"cell-{step}",
                             fp=f"interval-fp-{step}", structure=f"interval-ast-{step}"),
                     {"input_tokens": 1, "output_tokens": 0})
    forced = state.choose(8)
    assert forced["mode"] == "E0"


def test_live_commitments_preempt_i_and_redeem_all_awarded_slots_at_budget_end():
    state = _controller(budget=5, trial=2, renewal=1, lineage_cap=4,
                        p=True, g=False, f=True)
    investment_id = _start_project(state)
    state.action_rate_history["I"] = [.9]
    actions = []
    for step in range(1, 5):
        decision = state.choose(step)
        actions.append(decision["mode"])
        if decision["mode"] == "I":
            loss = .10
            fp, structure = f"i-{step}-fp", f"i-{step}-ast"
        else:
            loss = .121 - .001 * step
            fp, structure = f"b-{step}-fp", f"b-{step}-ast"
        state.observe(_record(f"budget-{step}", loss, fp=fp, structure=structure),
                     {"input_tokens": 1, "output_tokens": 0})

    commitment = state.investments[investment_id]["commitments"][0]
    assert actions == ["I", "I", "B", "B"]
    assert commitment["awarded_slots"] == 2
    assert commitment["redeemed_slots"] == 2
    assert commitment["forfeited_slots"] == 0
    assert state.reserved_proposals == 0
    assert state.proposals_used == state.config.proposal_budget
    assert state.summary()["commitment_accounting"] == {
        "awarded_slots": 2, "redeemed_slots": 2, "forfeited_slots": 0,
        "unresolved_slots": 0, "active_reserved_slots": 0,
        "lineage_reserved_slots": 0, "conservation_holds": True}


def test_f_off_still_redeems_commitments_before_spending_reserved_budget():
    state = _controller(budget=4, trial=2, renewal=1, lineage_cap=4, f=False)
    investment_id = _start_project(state)
    first = state.choose(1)
    assert first["mode"] == "I"
    state.observe(_record("unreserved-i", .10, fp="unreserved-fp",
                          structure="unreserved-ast"),
                  {"input_tokens": 1, "output_tokens": 0})

    assert state.choose(2)["mode"] == "B"
    state.observe(_record("committed-b-1", .119, fp="committed-one-fp",
                          structure="committed-one-ast"),
                  {"input_tokens": 1, "output_tokens": 0})
    final = state.choose(3)
    assert final["mode"] == "B"
    state.observe(_record("committed-b-2", .118, fp="committed-two-fp",
                          structure="committed-two-ast"),
                  {"input_tokens": 1, "output_tokens": 0})
    assert state.investments[investment_id]["commitments"][0]["redeemed_slots"] == 2
    assert state.reserved_proposals == 0


def test_early_stop_explicitly_forfeits_and_archives_unredeemed_slots():
    state = _controller(trial=3, renewal=1, lineage_cap=6)
    investment_id = _start_project(state)
    forfeitures = state.forfeit_unredeemed_commitments("frozen service pause")
    project = state.investments[investment_id]
    commitment = project["commitments"][0]
    assert len(forfeitures) == 1
    assert forfeitures[0]["maturity_created"] is False
    assert commitment["redeemed_slots"] == 0
    assert commitment["forfeited_slots"] == 3
    assert commitment["status"] == "forfeited"
    assert project["status"] == "archived"
    assert investment_id not in state.investment_pool
    assert state.reserved_proposals == 0
    assert state.summary()["commitment_accounting"]["unresolved_slots"] == 0


def test_partial_redemption_then_explicit_forfeiture_preserves_all_reserve_ledgers():
    state = _controller(trial=3, renewal=1, lineage_cap=6)
    investment_id = _start_project(state)
    project = state.investments[investment_id]
    state.choose(1, forced_action="B", frozen_branch={
        "checkpoint_id": "fixture", "node_id": project["best_node_id"],
        "investment_id": investment_id, "loss": project["best_loss"]})
    state.observe(_record("partial-trial", .119), {"input_tokens": 2, "output_tokens": 1})

    before = state.summary()["commitment_accounting"]
    assert before["awarded_slots"] == 3
    assert before["redeemed_slots"] == 1
    assert before["unresolved_slots"] == before["active_reserved_slots"] == 2
    assert before["unresolved_slots"] == before["lineage_reserved_slots"]
    assert before["conservation_holds"] is True

    state.forfeit_unredeemed_commitments("frozen service pause")
    after = state.summary()["commitment_accounting"]
    assert after == {"awarded_slots": 3, "redeemed_slots": 1, "forfeited_slots": 2,
                     "unresolved_slots": 0, "active_reserved_slots": 0,
                     "lineage_reserved_slots": 0, "conservation_holds": True}


def test_real_archived_trajectory_replay_preserves_project_identity_and_progress():
    demo = json.loads(Path(__file__).with_name("demo_real.json").read_text(encoding="utf-8"))
    assert demo["record_type"] == "real_archived_trajectory"
    assert demo["synthetic"] is False
    assert demo["source"]["git_commit"] == "6d491ac6391ccc2c7a00baa5e695fbe66e66f16a"
    assert demo["source"]["raw_archive_sha256"] == (
        "387d951ae7250eaf42b1e6a157b1a9145f3d7b00a8d1e41c64ac145320aa2c0f")
    assert demo["data_boundary"]["test_is_not_a_new_confirmation_observation"] is True

    seed_loss = demo["initial_comparison"]["incumbent_validation_loss"]
    seed = _record("archived-incumbent-unrecorded", seed_loss,
                   cell="archived-incumbent-cell-unrecorded",
                   fp="archived-incumbent-fingerprint-unrecorded",
                   structure="archived-incumbent-structure-unrecorded")
    state = MinimalMechanismController(seed, config=ControllerConfig(
        proposal_budget=8, pool_capacity=1, trial_slots=3, renewal_slots=1,
        lineage_slot_cap=4, quality_tolerance=.035), use_g=False, use_p=True, use_f=False)

    project_id = lineage_id = None
    replayed = []
    project_best_before = None
    incumbent_before = seed_loss
    for item in demo["trajectory"]:
        cell = json.dumps(item["behavior_cell"], ensure_ascii=True, separators=(",", ":"))
        candidate = _record(str(item["candidate_id"]), item["validation_loss"],
                            cell=cell, fp=item["program_hash"],
                            structure=f"structure-unavailable:{item['candidate_id']}")
        candidate["strategy_hypothesis"] = {}
        mode = "E0" if item["action"] == "explore" else "B"
        decision, event = _submit(state, item["step"], mode, candidate,
                                  investment_id=project_id, costs={"requests": 2})
        if item["action"] == "explore":
            project_id = event["investment_id"]
            lineage_id = event["lineage_id"]
        assert decision["parent_id"] == (str(item["parent_id"]) if item["parent_id"] is not None else None)
        assert event["node_id"] == str(item["candidate_id"])
        assert event["behavior_cell_id"] == cell
        assert state.nodes[event["node_id"]]["program_fingerprint"] == item["program_hash"]
        assert event["investment_id"] == project_id
        assert event["lineage_id"] == lineage_id
        expected_project_gain = (max(0.0, project_best_before - item["validation_loss"])
                                 if project_best_before is not None else 0.0)
        expected_global_gain = max(0.0, incumbent_before - item["validation_loss"])
        assert event["local_project_gain"] == pytest.approx(expected_project_gain)
        assert event["global_gain_credit"] == pytest.approx(expected_global_gain)
        assert event["costs"]["total_tokens"] is None
        if project_best_before is None or item["validation_loss"] < project_best_before:
            project_best_before = item["validation_loss"]
        if item["validation_loss"] < incumbent_before:
            incumbent_before = item["validation_loss"]
        replayed.append(event)

    assert [event["local_project_gain"] > 1e-4 for event in replayed] == [False, True, True, False]
    assert [event["global_gain_credit"] > 1e-4 for event in replayed] == [False, True, True, False]
    project = state.investments[project_id]
    assert project["best_node_id"] == str(demo["final_validation_selection_id"])
    assert project["best_loss"] == pytest.approx(0.04573976527348278)
    assert state.lineages[lineage_id]["spent_proposals"] == 4
    assert state.reserved_proposals == 0
    assert project["status"] == "archived"
    assert project["tokens_complete"] is False


def test_f_changes_action_only_when_matured_rates_change_the_choice():
    adaptive, fixed = _controller(f=True), _controller(f=False)
    adaptive_id = _start_project(adaptive)
    fixed_id = _start_project(fixed)
    adaptive.investments[adaptive_id]["last_mature_b_roi"] = .2
    fixed.investments[fixed_id]["last_mature_b_roi"] = .2
    adaptive.action_rate_history["I"] = [.1]
    fixed.action_rate_history["I"] = [.1]
    adaptive_decision = adaptive.choose(1)
    fixed_decision = fixed.choose(1)
    assert adaptive_decision["mode"] == "B"
    assert fixed_decision["mode"] == "I"
    assert adaptive_decision["component_effects"]["F"]["triggered"] is True
    assert adaptive_decision["component_effects"]["F"]["actual_action_changed"] is True


def test_renewal_loses_when_mature_project_roi_does_not_cover_opportunity_cost():
    state = _controller(trial=1, renewal=1, lineage_cap=4)
    investment_id = _start_project(state)
    state.action_rate_history["I"] = [.5]
    _submit(state, 1, "B", _record("small-progress", .119, cell="cell-2",
             fp="small-fp", structure="small-ast"), investment_id=investment_id,
             costs={"input_tokens": 1, "output_tokens": 0})
    assert investment_id not in state.investment_pool
    decision = [e for e in state.events if e.get("event") == "renewal_decision"][-1]
    assert decision["triggered"] is False
    assert decision["reason"] == "opportunity_cost_not_beaten_or_unmeasured"


def test_a_structural_hypothesis_change_opens_child_project_in_same_lineage():
    state = _controller(trial=2, renewal=1, lineage_cap=6)
    investment_id = _start_project(state)
    lineage_id = state.investments[investment_id]["lineage_id"]
    changed = _hypothesis("use regret rather than nearest-edge ordering")
    _, event = _submit(state, 1, "B", _record("strategy-fork", .119, cell="cell-fork",
             fp="fork-fp", structure="fork-ast", hypothesis=changed),
             investment_id=investment_id)
    child_id = event["investment_id"]
    assert child_id != investment_id
    assert state.investments[child_id]["forked_from_investment_id"] == investment_id
    assert state.investments[child_id]["lineage_id"] == lineage_id
    assert child_id in state.investment_pool
    assert state.lineages[lineage_id]["awarded_slots"] == 4
