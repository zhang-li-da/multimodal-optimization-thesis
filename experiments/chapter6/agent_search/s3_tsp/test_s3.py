import base64
import json

import pytest

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest

from .controller import SearchState, restore_state
from .runner import run_search
from . import study as study_module


def _behavior(mode):
    row = [int(index == mode) for index in range(5)]
    return [row[:] for _ in range(4)]


def _node(node_id, loss, mode, parent=None, code='def priority(f):\n    return -f["distance"]\n'):
    return {
        "id": node_id, "code": code, "parent_id": parent,
        "intent": "fixture", "tags": ["local_distance"],
        "evaluation": {"valid": True, "loss": loss,
                       "per_instance_loss": [loss] * 12,
                       "behavior": _behavior(mode), "family_loss": {},
                       "trajectory_values": [], "failure_type": None,
                       "program_identity": {"raw_code_sha256": str(node_id),
                                            "structural_format": "fixture",
                                            "structural_sha256": str(node_id)}},
    }


def _seeds():
    return [_node(0, .100, 0), _node(1, .115, 1), _node(2, .120, 2)]


def _candidate(state, step, *, loss, mode, costs=None):
    decision = state.choose(step)
    node_id = len(state.nodes)
    node = _node(node_id, loss, mode,
                 parent=decision["parent"]["id"] if decision["parent"] else None)
    node.update(allocated_tag=decision["target"], action=decision["action"],
                allocation=decision["allocation"])
    event = state.observe(node, costs or {"tokens_added": 1000, "requests_added": 2,
                                          "complete_responses": 2, "truncations": 0})
    return decision, node, event


def test_parentless_exploration_is_admitted_and_gets_bounded_followup():
    state = SearchState("FB", 0, steps=8, protection=True)
    state.initialize(_seeds())
    assert state.pool == {}
    decision, candidate, event = _candidate(state, 0, loss=.120, mode=3)
    assert decision["action"] == "explore"
    assert decision["parent"] is None
    assert event["new_direction"]
    assert event["branch_entry_created"]
    assert event["protection_grant_awarded"] == 2
    assert state.pool[event["direction_id"]]["node_id"] == candidate["id"]

    next_decision, _, child_event = _candidate(state, 1, loss=.115, mode=3)
    assert next_decision["allocation"]["protected"]
    assert next_decision["parent"]["id"] == candidate["id"]
    assert child_event["protected_parent_was_behind_global"]
    assert child_event["local_improvement"]
    assert child_event["protection_grant_awarded"] == 2
    assert state.ledgers[event["lineage_id"]]["grant_awarded"] == 4


def test_unprotected_arm_tracks_same_entry_but_does_not_force_it():
    state = SearchState("FB", 0, steps=8, protection=False)
    state.initialize(_seeds())
    _, candidate, event = _candidate(state, 0, loss=.120, mode=3)
    assert event["branch_entry_created"]
    assert event["protection_grant_awarded"] == 0
    assert state.pool[event["direction_id"]]["node_id"] == candidate["id"]
    decision = state.choose(1)
    assert not decision["allocation"]["protected"]
    assert decision["action"] in ("explore", "develop")


def test_pool_recycles_exhausted_entries_but_never_evicts_unspent_protection():
    unprotected = SearchState("FB", 0, steps=8, capacity=1, protection=False)
    unprotected.initialize(_seeds())
    _, first, first_event = _candidate(unprotected, 0, loss=.120, mode=3)
    _, _, second_event = _candidate(unprotected, 1, loss=.120, mode=4)
    assert second_event["pool_evicted_direction_id"] == first_event["direction_id"]
    assert first_event["direction_id"] not in unprotected.pool
    assert second_event["direction_id"] in unprotected.pool

    protected = SearchState("FB", 0, steps=8, capacity=1, protection=True)
    protected.initialize(_seeds())
    _, _, active_event = _candidate(protected, 0, loss=.120, mode=3)
    candidate = _node(len(protected.nodes), .120, 4)
    direction, _, _ = protected._register_direction(candidate, None)
    blocked = protected._admit(candidate, direction, "new_direction_trial", 0.0)
    assert blocked["reason"] == "pool_full"
    assert blocked["evicted_direction_id"] is None
    assert active_event["direction_id"] in protected.pool


def test_invalid_protected_proposal_spends_slot_but_not_valid_evaluation():
    state = SearchState("FB", 0, steps=8, protection=True)
    state.initialize(_seeds())
    _, candidate, _ = _candidate(state, 0, loss=.120, mode=3)
    decision = state.choose(1)
    invalid = {
        "id": len(state.nodes), "code": "", "parent_id": candidate["id"],
        "allocated_tag": decision["target"], "action": decision["action"],
        "allocation": decision["allocation"],
        "evaluation": {"valid": False, "loss": None, "behavior": [],
                       "per_instance_loss": [], "failure_type": "ProgramError"},
    }
    event = state.observe(invalid, {"tokens_added": 700, "requests_added": 1,
                                    "complete_responses": 1, "truncations": 1})
    summary = state.summary()
    assert event["protected_development"]
    assert summary["protected_slots_scheduled"] == 1
    assert summary["protected_slots_with_valid_program"] == 0
    assert summary["protected_slots_with_complete_output"] == 0
    assert summary["truncated_model_responses"] == 1
    assert event["scheduled_lineage_id"] == decision["allocation"]["lineage_id"]
    assert state.ledgers[decision["allocation"]["lineage_id"]]["proposal_slots_scheduled"] == 1


@pytest.mark.parametrize("policy", ["FB", "TS", "AD"])
def test_every_protected_strategy_uses_the_same_branch_admission_interface(policy):
    state = SearchState(policy, 0, steps=8, protection=True)
    state.initialize(_seeds())
    decision, candidate, event = _candidate(state, 0, loss=.120, mode=3)
    assert decision["action"] == "explore"
    assert event["branch_entry_created"] and event["protection_grant_awarded"] == 2
    next_decision = state.choose(1)
    assert next_decision["allocation"]["protected"]
    assert next_decision["parent"]["id"] == candidate["id"]


def test_direction_lineage_cap_does_not_reset_after_new_behavior():
    state = SearchState("FB", 0, steps=16, protection=True,
                        maximum_direction_attempts=4, grant=2)
    state.initialize(_seeds())
    _, first, first_event = _candidate(state, 0, loss=.120, mode=3)
    lineage = first_event["lineage_id"]
    for step, (loss, mode) in enumerate(((.115, 3), (.112, 3), (.111, 4), (.110, 4)), start=1):
        _candidate(state, step, loss=loss, mode=mode)
    assert state.ledgers[lineage]["grant_awarded"] <= 4
    assert sum(1 for e in state.events if e["protected_development"] and e["lineage_id"] == lineage) <= 4


@pytest.mark.parametrize("policy,expected_action", [
    ("SP", "develop"), ("WR", "explore"),
])
def test_endpoint_policies_have_fixed_action_semantics(policy, expected_action):
    state = SearchState(policy, 5, steps=4)
    state.initialize(_seeds())
    decision = state.choose(0)
    assert decision["action"] == expected_action
    assert (decision["parent"] is None) == (expected_action == "explore")


def test_fixed_and_time_schedules_have_preregistered_probability_endpoints():
    fb = SearchState("FB", 1, steps=32)
    ts = SearchState("TS", 1, steps=32)
    assert fb._p_develop(0)[0] == .5
    assert fb._p_develop(31)[0] == .5
    assert ts._p_develop(0)[0] == .15
    assert ts._p_develop(31)[0] == .85


def test_ad_uses_equal_length_global_gain_per_token_units():
    state = SearchState("AD", 9, steps=8, protection=False, adaptive_unit_steps=2)
    state.initialize(_seeds())
    _candidate(state, 0, loss=.090, mode=0)
    _candidate(state, 1, loss=.089, mode=0)
    _candidate(state, 2, loss=.087, mode=0)
    _candidate(state, 3, loss=.086, mode=0)
    assert {unit["action"] for unit in state.adaptive_units} == {"explore", "develop"}
    assert all(unit["proposal_slots"] == 2 for unit in state.adaptive_units)
    assert all(unit["tokens"] == 2000 for unit in state.adaptive_units)
    p_develop, means = state._ad_probability()
    assert means["explore"] is not None and means["develop"] is not None
    normalized = (means["develop"] - means["explore"]) / (
        .001 + abs(means["develop"]) + abs(means["explore"]))
    expected = .5 + .25 * max(-1.0, min(1.0, normalized))
    assert p_develop == pytest.approx(expected)
    assert .25 <= p_develop <= .75
    decision = state.choose(4)
    assert .25 <= decision["evidence"]["p_develop"] <= .75


def test_adaptive_units_exclude_forced_protection_slots():
    state = SearchState("AD", 0, steps=8, protection=True, grant=2)
    state.initialize(_seeds())
    _candidate(state, 0, loss=.120, mode=3)
    _candidate(state, 1, loss=.115, mode=3)
    assert not state.adaptive_units
    assert state.adaptive_unit["steps_used"] == 1
    assert state.adaptive_unit["ordinary_tokens"] == 1000


def test_replay_preserves_decisions_events_and_cost_accounting():
    state = SearchState("AD", 9, steps=4, protection=False)
    seeds = _seeds()
    state.initialize(seeds)
    decision, node, event = _candidate(state, 0, loss=.090, mode=0,
                                       costs={"tokens_added": 1500, "requests_added": 2,
                                              "complete_responses": 2, "truncations": 0})
    config = {"policy": "AD", "search_seed": 9, "steps": 4,
              "capacity": 6, "grant": 2, "maximum_direction_attempts": 8,
              "quality_tolerance": .035, "gain_epsilon": .0001,
              "protection": False, "behavior_radius": .08,
              "adaptive_unit_steps": 2}
    replayed = restore_state({"config": config, "seeds": seeds,
                              "records": [{"decision": decision, "node": node,
                                           "event": event,
                                           "costs": event["costs"]}]})
    assert replayed.snapshot() == state.snapshot()


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


def test_fixture_runner_records_completion_without_model_calls(tmp_path):
    block = 14
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, block)),
                "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, block))}
    job = {"job_id": "fixture-s3", "provider": "fixture", "model": "fixture-model",
           "arm_id": "FB_P", "policy": "FB", "protection": True, "role": "fixture",
           "task": "tsp", "data_block": block, "search_seed": 14000,
           "steps": 2, "capacity": 6, "grant": 2, "maximum_direction_attempts": 8,
           "quality_tolerance": .035, "gain_epsilon": .0001,
           "behavior_radius": .08, "adaptive_unit_steps": 2}
    params = {"temperature": 0.0, "planner_max_tokens": 200, "coder_max_tokens": 200,
              "timeout_seconds": 10, "token_budget": 100000, "request_limit": 4,
              "wall_limit_seconds": 60}
    result = run_search(job, snapshot, tmp_path / "run", {"manifest_sha256": digest(snapshot)},
                        params, _FixtureTransport(), mode="fixture")
    assert result["status"] == "search_complete_test_not_run"
    assert result["summary"]["completed_proposals"] == 2
    assert result["summary"]["complete_model_responses"] == 4
    assert result["summary"]["model_calls"] == 0


def test_frozen_job_matrix_is_block_paired_and_has_six_arms():
    protocol = json.loads((study_module.HERE / "protocol.final.json").read_text(encoding="utf-8"))
    planned = study_module.jobs(protocol)
    assert len(planned) == 48
    for block in protocol["blocks"]:
        rows = [job for job in planned if job["data_block"] == block]
        assert len(rows) == 6
        assert len({job["search_seed"] for job in rows}) == 1
        assert {job["arm_id"] for job in rows} == {arm["arm_id"] for arm in protocol["arms"]}


def test_test_split_is_not_verified_until_all_search_jobs_are_terminal(tmp_path, monkeypatch):
    calls = []
    manifest = {"jobs": [{"job_id": "pending"}]}
    monkeypatch.setattr(study_module, "verify",
                        lambda _study, **kwargs: calls.append(kwargs.get("roles")) or manifest)
    with pytest.raises(ValueError, match="not all terminal"):
        study_module.test_all(tmp_path)
    assert calls == [("search",)]
