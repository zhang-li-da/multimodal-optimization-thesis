"""Focused invariant tests for the v1.1 mechanism and selector."""
import copy
import json
import pytest

from chapter6_demo.benchmarks import INDEPENDENT_PROFILE, instances, split_fingerprint
from chapter6_demo.discovery import (SearchState, _restart_decision, _token_reservation,
                                    planner_prompt)
from chapter6_demo.v11.selector import fit_and_evaluate_selector
from chapter6_demo.v11.analyze_factorial import _bootstrap_interval, _summary_metric


def _node(idx, loss, behavior, parent=None):
    return {
        "id": idx, "name": f"n{idx}", "intent": "test rule", "tags": ["local_distance"],
        "allocated_tag": "local_distance", "code": 'def priority(f):\n    return -f["distance"]\n',
        "parent_id": parent, "reference_id": None, "action": "refine", "source": "unit_test",
        "evaluation": {
            "valid": True, "loss": loss, "per_instance_loss": [loss], "per_instance_value": [1],
            "family_loss": {"uniform": loss}, "behavior": [behavior],
            "trajectory_behavior": [behavior], "trajectory_values": [[1, loss]],
            "program_hash": str(idx), "ast_nodes": 2, "solutions": [[0, 1]],
            "failure_type": None, "features_called": 2, "local_checks": 0,
            "cpu_seconds": 0.0, "wall_seconds": 0.0,
        },
    }


def test_parent_gain_inside_quality_envelope_is_protected_only_when_factor_enabled():
    baseline = SearchState("tsp", "relational", 0)
    protected = SearchState("tsp", "relational_qp", 0)
    rows = [_node(0, .10, [1, 0, 0]), _node(1, .12, [1, 0, 0], parent=0),
            _node(2, .115, [1, 0, 0], parent=1)]
    for row in rows:
        baseline.observe(copy.deepcopy(row))
        protected.observe(copy.deepcopy(row))
    base_event, protected_event = baseline.events[-1], protected.events[-1]
    assert base_event["terminal_collision"]
    assert base_event["parent_improved"] and base_event["competitive_local_development"]
    assert base_event["improved"] is False
    assert base_event["unproductive_collision"] is False
    assert base_event["useful_gain"] is False
    assert protected_event["useful_gain"] is True
    assert protected_event["productive_collision"] is True


def test_local_credit_is_capped_per_allocated_family_until_a_global_or_novel_gain():
    state = SearchState("tsp", "relational_qp", 0)
    rows = [_node(0, .10, [1, 0, 0]), _node(1, .12, [1, 0, 0], parent=0),
            _node(2, .115, [1, 0, 0], parent=1), _node(3, .114, [1, 0, 0], parent=2),
            _node(4, .113, [1, 0, 0], parent=3)]
    for row in rows:
        state.observe(row)
    assert [event["local_credit_eligible"] for event in state.events[2:]] == [True, True, False]


def test_restart_correction_uses_recent_local_development_as_separate_factor():
    uncorrected = _restart_decision(3, 3, 3, 2, False, True)
    corrected = _restart_decision(3, 0, 3, 2, True, True)
    assert uncorrected["saturated"] and uncorrected["stalled"] and uncorrected["restart"]
    assert corrected["saturated"] is False  # productive local progress is not saturation evidence
    assert corrected["stalled"] is False and corrected["restart"] is False
    stale = _restart_decision(3, 3, 3, 2, True, False)
    assert stale["restart"] is True


def test_token_admission_reserves_input_bytes_and_full_output_cap():
    prompt = '{"task":"tsp","notes":"中文 heuristic"}'
    assert _token_reservation(prompt, 1800) >= len(prompt.encode("utf-8")) + 1800


def test_exact_block_bootstrap_and_unknown_usage_are_not_misreported_as_cost():
    assert _bootstrap_interval([.2, .2, .2, .2, .2]) == (.2, .2)
    incomplete = {"summary": {"tokens_used": 1234, "usage_complete": False}}
    assert _summary_metric(incomplete, "tokens_used") is None


def test_independent_block_splits_are_disjoint_and_deterministic(monkeypatch):
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE", INDEPENDENT_PROFILE)
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK", "4")
    instances.cache_clear()
    for task in ("tsp", "binpack"):
        splits = [instances(task, name) for name in ("probe", "validation", "test")]
        ids = [{x["id"] for x in part} for part in splits]
        assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
        assert [len(x) for x in splits] == [9, 24, 36]
        assert len({split_fingerprint(task, name) for name in ("probe", "validation", "test")}) == 3
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK", "3")
    assert instances("tsp", "test")[0]["id"] != splits[2][0]["id"]


def test_selector_uses_validation_archive_and_marks_oracle_as_upper_bound_only(monkeypatch):
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE", INDEPENDENT_PROFILE)
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK", "0")
    instances.cache_clear()
    train = instances("tsp", "validation")
    test = instances("tsp", "test")
    nodes = [_node(0, .10, [1, 0, 0]), _node(1, .11, [0, 1, 0])]
    nodes[0]["evaluation"]["per_instance_loss"] = [0.1] * len(train)
    nodes[0]["evaluation"]["loss"] = .10
    nodes[1]["evaluation"]["per_instance_loss"] = [0.11] * len(train)
    nodes[1]["evaluation"]["loss"] = .11
    test_results = {
        "0": {"valid": True, "per_instance_loss": [.2] * len(test)},
        "1": {"valid": True, "per_instance_loss": [.15] * len(test)},
    }
    output = fit_and_evaluate_selector({"nodes": nodes, "archive_ids": [0, 1], "test": test_results},
                                       "tsp", train, test)
    assert output["status"] == "ok"
    assert output["validation_instance_count"] == len(train)
    assert output["test_instance_count"] == len(test)
    assert output["test_instance_oracle_loss_upper_bound_only"] <= output["learned_selector_test_loss"]
    assert len(output["test_selected_ids"]) == len(test)
    assert output["selection_seconds_per_test_instance"] >= 0
    test_results["0"]["per_instance_loss"] = [.01]*len(test)
    test_results["1"]["per_instance_loss"] = [.99]*len(test)
    changed = fit_and_evaluate_selector({"nodes":nodes,"archive_ids":[0,1],"test":test_results},
                                        "tsp",train,test)
    assert changed["test_selected_ids"] == output["test_selected_ids"]


def test_online_binpack_selector_refuses_future_leaking_full_sequence_features(monkeypatch):
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE", INDEPENDENT_PROFILE)
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK", "0")
    instances.cache_clear()
    outcome = fit_and_evaluate_selector({"nodes": [], "archive_ids": [], "test": {}}, "binpack",
                                        instances("binpack", "validation"), instances("binpack", "test"))
    assert outcome["status"] == "not_applicable_online_task"


def test_paid_planner_budget_stop_and_finished_checkpoint_do_not_repeat_calls(monkeypatch, tmp_path):
    from chapter6_demo import discovery
    from chapter6_demo.providers import Completion
    class Client:
        calls=0
        def complete(self, *args, **kwargs):
            self.calls+=1
            return Completion('{"name":"test","tags":["tight_fit"]}',"fake-test",900,100,0.0,"fake")
    client=Client()
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE",INDEPENDENT_PROFILE)
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK","0")
    monkeypatch.setattr(discovery.ModelClient,"from_opencode",lambda *args,**kwargs:client)
    monkeypatch.setattr(discovery,"_token_reservation",lambda *args:1000)
    args=("binpack","niche",0,8,"fake","fake-test",tmp_path,1500)
    result=discovery.run_search(*args)
    assert client.calls==1
    assert result["summary"]["generated"]==0
    assert result["summary"]["partial_attempts"]==1
    assert result["summary"]["tokens_used"]==1000
    assert result["summary"]["token_budget_valid"]
    (tmp_path/"result.json").unlink()
    resumed=discovery.run_search(*args)
    assert client.calls==1 and resumed["summary"]["tokens_used"]==1000


def test_pending_request_is_not_silently_reissued(monkeypatch,tmp_path):
    from chapter6_demo.discovery import run_search
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE",INDEPENDENT_PROFILE)
    (tmp_path/"pending_call.json").write_text(json.dumps({"iteration":0}),encoding="utf-8")
    with pytest.raises(RuntimeError,match="unaccounted retry"):
        run_search("binpack","niche",0,8,"fake","fake-test",tmp_path,1500)
