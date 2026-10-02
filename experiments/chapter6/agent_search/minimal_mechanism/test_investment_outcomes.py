import copy

import pytest

from experiments.chapter6.agent_search.minimal_mechanism.investment_outcomes import (
    horizon_outcome, marginal_outcome, paired_summary,
)


def node(i, loss):
    return {"id": i, "evaluation": {"valid": True, "loss": loss}}


def fixture(losses, *, complete=True):
    cp = {"incumbent": {"id": 0, "loss": .1}, "nodes": [node(0, .1), node(1, .2)]}
    records = [{"node": node(i + 2, loss),
                "event": {"project_progress": i > 0,
                          "action": "explore" if i == 0 else "develop",
                          "exploration_admitted": i == 0},
                "usage_after": {"known_tokens": 1000 * (i + 1),
                                "call_attempts": 2 * (i + 1), "usage_complete": complete},
                "costs": {"evaluation_seconds": .1}} for i, loss in enumerate(losses)]
    return cp, records


def test_local_catchup_does_not_count_as_global_return():
    cp, records = fixture([.20, .18, .15, .12])
    result = horizon_outcome(cp, records, 4)
    assert result["project_progress_events"] == 3
    assert result["global_gain_pp"] == 0
    assert result["gain_pp_per_1000_tokens"] == 0
    assert result["first_global_improvement_proposal"] is None
    assert result["known_tokens"] == 4000  # includes discovery, not just three developments


def test_window_cannot_see_future_and_marginal_gain_is_not_double_credited():
    cp, records = fixture([.11, .10, .10, .10, .09, .08, .08, .08])
    early, late = [horizon_outcome(cp, records, h) for h in (4, 8)]
    assert early["best_id"] == 0
    assert early["global_gain_pp"] == 0
    assert late["global_gain_pp"] == pytest.approx(2)
    assert late["first_global_improvement_proposal"] == 5
    marginal = marginal_outcome(early, late)
    assert marginal["known_tokens"] == 4000
    assert marginal["global_gain_pp"] == pytest.approx(2)


def test_missing_window_and_unknown_cost_are_not_zero():
    cp, records = fixture([.09] * 4, complete=False)
    early, late = [horizon_outcome(cp, records, h) for h in (4, 8)]
    assert early["global_gain_pp"] == pytest.approx(1)
    assert early["gain_pp_per_1000_tokens"] is None
    assert late["status"] == "missing_prefix"
    assert late["global_gain_pp"] is None
    assert marginal_outcome(early, late)["status"] == "censored"


def test_paired_mean_weights_blocks_equally_and_retains_missing_rows():
    cp, records = fixture([.09] * 8)
    base = {str(h): horizon_outcome(cp, records, h) for h in (4, 8)}
    rows = []
    for block, repetition, b_gain in [(60, 0, 2), (60, 1, 2), (61, 0, 5), (61, 1, None)]:
        for strategy in ("I", "B", "E0", "EG"):
            windows = copy.deepcopy(base)
            for value in windows.values():
                value["global_gain_pp"] = b_gain if strategy == "B" else 1
                if strategy == "B" and b_gain is None:
                    value["status"] = "missing_prefix"
            rows.append({"checkpoint_id": f"b{block}-step08", "repetition": repetition,
                         "block": block, "strategy": strategy, "status": "continuation_complete",
                         "horizons": windows})
    result = paired_summary(rows)[0]
    assert result["complete_pairs"] == 3
    assert result["independent_blocks_observed"] == 2
    assert result["equal_block_mean_pp"] == 2.5  # not the three-pair mean of 2
    assert len(result["missing"]) == 1
