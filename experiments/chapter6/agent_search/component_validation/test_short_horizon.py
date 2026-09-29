import copy

import pytest

from .short_horizon_runner import ShortHorizonState, restore_short_horizon


def _node(node_id, loss, parent_id=None, valid=True):
    behavior = [[node_id % 3] for _ in range(4)]
    return {
        "id": node_id,
        "code": "def priority(f): return -f['distance']",
        "intent": "fixture",
        "tags": ["fixture"],
        "parent_id": parent_id,
        "evaluation": {
            "valid": valid, "loss": loss if valid else None,
            "behavior": behavior, "trajectory_behavior": behavior,
            "family_loss": {}, "trajectory_values": [], "failure_type": None,
        },
    }


def _checkpoint():
    incumbent = _node(0, 0.10)
    branch = _node(1, 0.12)
    return {
        "checkpoint_id": "fixture",
        "nodes": [incumbent, branch],
        "incumbent": {"id": 0, "loss": 0.10},
        "branch": {"id": 1, "loss": 0.12},
    }


def _step(state, loss, valid=True):
    decision = state.choose(len(state.decisions))
    parent = decision["parent"]["id"] if decision["parent"] else None
    node = _node(max(state.by_id) + 1, loss, parent, valid)
    event = state.observe(node, {"tokens_added": 10, "requests_added": 2})
    return decision, event


def test_action_parent_sequences_are_frozen():
    incumbent = ShortHorizonState(_checkpoint(), "C-I")
    branch = ShortHorizonState(_checkpoint(), "C-B")
    explore = ShortHorizonState(_checkpoint(), "C-E")
    assert incumbent.choose(0)["parent"]["id"] == 0
    assert branch.choose(0)["parent"]["id"] == 1
    assert explore.choose(0)["parent"] is None


def test_successful_exploration_becomes_development_project():
    state = ShortHorizonState(_checkpoint(), "C-E")
    first, event = _step(state, 0.11)
    assert first["action"] == "explore"
    assert event["parent_id"] is None
    assert state.exploration_root_id == event["node_id"]
    follow, _ = _step(state, 0.105)
    assert follow["action"] == "develop"
    assert follow["parent"]["id"] == event["node_id"]


def test_invalid_exploration_does_not_fabricate_parent_or_followup():
    state = ShortHorizonState(_checkpoint(), "C-E")
    first, event = _step(state, None, valid=False)
    assert first["parent"] is None
    assert event["parent_id"] is None
    assert state.exploration_root_id is None
    follow, _ = _step(state, 0.09)
    assert follow["action"] == "explore"
    assert follow["parent"] is None


def test_snapshot_restore_preserves_project_identity_and_parent():
    state = ShortHorizonState(_checkpoint(), "C-B")
    _step(state, 0.11)
    restored = restore_short_horizon(_checkpoint(), {"state": state.snapshot()})
    assert restored.snapshot() == state.snapshot()
    decision = restored.choose(1)
    assert decision["parent"]["id"] == state.project_best_id


def test_step_must_be_contiguous_and_parent_immutable():
    state = ShortHorizonState(_checkpoint(), "C-I")
    with pytest.raises(ValueError):
        state.choose(1)
    decision = state.choose(0)
    node = _node(3, 0.09, parent_id=1)
    with pytest.raises(ValueError):
        state.observe(node)
