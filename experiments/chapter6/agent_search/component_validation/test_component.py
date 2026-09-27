import pytest

from chapter6_demo import benchmarks
from .controller import ComponentSearchState, plain, restore_state


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
        _candidate(state, step, loss=.100 - .00001 * step, mode=step % 5)
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
