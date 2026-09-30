import base64
import json

import pytest

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import read_json, save_json

from experiments.chapter6.agent_search.minimal_mechanism.phase_b_runner import (
    PhaseBState, _append_history_prompt, _check_request_budget, history_summary,
    run_phase_b,
)
from experiments.chapter6.agent_search.component_validation.e1_runner import BudgetStop
from chapter6_demo.v12_2.calls import IndeterminateCall


def _candidate(node_id, loss, *, parent_id=None, code="def priority(f):\n    return -f['distance']\n",
              hypothesis=None, valid=True, behavior=None):
    return {"id": node_id, "name": f"n{node_id}", "intent": "candidate intent",
            "strategy_hypothesis": hypothesis, "tags": ["local_distance"], "code": code,
            "source": "fixture", "parent_id": parent_id,
            "evaluation": {"valid": valid, "loss": loss,
                           "behavior": behavior if behavior is not None else [[1] + [0] * 90 for _ in range(12)],
                           "failure_type": None if valid else "invalid_program"}}


def _checkpoint(branch=True):
    nodes = [_candidate(0, .2), _candidate(1, .3)]
    return {"checkpoint_id": "b60-step08", "continuation": {"block": 60},
            "nodes": nodes, "incumbent": {"id": 0, "loss": .2},
            "branch": {"id": 1, "loss": .3} if branch else None}


def _hypothesis():
    return {"target_failure": "long edges are chosen too early", "mechanism": "weight nearest edges",
            "expected_behavior_change": "prefer short edges",
            "falsifiable_prediction": "validation loss decreases"}


def test_history_summary_is_search_only_and_does_not_equate_tags_with_directions():
    summary = history_summary([_candidate(0, .2), _candidate(1, .4, valid=False)], .2)
    assert summary["test_data_access"] is False
    assert "directions" not in summary
    assert summary["quality_level"]["best_validation_loss"] == .2
    assert summary["observed_failures"][0]["node_id"] == 1


def test_g_and_e0_begin_with_same_parentless_action_but_only_g_sees_history():
    e0 = PhaseBState(_checkpoint(), "E0")
    eg = PhaseBState(_checkpoint(), "EG")
    e0_decision, eg_decision = e0.choose(0), eg.choose(0)
    assert e0_decision["action"] == eg_decision["action"] == "explore"
    assert e0_decision["parent"] is eg_decision["parent"] is None
    assert e0_decision["evidence"]["required_strategy_hypothesis"] == eg_decision["evidence"]["required_strategy_hypothesis"]
    assert "historical_search_summary" not in e0_decision["evidence"]
    assert eg_decision["evidence"]["historical_search_summary"]["quality_level"]["incumbent_validation_loss"] == .2
    assert set(eg_decision["evidence"]) - set(e0_decision["evidence"]) == {"historical_search_summary"}
    assert "historical_search_summary" not in e0_decision
    assert _append_history_prompt("same", e0_decision) == _append_history_prompt("same", eg_decision)


def test_project_admission_contract_is_shared_and_identity_is_stable():
    eg = PhaseBState(_checkpoint(), "EG")
    decision = eg.choose(0)
    node = _candidate(2, .18, hypothesis=None)
    eg.observe(node)
    assert eg.exploration_root_id is None
    assert eg.investment_id is None
    assert eg.events[-1]["exploration_admitted"] is False
    retry = eg.choose(1)
    assert retry["action"] == "explore"

    e0 = PhaseBState(_checkpoint(), "E0")
    e0.choose(0)
    e0.observe(_candidate(2, .21, hypothesis=None))
    assert e0.exploration_root_id is None
    assert e0.investment_id is None
    assert e0.events[-1]["exploration_admitted"] is False
    assert e0.choose(1)["action"] == "explore"

    e0 = PhaseBState(_checkpoint(), "E0")
    e0.choose(0)
    e0.observe(_candidate(2, .21, hypothesis=_hypothesis()))
    assert e0.exploration_root_id == 2
    identity = e0.investment_id
    e0.choose(1)
    e0.observe(_candidate(3, .19, parent_id=2,
                          behavior=[[0, 1] + [0] * 89 for _ in range(12)]))
    assert e0.investment_id == identity
    assert e0.project_best_id == 3
    assert e0.behavior_cell_id[2] != e0.behavior_cell_id[3]


def test_b_is_unavailable_without_a_frozen_eligible_branch():
    with pytest.raises(ValueError, match="branch-unavailable"):
        PhaseBState(_checkpoint(branch=False), "B")


class _FixtureTransport:
    def __init__(self):
        self.stages = []

    def send(self, request, persist):
        self.stages.append(request["stage"])
        if request["stage"] == "planner":
            payload = {"name": "fixture", "intent": "a testable route heuristic",
                       "tags": ["local_distance"], "formula": "-distance",
                       "strategy_hypothesis": _hypothesis()}
        else:
            payload = {"code": "def priority(f):\n    return -f['distance']\n"}
        body = {"model": request["model"], "id": f"fixture-{request['step']}-{request['stage']}",
                "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30}}
        persist({"body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
                 "seconds": .001, "request_id": body["id"], "protocol": "openai"})


def test_eg_runner_persists_context_cost_and_validation_prefixes_without_test(tmp_path):
    block = 60
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, block)),
                "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, block))}
    job = {"job_id": "fixture-eg", "strategy": "EG", "checkpoint_id": "b60-step08",
           "provider": "fixture", "model": "fixture-model",
           "data_block": block, "repetition": 0}
    parameters = {"temperature": 0.7, "planner_max_tokens": 200, "coder_max_tokens": 200,
                  "timeout_seconds": 10, "token_budget": 100000, "request_limit": 16,
                  "wall_limit_seconds": 60}
    transport = _FixtureTransport()
    result = run_phase_b(job, _checkpoint(), snapshot, tmp_path / "run",
                         {"manifest_sha256": "fixture"}, parameters, transport, mode="fixture")
    assert result["status"] == "continuation_complete"
    assert result["summary"]["completed_proposals"] == 8
    assert result["summary"]["request_count"] == 16
    assert result["summary"]["exploration_admissions"] == 1
    assert result["selections"]["4"]["prefix_proposals"] == 4
    assert result["selections"]["8"]["prefix_proposals"] == 8
    request = read_json(tmp_path / "run" / "calls" / "000-planner" / "request.json")
    assert "historical_search_summary" in request["prompt"]
    assert "test_access\": false" in json.dumps(result["config"])
    assert not (tmp_path / "run" / "test.json").exists()


def test_request_budget_reserves_prompt_output_and_provider_overhead_before_dispatch():
    class Calls:
        def __init__(self, usage):
            self.result = usage

        def usage(self):
            return self.result

    params = {"request_limit": 4, "token_budget": 700, "wall_limit_seconds": 60}
    started = __import__("time").perf_counter()
    with pytest.raises(BudgetStop, match="token_budget_reservation"):
        _check_request_budget(Calls({"call_attempts": 0, "known_tokens": 0,
                                     "usage_complete": True}),
                              params, started, "system", "prompt", 200)


def test_request_budget_refuses_to_continue_after_unknown_prior_usage():
    class Calls:
        def usage(self):
            return {"call_attempts": 1, "known_tokens": 10, "usage_complete": False}

    params = {"request_limit": 4, "token_budget": 100000, "wall_limit_seconds": 60}
    with pytest.raises(IndeterminateCall, match="usage is incomplete"):
        _check_request_budget(Calls(), params, __import__("time").perf_counter(),
                              "system", "prompt", 10)


def test_request_timeout_is_capped_by_remaining_task_wall_time():
    class Calls:
        def usage(self):
            return {"call_attempts": 0, "known_tokens": 0, "usage_complete": True}

    class Transport:
        deadline = None

        def set_wall_deadline(self, value):
            self.deadline = value

    now = __import__("time").perf_counter()
    started = now - 9
    transport = Transport()
    timeout = _check_request_budget(
        Calls(), {"request_limit": 4, "token_budget": 100000,
                  "wall_limit_seconds": 10, "timeout_seconds": 180},
        started, "system", "prompt", 10, transport)

    assert 0 < timeout <= 1
    assert transport.deadline == pytest.approx(started + 10)
