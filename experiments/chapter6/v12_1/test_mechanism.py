"""Behavioral acceptance tests; socket and model access are forbidden."""
import copy
import json
import socket

import pytest

from chapter6_demo.discovery import planner_prompt
from chapter6_demo.providers import ModelClient
from chapter6_demo.v12_1_controller import V121SearchState
from chapter6_demo.v12.test_v12 import _node


@pytest.fixture(autouse=True)
def no_model_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline mechanism tests cannot call models/network.")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(ModelClient, "complete", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def node(idx, loss, behavior=None, parent=1, source="live_llm", tag="return_aware", **kwargs):
    value = _node(idx, loss, behavior or [[0, 1, 0]], parent=parent, source=source)
    value.update(tags=[tag], allocated_tag=tag, **kwargs)
    return value


def seeded(method):
    state = V121SearchState("tsp", method, 0)
    state.observe(node(0, .08, [[1, 0, 0]], parent=None, source="handwritten_seed"))
    state.observe(node(1, .12, parent=None, source="handwritten_seed"))
    return state


def two_branches(method, bad_tag="local_distance"):
    state = seeded(method)
    state.observe(node(2, .11, tag="local_distance"))
    state.observe(node(3, .11, [[0, 0, 1]], tag="return_aware"))
    # Equal local gain/use counts; observed family outcomes alone break ties.
    state.observe(node(4, .5, tag=bad_tag))
    state.observe(node(5, .6, tag=bad_tag))
    assert [b["node_id"] for b in state._available()] == [2, 3]
    return state


def finish(state, selection, idx, loss=.5, valid=True):
    child = node(idx, loss, parent=selection["parent"]["id"] if selection["parent"] else None,
                 tag=selection["target"], allocation=selection["audit"],
                 reference_id=selection["reference"]["id"] if selection["reference"] else None,
                 action=selection["action"])
    if not valid:
        child["evaluation"].update(valid=False, loss=1., per_instance_loss=[], behavior=[],
                                   trajectory_behavior=[], failure_type="ProgramError")
    state.observe(child)
    return child


def test_live_choose_uses_family_evidence_and_isolates_branch_factor():
    fixed = two_branches("niche_fixed_dev")
    relation = two_branches("relational_branch")
    left, right = fixed.choose(1), relation.choose(1)
    assert left["parent"]["id"] == 2
    assert right["parent"]["id"] == 3
    assert left["audit"]["available_branch_ids"] == [2, 3]
    assert left["audit"]["ordinary_decision"] == right["audit"]["ordinary_decision"]
    assert left["audit"]["family_statistics"] == right["audit"]["family_statistics"]
    assert left["audit"]["branch_scores"] == right["audit"]["branch_scores"]
    assert right["audit"]["branch_scores"]["3"] > right["audit"]["branch_scores"]["2"]
    swapped = two_branches("relational_branch", bad_tag="return_aware")
    assert swapped.choose(1)["parent"]["id"] == 2
    for selection in (left, right):
        prompt = planner_prompt("tsp", selection, 1)
        assert "niche_fixed_dev" not in prompt and "relational_branch" not in prompt
        assert json.loads(prompt)["allocation_evidence"] == {"decision_step": 1}


@pytest.mark.parametrize("step", [0, 2, 4, 6, 8, 14])
def test_matched_history_ordinary_slots_and_full_prompts_are_identical(step):
    fixed, relation = two_branches("niche_fixed_dev"), two_branches("relational_branch")
    # Inject a tempting reference to catch accidental W policy use.
    relation.W = [node(99, .001, [[1, 1, 1]])]
    left, right = fixed.choose(step), relation.choose(step)
    assert left == right
    assert planner_prompt("tsp", left, step) == planner_prompt("tsp", right, step)
    assert (right["reference"] or {}).get("id") != 99


def test_empty_pool_reserved_slot_is_identical_and_new_tag_does_not_force_restart():
    fixed, relation = seeded("niche_fixed_dev"), seeded("relational_branch")
    left, right = fixed.choose(3), relation.choose(3)
    assert left == right
    assert left["action"] == "refine" and left["parent"] is not None
    assert left["target"] == "cluster"


def test_fifo_exhausts_oldest_invalid_attempt_consumes_budget_and_cannot_double_charge():
    state = two_branches("niche_fixed_dev")
    for step, idx in [(1, 6), (3, 7)]:
        selection = state.choose(step)
        assert selection["parent"]["id"] == 2
        child = finish(state, selection, idx, valid=False)
        with pytest.raises(ValueError, match="Duplicate"):
            state.observe(child)
    assert state.choose(5)["parent"]["id"] == 3
    assert next(b for b in state.branch_pool if b["node_id"] == 2)["remaining"] == 0


def test_failed_depth_four_attempt_leaves_success_depth_three():
    state = seeded("niche_fixed_dev")
    state.observe(node(2, .11))
    finish(state, state.choose(1), 3, loss=.10)
    finish(state, state.choose(3), 4, valid=False)
    selection = state.choose(5)
    assert selection["parent"]["id"] == 3
    finish(state, selection, 5, loss=.09)
    finish(state, state.choose(7), 6, valid=False)
    last = state.choose(9)
    assert last["parent"]["id"] == 5
    finish(state, last, 7, loss=.10)
    assert state.events[-1]["branch_attempt_depth"] == 4
    assert state.events[-1]["branch_success_depth"] is None
    assert state.summary()["branch_development_max_attempt_depth"] == 4
    assert state.summary()["branch_development_max_success_depth"] == 3


def test_reproduction_does_not_renew_budget_even_when_parent_improves():
    state = two_branches("niche_fixed_dev")
    before = copy.deepcopy(state.branch_pool)
    state.observe(node(6, .11, parent=1, tag="local_distance"))
    assert state.events[-1]["parent_improved"]
    assert state.events[-1]["branch_classification"] == "known_rule_reproduction"
    assert state.branch_pool == before
    state.observe(node(7, .8, [[1, 1, 1]]))
    assert state.events[-1]["branch_classification"] == "low_quality_novel_behavior"
    assert not state.events[-1]["branch_admitted"]


def test_evicted_ancestry_is_preserved_and_niche_never_admits():
    state = seeded("relational_branch")
    for idx, loss, parent in [(2, .11, 1), (3, .095, 2), (4, .075, 3), (5, .05, 4)]:
        state.observe(node(idx, loss, parent=parent))
    assert len(state.branch_pool) == 3
    evicted = state.events[-1]["branch_evicted_id"]
    assert evicted in state.success_depths
    depth = state.success_depths[evicted]
    state.observe(node(6, .07, parent=evicted))
    assert state.events[-1]["branch_success_depth"] == depth + 1
    niche = seeded("niche")
    niche.observe(node(2, .11))
    assert niche.branch_pool == [] and niche.summary()["branch_admissions"] == 0


def test_allocation_must_match_chosen_parent_and_choice_cannot_be_overwritten():
    state = two_branches("niche_fixed_dev")
    selected = state.choose(1)
    with pytest.raises(ValueError, match="pending"):
        state.choose(3)
    with pytest.raises(ValueError, match="allocation"):
        state.observe(node(6, .10, parent=3, allocation=selected["audit"]))
