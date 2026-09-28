import base64
import json
import pytest

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest

from .e1 import choose_checkpoint_nodes, continuation_jobs, reevaluate_history
from .e1_runner import ContinuationState, run_continuation
from .study_e1 import _acceptance_summary


def _node(node_id, loss, mode, parent=None):
    behavior = [[int(i == mode) for i in range(5)] for _ in range(4)]
    return {"id": node_id, "code": "def priority(f):\n    return -f['distance']\n",
            "parent_id": parent, "tags": ["local_distance"],
            "evaluation": {"valid": True, "loss": loss, "behavior": behavior,
                            "per_instance_loss": [loss] * 12}}


def _checkpoint():
    nodes = [_node(0, .100, 0), _node(1, .115, 1), _node(2, .140, 2)]
    return {"checkpoint_id": "b44-sp-s16", "nodes": nodes,
            "incumbent": {"id": 0, "loss": .100},
            "branch": {"id": 1, "loss": .115},
            "continuation": {"block": 44}, "config": {"steps": 4}}


def test_checkpoint_selection_requires_quality_close_behavior_different_branch():
    incumbent, branch = choose_checkpoint_nodes([_node(0, .100, 0), _node(1, .120, 1)])
    assert incumbent["id"] == 0
    assert branch["id"] == 1


def test_reevaluate_history_replaces_new_block_evaluation_without_changing_ids():
    source = {"e1_source_step": 1, "seeds": [_node(0, .1, 0)],
              "records": [{"node": _node(1, .2, 1)}]}
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": 44,
                "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, 44)),
                "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, 44))}
    nodes = reevaluate_history(source, snapshot)
    assert [node["id"] for node in nodes] == [0, 1]
    assert all(node["evaluation"]["data_snapshot_sha256"] == digest(snapshot) for node in nodes)
    assert all("source_evaluation" in node for node in nodes)


def test_e1_job_matrix_has_192_proposals():
    checkpoints = [{"checkpoint_id": f"b{block}-sp-s16", "continuation": {"block": block}}
                    for block in range(44, 48)]
    checkpoints += [{"checkpoint_id": f"b{block}-fb_p-s16", "continuation": {"block": block}}
                   for block in range(44, 48)]
    jobs = continuation_jobs(checkpoints)
    assert len(jobs) == 48
    assert sum(job["steps"] for job in jobs) == 192
    assert {job["strategy"] for job in jobs} == {"C-I", "C-B", "C-E"}


class _FixtureTransport:
    def send(self, request, persist):
        if request["stage"] == "planner":
            content = json.dumps({"name": "fixture", "intent": "continue", "tags": ["local_distance"]})
        else:
            content = json.dumps({"code": "def priority(f):\n    return -f['distance']\n"})
        body = {"model": request["model"], "id": f"fixture-{request['step']}-{request['stage']}",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        persist({"body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
                 "seconds": .001, "request_id": body["id"], "protocol": "openai"})


def test_fixture_continuation_persists_three_strategy_contract(tmp_path):
    checkpoint = _checkpoint()
    snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": 44,
                "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, 44)),
                "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, 44))}
    job = {"job_id": "fixture-cb-r0", "checkpoint_id": checkpoint["checkpoint_id"],
           "strategy": "C-B", "repetition": 0, "steps": 4,
           "provider": "fixture", "model": "fixture-model"}
    params = {"temperature": 0.0, "planner_max_tokens": 200, "coder_max_tokens": 200}
    result = run_continuation(job, checkpoint, snapshot, tmp_path / "run",
                              {"checkpoint_sha256": digest(checkpoint)}, params,
                              _FixtureTransport(), mode="fixture")
    assert result["status"] == "continuation_complete"
    assert result["summary"]["completed_proposals"] == 4
    assert result["summary"]["new_model_calls"] == 0
    assert result["summary"]["strategy"] == "C-B"


def test_e1_acceptance_requires_full_planner_coder_workload(tmp_path):
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({
        "status": "passed", "returned_model": "MiniMax-M3",
        "workload": {"planner_completed": 3, "coder_completed": 3},
    }), encoding="utf-8")
    result = _acceptance_summary(summary, expected_model="MiniMax-M3")
    assert result["planner_completed"] == 3
    assert result["coder_completed"] == 3

    summary.write_text(json.dumps({
        "status": "passed", "returned_model": "MiniMax-M3",
        "workload": {"planner_completed": 1, "coder_completed": 3},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="3\\+3"):
        _acceptance_summary(summary, expected_model="MiniMax-M3")


def test_e1_test_all_skips_infrastructure_incomplete_jobs(tmp_path, monkeypatch):
    from . import study_e1 as module

    study = tmp_path / "study"
    run = study / "runs" / "job-a"
    run.mkdir(parents=True)
    (run / "status.json").write_text(json.dumps({"status": "infrastructure_incomplete"}),
                                      encoding="utf-8")
    monkeypatch.setattr(module, "verify", lambda *args, **kwargs: {
        "manifest_sha256": "manifest", "jobs": [{"job_id": "job-a", "data_block": 44}],
        "data": [],
    })

    result = module.test_all(study)

    assert result["status"] == "test_complete"
    assert result["tested_jobs"] == 0
    assert not (study / "tests" / "job-a.json").exists()


def test_e1_verify_rejects_manifest_without_embedded_protocol(tmp_path, monkeypatch):
    from . import study_e1 as module

    study = tmp_path / "study"
    study.mkdir()
    manifest = {
        "schema": "chapter6-e1-same-state-study-v1",
        "status": "FROZEN_PENDING_EXECUTION",
        "protocol_path": "protocol.final.json",
        "protocol_sha256": "protocol-hash",
        "checkpoints": [], "data": [],
    }
    manifest["manifest_sha256"] = digest(manifest)
    (study / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "file_sha", lambda _: "protocol-hash")

    with pytest.raises(ValueError, match="does not embed"):
        module.verify(study, frozen=True)
