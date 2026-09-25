"""Offline checks for the public evidence calculations, separate from runtime tests."""
import copy
import socket

import pytest

from chapter6_demo.providers import ModelClient
from chapter6_demo.v12_1.test_mechanism import finish, two_branches
from chapter6_demo.v12_2.runner import branch_exposure, plain
from chapter6_demo.v12_3.analyze import paired_rows, percentile_interval, selection_metrics, verify_exposure


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Evidence tests never call a model or a network.")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(ModelClient, "complete", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_pair_units_direction_and_missing_block_are_preserved():
    rows = []
    for block, f, r in [(3, .05, .04), (4, .05, None), (5, .02, .03)]:
        for controller, gap in [("niche_fixed_dev", f), ("relational_branch", r)]:
            rows.append(dict(block=block, controller=controller, test_gap=gap, tokens=None,
                             status="not_started" if gap is None else "search_complete_test_not_run"))
    pairs = paired_rows(rows, [3, 4, 5])
    assert len(pairs) == 3 and pairs[0]["delta_pp"] == pytest.approx(-1)
    assert pairs[1]["delta_pp"] is None and not pairs[1]["complete_pair"]
    assert pairs[2]["delta_pp"] == pytest.approx(1)


def test_bootstrap_uses_paired_differences_and_handles_insufficient_pairs():
    spec = {"bootstrap_seed": 9261203, "bootstrap_replicates": 20000}
    assert percentile_interval([.3], spec) is None
    assert percentile_interval([-.2] * 8, spec) == pytest.approx([-.2, -.2])
    interval = percentile_interval([-1, 0, 1, 1, 2, 0, 0, 0], spec)
    assert interval == percentile_interval([-1, 0, 1, 1, 2, 0, 0, 0], spec)
    assert interval[0] < interval[1]


def record_for(step):
    state = two_branches("relational_branch")
    seeds, before = copy.deepcopy(state.nodes), copy.deepcopy(state.branch_pool)
    choice = plain(copy.deepcopy(state.choose(step)))
    exposure = plain(branch_exposure(state, choice))
    child = finish(state, choice, 6, valid=False)
    record = dict(decision=choice, exposure=exposure, node=child,
                  event=plain(state.events[-1]), pool_before=before,
                  pool_after=plain(state.branch_pool))
    return seeds, record


def test_ranking_effect_requires_an_actual_development_slot():
    _, record = record_for(1)
    job = dict(job_id="synthetic", controller="relational_branch", data_block=-1)
    slot = selection_metrics(record, job)
    assert slot["actual_parent_id"] == 3 and slot["fifo_parent_id"] == 2
    assert slot["full_vs_gain_differs"] and slot["full_vs_fifo_differs"]
    assert slot["q_span_greater_than_gain_span"] and slot["weighted_gain_span"] == 0
    assert not slot["valid_child"] and not slot["different_choice_followed_by_parent_gain"]
    _, ordinary = record_for(0)
    slot = selection_metrics(ordinary, job)
    assert not slot["branch_slot"] and not slot["full_vs_gain_differs"]
    assert not slot["family_priority_varies"] and not slot["full_vs_fifo_differs"]


def test_exposure_verifier_rejects_a_tampered_counterfactual_choice():
    # Replay one ordinary step. The pool still has two entries, so its logged
    # counterfactual ranking is useful and must not silently be trusted.
    seeds, record = record_for(0)
    cp = dict(config=dict(controller="relational_branch", search_seed=0),
              seeds=seeds, records=[record])
    verify_exposure(cp)
    bad = copy.deepcopy(cp)
    bad["records"][0]["exposure"]["gain_only_parent_id"] = 999
    with pytest.raises(AssertionError):
        verify_exposure(bad)
