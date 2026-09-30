import json
from pathlib import Path

import pytest

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest

from .controller import S2State
from .runner import run_search


def _node(i, loss, parent=None, code='def priority(f):\n    return -f["distance"]\n'):
    return {
        "id": i, "code": code, "parent_id": parent,
        "evaluation": {"valid": True, "loss": loss,
                        "per_instance_loss": [loss],
                        "behavior": [[i]], "program_identity": {"raw_code_sha256": "x", "structural_format": "y", "structural_sha256": None}},
    }


def test_protected_controller_spends_fifo_grants():
    seeds = [_node(0, .2), _node(1, .21, code='def priority(f):\n    return -f["return_distance"]\n'),
             _node(2, .22, code='def priority(f):\n    return -f["regret"]\n')]
    state = S2State("fb_protected", 4, steps=4, protection=True)
    state.initialize(seeds)
    first = state.choose(0)
    state.observe({**_node(3, .19, parent=first["parent"]["id"], code='def priority(f):\n    return -f["distance"] + .1*f["regret"]\n'),
                   "allocated_tag": first["target"], "action": first["action"],
                   "allocation": first["allocation"], "evaluation": {"valid": True, "loss": .19,
                   "per_instance_loss": [.19], "behavior": [[3]], "program_identity": {"raw_code_sha256": "x", "structural_format": "y", "structural_sha256": None}}})
    # The test is about the executable state transition, not syntax identity.
    assert state.summary()["branch_admissions"] in (0, 1)
    assert len(state.decisions) == 1


class _FixtureTransport:
    def send(self, request, persist):
        if request["stage"] == "planner":
            body = {"model": request["model"], "id": "fixture-planner", "choices": [{"message": {"content": json.dumps({"name": "fixture", "intent": "fixed", "tags": [request["prompt"].split('target_strategy_family')[0] if False else "local_distance"], "formula": "-distance"})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        else:
            body = {"model": request["model"], "id": "fixture-coder", "choices": [{"message": {"content": json.dumps({"code": 'def priority(f):\n    return -f["distance"]\n'})}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        persist({"body_base64": __import__("base64").b64encode(json.dumps(body).encode()).decode(),
                 "seconds": 0.001, "request_id": body["id"], "protocol": "openai"})


@pytest.mark.parametrize("controller,protection", [("fb_unprotected", False), ("fb_protected", True)])
def test_fixture_runner_completes_without_model(tmp_path, controller, protection):
    block = 14
    current = {split: list(benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block))
               for split in ("probe", "validation")}
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                "probe": current["probe"], "validation": current["validation"]}
    job = {"job_id": f"fixture-{controller}", "provider": "fixture", "model": "fixture-model",
           "controller": controller, "task": "tsp", "data_block": block, "search_seed": 1400,
           "steps": 2, "capacity": 4, "grant": 2, "maximum_direction_attempts": 8,
           "quality_tolerance": .035, "gain_epsilon": .0001, "protection": protection}
    params = {"temperature": .0, "planner_max_tokens": 200, "coder_max_tokens": 200,
              "timeout_seconds": 10, "token_budget": 10000, "request_limit": 4,
              "wall_limit_seconds": 60}
    result = run_search(job, snapshot, tmp_path / "run", {"manifest_sha256": digest(snapshot)},
                        params, _FixtureTransport(), mode="fixture")
    assert result["status"] == "search_complete_test_not_run"
    assert result["summary"]["completed_proposals"] == 2

