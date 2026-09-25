"""Acceptance checks for matrix revision and all-searches-before-test ordering."""
import socket
from types import SimpleNamespace

import pytest

from chapter6_demo.providers import ModelClient
from chapter6_demo.v12_2.common import read_json, save_json, source_record
from chapter6_demo.v12_2.data import planned_jobs
from chapter6_demo.v12_3 import study


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Offline acceptance tests cannot call models.")
    monkeypatch.setattr(ModelClient, "from_opencode", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)


def test_runtime_and_old_draft_bytes_are_preserved(tmp_path):
    reference = read_json(study.ROOT / "experiments/chapter6/v12_2/results/readiness-20260925/summary.json")
    assert source_record() == reference["source"]
    manifest = study.prepare(tmp_path / "draft")
    assert study.verify(tmp_path / "draft") == manifest
    assert len(manifest["jobs"]) == 16
    assert {j["model"] for j in manifest["jobs"]} == {"MiniMax-M3"}
    assert manifest["overlap_check"]["new_instances"] == 864
    assert manifest["overlap_check"]["id_or_exact_coordinate_collisions"] == 0
    for block in range(3, 7):
        for role in ("search", "test"):
            name = f"data/{role}-b{block}.json"
            assert (tmp_path / "draft" / name).read_bytes() == (study.OLD_DRAFT / name).read_bytes()
    with pytest.raises(ValueError, match="separately frozen"):
        study.verify(tmp_path / "draft", frozen=True)


def test_new_matrix_is_paired_and_has_no_qwen():
    protocol = read_json(study.PROTOCOL)
    jobs = planned_jobs(protocol)
    assert len({j["job_id"] for j in jobs}) == 16
    for block in range(3, 11):
        pair = [j for j in jobs if j["data_block"] == block]
        assert len(pair) == 2
        assert {j["controller"] for j in pair} == {"niche_fixed_dev", "relational_branch"}
        assert {j["search_seed"] for j in pair} == {block}
        assert all(j["provider"] == "minimax-cn-coding-plan" and j["steps"] == 8 for j in pair)


def test_test_dispatch_refuses_any_unfinished_search(tmp_path, monkeypatch):
    manifest = {"jobs": [{"job_id": "a"}, {"job_id": "b"}]}
    monkeypatch.setattr(study, "verify", lambda *a, **k: manifest)
    save_json(tmp_path / "runs/a/status.json", {"status": "search_complete_test_not_run"})
    called = []
    monkeypatch.setattr(study.subprocess, "run", lambda *a, **k: called.append(a))
    with pytest.raises(ValueError, match="Finish all"):
        study.test_all(tmp_path)
    assert called == []
    save_json(tmp_path / "runs/b/status.json", {"status": "infrastructure_incomplete"})
    study.test_all(tmp_path)
    assert len(called) == 1 and called[0][0][-1] == "a"


def test_two_infrastructure_failures_halt_and_no_terminal_job_is_retried(tmp_path, monkeypatch):
    root = tmp_path / "study"
    manifest = {"jobs": [{"job_id": str(i)} for i in range(3)]}
    monkeypatch.setattr(study, "verify", lambda *a, **k: manifest)
    save_json(tmp_path / "probe/summary.json", {"status": "passed", "returned_model": "MiniMax-M3"})
    called = []
    def failing_process(cmd, **kwargs):
        job_id = cmd[cmd.index("--job") + 1]
        called.append(job_id)
        save_json(root / "runs" / job_id / "status.json", {"status": "infrastructure_incomplete"})
        return SimpleNamespace(returncode=2)
    monkeypatch.setattr(study.subprocess, "run", failing_process)
    study.search_all(root, tmp_path / "probe")
    assert called == ["0", "1"]
    assert (root / "dispatch/halt.json").exists()
    assert not (root / "runs/2/status.json").exists()
