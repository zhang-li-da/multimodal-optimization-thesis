import base64
import json

import pytest

from chapter6_demo import benchmarks
from .controller import ComponentSearchState, plain, restore_state
from .runner import run_search
from chapter6_demo.v12_2.common import digest


def _behavior(mode):
    row = [int(i == mode) for i in range(5)]
    return [row[:] for _ in range(4)]


def _node(node_id, loss, mode, parent=None):
    return {
        "id": node_id, "code": 'def priority(f):\n    return -f["distance"]\n',
        "parent_id": parent, "intent": "fixture", "tags": ["local_distance"],
        "evaluation": {"valid": True, "loss": loss, "per_instance_loss": [loss] * 12,
                        "behavior": _behavior(mode), "trajectory_behavior": _behavior(mode),
                        "family_loss": {}, "trajectory_values": [], "failure_type": None,
                        "program_identity": {"raw_code_sha256": str(node_id), "structural_format": "fixture", "structural_sha256": str(node_id)}},
    }


def _seeds():
    return [_node(0, .100, 0), _node(1, .115, 1), _node(2, .120, 2)]


def _candidate(state, step, *, loss, mode, parent=None):
    decision = state.choose(step)
    node_id = len(state.nodes)
    node = _node(node_id, loss, mode, parent=decision["parent"]["id"] if decision["parent"] else parent)
    node.update(allocated_tag=decision["target"], action=decision["action"], allocation=decision["allocation"])
    event = state.observe(node, {"tokens_added": 1000, "requests_added": 2,
                                 "complete_responses": 2, "truncations": 0})
    return decision, node, event


def _to_step(state, step):
    for current in range(len(state.decisions), step):
        _candidate(state, current, loss=.200, mode=0)


def _install_pool_entry(state, direction, *, node_id, loss, created_order,
                        remaining=1, protection_remaining=1, deadline_step=32):
    """Install a funded fixture entry without exercising model generation."""
    lineage = state.direction_lineage[direction]
    state.pool[direction] = {
        "direction_id": direction, "lineage_id": lineage, "node_id": node_id,
        "remaining": remaining, "protection_remaining": protection_remaining,
        "attempts": 0, "loss": loss, "created_order": created_order,
        "last_gain": 0.0, "last_admission_reason": "fixture",
        "deadline_step": deadline_step,
    }


def test_zero_credit_directions_never_enter_active_pool():
    state = ComponentSearchState("P00", 0, steps=32)
    state.initialize(_seeds())
    # A valid but non-competitive direction is remembered, not funded.
    _to_step(state, 6)
    decision = state.choose(6)
    node = _node(len(state.nodes), .300, 4,
                 decision["parent"]["id"] if decision["parent"] else None)
    node.update(allocated_tag=decision["target"], action=decision["action"], allocation=decision["allocation"])
    event = state.observe(node, {"tokens_added": 1000, "requests_added": 2, "complete_responses": 2, "truncations": 0})
    assert event["branch_entry_created"] is False
    assert state.pool == {}


def test_zero_credit_same_lineage_cannot_enter_or_evict_funded_entry():
    state = ComponentSearchState("P00", 0, steps=32, capacity=1)
    state.initialize(_seeds())
    _install_pool_entry(state, "D0000", node_id=0, loss=.10, created_order=0)
    # A new direction in the same lineage has exhausted its cumulative cap.
    direction = "D-unfunded"
    state.direction_lineage[direction] = state.direction_lineage["D0000"]
    state.ledgers[state.direction_lineage[direction]]["grant_awarded"] = state.maximum_direction_attempts
    result = state._admit(_node(99, .30, 4), direction, "new_direction_trial", 0.0)
    assert result["development_grant_awarded"] == 0
    assert result["zero_credit_pool_entry"] is True
    assert set(state.pool) == {"D0000"}


def test_budget_end_does_not_award_unredeemable_commitment():
    state = ComponentSearchState("P00", 0, steps=32)
    state.initialize(_seeds())
    # The interleaved schedule has its last branch slot at step 23.
    _to_step(state, 24)
    state.pool.clear()
    state.ledgers["L0000"]["grant_awarded"] = 0
    result = state._admit(_node(99, .11, 4), "D0000", "new_direction_trial", 0.0)
    assert result["development_grant_awarded"] == 0
    assert result["reason"] == "budget_window_exhausted"
    assert state.pool == {}


def test_ps_changes_branch_choice_from_quality_to_deadline():
    quality = ComponentSearchState("P00", 0, steps=32, capacity=2)
    deadline = ComponentSearchState("P10", 0, steps=32, capacity=2)
    for state in (quality, deadline):
        state.initialize(_seeds())
        _to_step(state, 3)
        state.pool.clear()
        _install_pool_entry(state, "D0000", node_id=0, loss=.10,
                            created_order=0, deadline_step=11)
        _install_pool_entry(state, "D0001", node_id=1, loss=.20,
                            created_order=1, deadline_step=3)
    quality_choice = quality.choose(3)["allocation"]["direction_id"]
    deadline_choice = deadline.choose(3)["allocation"]["direction_id"]
    assert quality_choice == "D0000"       # ordinary policy: best loss
    assert deadline_choice == "D0001"      # P-S policy: earliest deadline
    assert quality_choice != deadline_choice


def test_pe_blocks_eviction_of_unredeemed_entry():
    unprotected = ComponentSearchState("P00", 0, steps=32, capacity=2)
    protected = ComponentSearchState("P01", 0, steps=32, capacity=2)
    for state in (unprotected, protected):
        state.initialize(_seeds())
        _install_pool_entry(state, "D0000", node_id=0, loss=.10,
                            created_order=0, remaining=1)
        _install_pool_entry(state, "D0001", node_id=1, loss=.20,
                            created_order=1, remaining=1)
    admitted = unprotected._admit(_node(99, .15, 4), "D0002",
                                  "new_direction_trial", 0.0)
    refused = protected._admit(_node(99, .15, 4), "D0002",
                                "new_direction_trial", 0.0)
    assert admitted["evicted_direction_id"] == "D0001"
    assert set(unprotected.pool) == {"D0000", "D0002"}
    assert refused["reason"] == "pool_full_protected"
    assert set(protected.pool) == {"D0000", "D0001"}


def test_interleaved_slot_positions_expose_branch_opportunities_early():
    slots = ComponentSearchState.SLOT_TYPES
    assert len(slots) == 32
    assert slots.count("incumbent") == 20
    assert slots.count("explore") == 6
    assert slots.count("branch") == 6
    assert [i for i, kind in enumerate(slots) if kind == "branch"] == [3, 7, 11, 15, 19, 23]
    assert all(i < 24 for i, kind in enumerate(slots) if kind == "branch")


def test_trial_is_one_shot_and_behavior_mutation_does_not_reset_lineage_budget():
    state = ComponentSearchState("P11", 0, steps=32)
    state.initialize(_seeds())
    _to_step(state, 6)
    _, first, event = _candidate(state, 6, loss=.120, mode=3)
    assert event["branch_entry_created"] and state.pool
    direction, lineage = event["direction_id"], event["lineage_id"]
    # Branch slots are at the end of the fixed table. Consume the trial.
    _to_step(state, 26)
    for step in range(26, 32):
        if state.pool[direction]["remaining"] > 0:
            _candidate(state, step, loss=.121, mode=4)
            break
    awarded = state.ledgers[lineage]["grant_awarded"]
    assert awarded <= 1


def test_cross_behavior_progress_continues_same_investment_and_can_renew():
    state = ComponentSearchState("P11", 0, steps=32, capacity=2, grant=1)
    state.initialize(_seeds())
    # Step 6 is an exploration slot.  It creates the first funded project.
    _, first, trial = _candidate(state, 0, loss=.120, mode=3)
    assert trial["investment_id"] is not None
    investment_id = trial["investment_id"]
    assert trial["behavior_cell_id"] == trial["direction_id"]
    # Step 1 is an incumbent slot in this fixture, but it is still a child of
    # the current best only when explicitly supplied through the allocation.
    # Use the next branch slot after advancing with valid no-op candidates.
    _to_step(state, 3)
    _, child, event = _candidate(state, 3, loss=.110, mode=4)
    assert event["new_direction"] is True
    assert event["investment_id"] == investment_id
    assert event["investment_progress"] is True
    assert event["admission_reason"] == "direction_progression"
    assert event["development_grant_awarded"] == 1
    assert state.ledgers[event["lineage_id"]]["grant_awarded"] == 2
    assert state.investments[investment_id]["best_node_id"] == child["id"]


def test_behavior_change_without_project_progress_does_not_reset_trial():
    state = ComponentSearchState("P11", 0, steps=32, capacity=2, grant=1)
    state.initialize(_seeds())
    _, first, trial = _candidate(state, 0, loss=.120, mode=3)
    investment_id = trial["investment_id"]
    _to_step(state, 3)
    _, _, event = _candidate(state, 3, loss=.125, mode=4)
    assert event["investment_id"] == investment_id
    assert event["investment_progress"] is False
    assert event["admission_reason"] in {"no_investment_evidence", "not_eligible"}
    assert state.ledgers[event["lineage_id"]]["grant_awarded"] == 1


def test_snapshot_contains_separate_identity_ledgers():
    state = ComponentSearchState("P00", 0, steps=32)
    state.initialize(_seeds())
    snapshot = state.snapshot()
    assert snapshot["behavior_cell_id"]
    assert snapshot["node_investment_id"] == {}
    assert snapshot["direction_investment_id"] == {}
    assert snapshot["investments"] == {}


def test_eviction_factor_changes_only_eviction_eligibility():
    unprotected = ComponentSearchState("P00", 0, steps=32, capacity=2)
    protected = ComponentSearchState("P01", 0, steps=32, capacity=2)
    for state in (unprotected, protected):
        state.initialize(_seeds())
        _to_step(state, 6)
        _candidate(state, 6, loss=.120, mode=3)
        _candidate(state, 7, loss=.121, mode=4)
    assert set(unprotected.pool) == set(protected.pool)
    assert unprotected.eviction_protection is False
    assert protected.eviction_protection is True


def test_fixed_slot_table_and_factor_replay():
    state = ComponentSearchState("P10", 9, steps=32)
    state.initialize(_seeds())
    for step in range(32):
        decision, _, event = _candidate(state, step, loss=.100 - .00001 * step, mode=step % 5)
        assert event["slot_type"] == decision["evidence"]["slot_type"]
    assert [d["evidence"]["slot_type"] for d in state.decisions].count("incumbent") == 20
    assert [d["evidence"]["slot_type"] for d in state.decisions].count("explore") == 6
    assert [d["evidence"]["slot_type"] for d in state.decisions].count("branch") == 6
    checkpoint = {"config": {"policy": "P10", "search_seed": 9, "steps": 32,
                              "capacity": 2, "grant": 1, "maximum_direction_attempts": 4,
                              "quality_tolerance": .035, "gain_epsilon": .0001,
                              "behavior_radius": .08, "scheduling_priority": True,
                              "eviction_protection": False, "adaptive_unit_steps": 2},
                 "seeds": _seeds(), "records": []}
    # The replay path is exercised on a one-record state to keep the fixture compact.
    mini = ComponentSearchState("P10", 9, steps=32); mini.initialize(_seeds())
    decision, node, event = _candidate(mini, 0, loss=.099, mode=0)
    checkpoint["records"] = [{"decision": decision, "node": node, "event": event, "costs": event["costs"]}]
    replayed = restore_state(checkpoint)
    assert replayed.snapshot() == mini.snapshot()


class _FixtureTransport:
    def send(self, request, persist):
        if request["stage"] == "planner":
            content = json.dumps({"name": "fixture", "intent": "fixed",
                                  "tags": ["local_distance"], "formula": "-distance"})
        else:
            content = json.dumps({"code": 'def priority(f):\n    return -f["distance"]\n'})
        body = {"model": request["model"], "id": "fixture-" + request["stage"],
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        persist({"body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
                 "seconds": .001, "request_id": body["id"], "protocol": "openai"})


def test_component_runner_fixture_persists_fixed_factor_and_no_model_calls(tmp_path):
    block = 40
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, block)),
                "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, block))}
    job = {"job_id": "fixture-component", "provider": "fixture", "model": "fixture-model",
           "arm_id": "P11", "policy": "P11", "protection": True,
           "scheduling_priority": True, "eviction_protection": True,
           "role": "fixture", "task": "tsp", "data_block": block, "search_seed": 40000,
           "steps": 32, "capacity": 2, "grant": 1, "maximum_direction_attempts": 4,
           "quality_tolerance": .035, "gain_epsilon": .0001,
           "behavior_radius": .08, "adaptive_unit_steps": 2,
           "request_limit": 64, "token_budget": 100000, "wall_limit_seconds": 60}
    params = {"temperature": 0.0, "planner_max_tokens": 200, "coder_max_tokens": 200,
              "timeout_seconds": 10, "token_budget": 100000, "request_limit": 64,
              "wall_limit_seconds": 60}
    result = run_search(job, snapshot, tmp_path / "run", {"manifest_sha256": digest(snapshot)},
                        params, _FixtureTransport(), mode="fixture")
    assert result["summary"]["completed_proposals"] == 32
    assert result["summary"]["model_calls"] == 0
    assert result["summary"]["scheduling_priority"] is True
    assert result["summary"]["eviction_protection"] is True
