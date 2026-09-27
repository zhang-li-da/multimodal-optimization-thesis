"""Regression checks for actual execution isolation and causal controls."""
import copy
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

import pytest

from chapter6_demo import benchmarks
from chapter6_demo.discovery import planner_prompt, coder_prompt
from chapter6_demo.v12_2.calls import ProviderFailure
from chapter6_demo.v12_2.common import read_json
from chapter6_demo.v12_2.data import evaluate_search as legacy_evaluate

from .evaluator import evaluate_search
from .controller import SearchState
from .runner import _brief_selection, run_search
from .test_s3 import _seeds, _candidate, _FixtureTransport
from . import study


def snapshot(block):
    return {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
            **{split: list(benchmarks._instances_cached("tsp", split,
                   benchmarks.V12_TSP_PROFILE, block))[:2] for split in ("probe", "validation")}}


def scientific(result):
    return {k: result.get(k) for k in ("valid", "loss", "per_instance_loss",
        "per_instance_value", "family_loss", "behavior", "trajectory_behavior",
        "trajectory_values", "solutions", "features_called", "instance_evaluations",
        "program_identity", "failure_type")}


def test_explicit_evaluator_matches_serial_legacy_and_isolates_threads():
    code = benchmarks.SEEDS["tsp"][1][2]
    snapshots = [snapshot(32), snapshot(33)]
    references = [scientific(legacy_evaluate(code, value)) for value in snapshots]
    original_loader = benchmarks.instances
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(i, pool.submit(evaluate_search, code, snapshots[i % 2])) for i in range(12)]
        for i, future in futures:
            result = future.result()
            assert scientific(result) == references[i % 2]
            assert result["probe_instance_ids"] == [x["id"] for x in snapshots[i % 2]["probe"]]
    assert benchmarks.instances is original_loader


def test_process_workers_match_serial_explicit_evaluator():
    code = benchmarks.SEEDS["tsp"][0][2]
    snapshots = [snapshot(34), snapshot(35)]
    expected = [scientific(evaluate_search(code, s)) for s in snapshots]
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(evaluate_search, code, s) for s in snapshots]
        assert [scientific(f.result()) for f in results] == expected


def test_unprotected_pool_really_supplies_a_parent():
    s = SearchState("FB", 0, steps=8, protection=False)
    s.initialize(_seeds())
    _, candidate, event = _candidate(s, 0, loss=.12, mode=3)
    assert s.pool[event["direction_id"]]["remaining"] == 2
    _candidate(s, 1, loss=.2, mode=4)
    d, _, e = _candidate(s, 2, loss=.11, mode=3)
    assert d["parent"]["id"] == candidate["id"]
    assert not d["allocation"]["protected"]
    assert e["branch_development"] and e["local_improvement"]


def test_protection_only_changes_scheduling_and_eviction_guarantee():
    states = [SearchState("FB", 0, steps=8, protection=p) for p in (False, True)]
    for s in states:
        s.initialize(_seeds())
        _candidate(s, 0, loss=.12, mode=3)
    left, right = states
    assert left.direction_members == right.direction_members
    assert left.ledgers == right.ledgers
    le, re = list(left.pool.values())[0], list(right.pool.values())[0]
    assert {k: v for k, v in le.items() if k != "protection_remaining"} == {
        k: v for k, v in re.items() if k != "protection_remaining"}
    assert left.choose(1)["action"] == "explore"
    d = right.choose(1)
    assert d["action"] == "develop" and d["parent"]["id"] == 3


def test_interrupted_ad_unit_excludes_protected_gain_and_tokens():
    s = SearchState("AD", 0, steps=12, protection=True, maximum_direction_attempts=2)
    s.initialize(_seeds())
    for i, (loss, mode) in enumerate([(.12, 3), (.08, 3), (.08, 3), (.08, 0)]):
        _candidate(s, i, loss=loss, mode=mode)
    unit = s.adaptive_units[0]
    assert unit["tokens"] == 2000 and unit["proposal_slots"] == 2
    assert unit["global_best_improvement"] == 0
    assert s.events[1]["protected_parent_was_behind_global"]
    assert s.events[1]["global_improvement"]


def test_same_routed_programs_make_identical_prompts_despite_private_evidence():
    s = SearchState("SP", 0, steps=2); s.initialize(_seeds())
    d = s.choose(0); other = copy.deepcopy(d)
    other.update(policy="AD", evidence={"controller": "different", "p_develop": .1})
    plan = {"intent": "same", "formula": "-distance"}
    assert planner_prompt("tsp", _brief_selection(d), 0) == planner_prompt("tsp", _brief_selection(other), 0)
    assert coder_prompt("tsp", plan, _brief_selection(d)) == coder_prompt("tsp", plan, _brief_selection(other))


class FailureTransport:
    def send(self, request, persist):
        raise ProviderFailure("fixture failure; never repeated")


@pytest.mark.parametrize("failure", ["budget", "provider"])
def test_terminal_failures_freeze_available_output_without_retry(tmp_path, failure):
    protocol = read_json(study.PROTOCOL)
    job = {**study.jobs(protocol)[0], "data_block": 32, "steps": 2}
    parameters = {"temperature": 0, "planner_max_tokens": 200, "coder_max_tokens": 200,
                  "timeout_seconds": 10, "token_budget": 100000,
                  "request_limit": 0 if failure == "budget" else 4, "wall_limit_seconds": 60}
    output = tmp_path / failure
    result = run_search(job, snapshot(32), output, {}, parameters,
                        FailureTransport() if failure == "provider" else _FixtureTransport(), mode="fixture")
    assert result["status"] == ("budget_exhausted" if failure == "budget" else "infrastructure_incomplete")
    assert result["summary"]["completed_proposals"] == 0
    assert result["summary"]["request_count"] == (failure == "provider")
    readout = read_json(output / "selection_frozen.json")
    assert readout["best_id"] == readout["seed_best_id"]
