import json

import pytest

from chapter6_demo.v12_2.calls import response_envelope
from chapter6_demo.v12_2.common import digest, save_json
from experiments.chapter6.agent_search.minimal_mechanism import analyze_phase_b


def _job(block, checkpoint, strategy, repetition):
    return {"job_id": f"{strategy}-{block}-{checkpoint}-{repetition}",
            "data_block": block, "checkpoint_id": checkpoint,
            "strategy": strategy, "repetition": repetition}


def _row(job, prefix, gap):
    return {"kind": "continuation_prefix", "data_block": job["data_block"],
            "checkpoint_id": job["checkpoint_id"], "strategy": job["strategy"],
            "repetition": job["repetition"], "prefix_proposals": prefix,
            "test_gap_percent": gap}


def _released_test_gate(study, *, write_data_manifest=True):
    save_json(study / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [], "prefix_jobs": [], "protocol": {"stages": {"B": {}}}})
    test_data_manifest = {"data": []}
    test_data_manifest["manifest_sha256"] = digest(test_data_manifest)
    candidate_manifest = {"candidates": [],
        "test_data_manifest_sha256": test_data_manifest["manifest_sha256"]}
    candidate_manifest["test_candidate_manifest_sha256"] = digest(candidate_manifest)
    gate = {"released": True,
        "test_candidate_manifest_sha256": candidate_manifest["test_candidate_manifest_sha256"],
        "test_data_manifest_sha256": test_data_manifest["manifest_sha256"]}
    save_json(study / "test_candidate_manifest.json", candidate_manifest)
    if write_data_manifest:
        save_json(study / "test_data_manifest.json", test_data_manifest)
    save_json(study / "test_gate.json", gate)
    return candidate_manifest, test_data_manifest, gate


def _missing_task(study):
    job = _job(60, "b60-step08", "EG", 0)
    save_json(study / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [job], "prefix_jobs": [],
        "protocol": {"stages": {"B": {"max_tokens": 100}}}})
    return job, study / "runs" / job["job_id"]


def _durable_request(run_dir):
    request = {"step": 0, "stage": "planner", "provider": "fixture",
        "model": "fixture-model", "system": "system", "prompt": "prompt",
        "max_tokens": 32, "temperature": 0.0}
    request_sha = digest(request)
    save_json(run_dir / "calls" / "000-planner" / "request.json", request)
    return run_dir / "calls" / "000-planner", request_sha


def test_missing_terminal_and_unreleased_test_are_reported_without_zero_filling(tmp_path):
    save_json(tmp_path / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [_job(60, "b60-step08", "EG", 0)], "prefix_jobs": [],
        "protocol": {"stages": {"B": {"max_tokens": 100}}}})
    result = analyze_phase_b.analyze(tmp_path)
    assert result["status"] == "search_status_only_test_not_released"
    assert result["terminal_status_counts"] == {"missing": 1}
    assert result["missing_or_noncomplete_jobs"][0]["reason"] == "no_terminal_status"
    assert result["test_outcome_rows"] == []
    assert result["test_contrasts"] == {}


def test_released_test_gate_without_candidate_manifest_fails_analysis(tmp_path):
    save_json(tmp_path / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [], "prefix_jobs": [], "protocol": {"stages": {"B": {}}}})
    save_json(tmp_path / "test_gate.json", {"released": True})

    with pytest.raises(ValueError, match="missing its frozen candidate manifest"):
        analyze_phase_b.analyze(tmp_path)


def test_released_test_gate_without_test_data_manifest_fails_analysis(tmp_path):
    _released_test_gate(tmp_path, write_data_manifest=False)

    with pytest.raises(ValueError, match="missing its frozen Test data manifest"):
        analyze_phase_b.analyze(tmp_path)


@pytest.mark.parametrize("mismatch", ["candidate_digest", "gate_binding"])
def test_released_test_gate_rejects_candidate_manifest_hash_mismatch(tmp_path, mismatch):
    candidate, _, gate = _released_test_gate(tmp_path)
    if mismatch == "candidate_digest":
        candidate["candidates"].append({"candidate_id": "tampered"})
        save_json(tmp_path / "test_candidate_manifest.json", candidate)
    else:
        gate["test_candidate_manifest_sha256"] = "0" * 64
        save_json(tmp_path / "test_gate.json", gate)

    with pytest.raises(ValueError, match="test candidate/data manifest digest or gate binding differs"):
        analyze_phase_b.analyze(tmp_path)


@pytest.mark.parametrize("mismatch", ["data_digest", "gate_binding"])
def test_released_test_gate_rejects_test_data_manifest_hash_mismatch(tmp_path, mismatch):
    _, test_data, gate = _released_test_gate(tmp_path)
    if mismatch == "data_digest":
        test_data["data"].append({"path": "tampered.json", "sha256": "fixture"})
        save_json(tmp_path / "test_data_manifest.json", test_data)
    else:
        gate["test_data_manifest_sha256"] = "0" * 64
        save_json(tmp_path / "test_gate.json", gate)

    with pytest.raises(ValueError, match="test candidate/data manifest digest or gate binding differs"):
        analyze_phase_b.analyze(tmp_path)


def test_released_test_gate_rejects_candidate_to_test_data_manifest_mismatch(tmp_path):
    candidate, _, gate = _released_test_gate(tmp_path)
    candidate["test_data_manifest_sha256"] = "0" * 64
    candidate["test_candidate_manifest_sha256"] = digest({
        key: value for key, value in candidate.items()
        if key != "test_candidate_manifest_sha256"})
    gate["test_candidate_manifest_sha256"] = candidate["test_candidate_manifest_sha256"]
    save_json(tmp_path / "test_candidate_manifest.json", candidate)
    save_json(tmp_path / "test_gate.json", gate)

    with pytest.raises(ValueError, match="test candidate/data manifest digest or gate binding differs"):
        analyze_phase_b.analyze(tmp_path)


def test_sent_unknown_terminal_remains_missing_with_unknown_cost(tmp_path):
    job = _job(60, "b60-step08", "EG", 0)
    save_json(tmp_path / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [job], "prefix_jobs": [],
        "protocol": {"stages": {"B": {"max_tokens": 100}}}})
    terminal = tmp_path / "runs" / job["job_id"] / "terminal_status.json"
    save_json(terminal, {"status": "sent_unknown", "unknown_cost": True,
        "missing_outcome": True, "outcome_counted_as_zero": False})

    result = analyze_phase_b.analyze(tmp_path)

    assert result["terminal_status_counts"] == {"sent_unknown": 1}
    assert result["missing_or_noncomplete_jobs"][0]["unknown_cost"] is True
    assert result["missing_or_noncomplete_jobs"][0]["missing_outcome"] is True
    assert result["unknown_cost_job_ids"] == [job["job_id"]]
    assert result["test_outcome_rows"] == []


@pytest.mark.parametrize("response_storage", ["decoded", "raw_only"])
def test_missing_task_recovers_completed_call_usage_from_durable_logs(tmp_path, response_storage):
    job, run_dir = _missing_task(tmp_path)
    call_dir, request_sha = _durable_request(run_dir)
    if response_storage == "decoded":
        save_json(call_dir / "response.json", {"request_sha256": request_sha,
            "input_tokens": 23, "output_tokens": 11, "usage_complete": True,
            "returned_model": "fixture-returned"})
        save_json(call_dir / "state.json", {"status": "response_persisted",
            "request_sha256": request_sha})
        expected_status = "response_persisted"
    else:
        body = json.dumps({"model": "fixture-returned", "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 23, "completion_tokens": 11}}).encode("utf-8")
        envelope = response_envelope(body, seconds=0.1, request_id="fixture-request", protocol="openai")
        save_json(call_dir / "raw_response.json", {"request_sha256": request_sha,
            "envelope": envelope, "envelope_sha256": digest(envelope)})
        save_json(call_dir / "state.json", {"status": "sent_unknown",
            "request_sha256": request_sha, "sent_utc": "fixture-time"})
        expected_status = "response_recovered_from_raw"

    result = analyze_phase_b.analyze(tmp_path)
    row = result["search_cost_rows"][0]

    assert result["terminal_status_counts"] == {"missing": 1}
    assert row["job_id"] == job["job_id"]
    assert row["status"] == "missing"
    assert row["known_tokens"] == 34
    assert row["usage_complete"] is True
    assert row["request_count"] == 1
    assert row["call_status_counts"] == {expected_status: 1}
    assert row["unknown_cost"] is False
    assert row["missing_outcome"] is True
    assert result["missing_or_noncomplete_jobs"][0]["reason"] == "no_terminal_status"
    assert result["unknown_cost_job_ids"] == []
    assert result["test_outcome_rows"] == []


def test_missing_task_sent_unknown_call_stays_missing_and_is_not_retried(tmp_path):
    job, run_dir = _missing_task(tmp_path)
    call_dir, request_sha = _durable_request(run_dir)
    state = {"status": "sent_unknown", "request_sha256": request_sha,
        "sent_utc": "fixture-time"}
    save_json(call_dir / "state.json", state)

    result = analyze_phase_b.analyze(tmp_path)
    row = result["search_cost_rows"][0]

    assert result["terminal_status_counts"] == {"missing": 1}
    assert row["status"] == "missing"
    assert row["request_count"] == 1
    assert row["known_tokens"] == 0
    assert row["usage_complete"] is False
    assert row["call_status_counts"] == {"sent_unknown": 1}
    assert row["unknown_cost"] is True
    assert row["missing_outcome"] is True
    assert result["missing_or_noncomplete_jobs"][0]["unknown_cost"] is True
    assert result["missing_or_noncomplete_jobs"][0]["missing_outcome"] is True
    assert result["unknown_cost_job_ids"] == [job["job_id"]]
    assert result["test_outcome_rows"] == []
    assert not (call_dir / "raw_response.json").exists()
    assert not (call_dir / "response.json").exists()
    assert analyze_phase_b.read_json(call_dir / "state.json") == state


def test_public_prefix_terminal_and_unknown_cost_are_included(tmp_path):
    prefix_job = {"job_id": "prefix-sp-b60", "data_block": 60}
    save_json(tmp_path / "manifest.json", {"study_id": "offline-fixture", "manifest_sha256": "fixture",
        "jobs": [], "prefix_jobs": [prefix_job],
        "protocol": {"stages": {"B": {"max_tokens": 100}}}})
    run_dir = tmp_path / "prefix_runs" / prefix_job["job_id"]
    save_json(run_dir / "terminal_status.json", {"status": "sent_unknown", "unknown_cost": True,
        "missing_outcome": True, "outcome_counted_as_zero": False,
        "attempted_calls": [{"call": "000-planner"}]})
    save_json(run_dir / "calls" / "000-planner" / "request.json", {"request_sha256": "request-hash"})

    result = analyze_phase_b.analyze(tmp_path)

    prefix = result["public_prefix_cost_rows"][0]
    assert prefix["status"] == "sent_unknown"
    assert prefix["unknown_cost"] is True
    assert prefix["request_count"] == 1
    assert result["unknown_cost_job_ids"] == [prefix_job["job_id"]]
    assert result["resource_summary"]["public_prefix_terminal_status_counts"] == {"sent_unknown": 1}
    assert result["resource_summary"]["all_observed_token_usage_complete"] is False


def test_block_intervals_and_complete_block_sensitivity_use_planned_pairs():
    jobs, rows = [], []
    for block in (60, 61):
        for checkpoint in (f"b{block}-step08", f"b{block}-step24"):
            for repetition in (0, 1):
                for strategy in ("I", "E0"):
                    job = _job(block, checkpoint, strategy, repetition)
                    jobs.append(job)
                    if not (block == 60 and strategy == "E0" and checkpoint.endswith("step24") and repetition == 1):
                        rows.append(_row(job, 4, 5.0 if strategy == "I" else 5.2))
    result = analyze_phase_b._contrast(rows, jobs, "I", "E0", 4)
    assert result["block_differences_pp"]["60"] == pytest.approx(.2)
    assert result["block_differences_pp"]["61"] == pytest.approx(.2)
    assert result["complete_blocks"] == [61]
    assert len(result["missing_pairs"]) == 1
    assert result["block_equal_difference_pp"]["n_blocks"] == 2
    assert result["complete_block_sensitivity_pp"]["n_blocks"] == 1


def test_branch_coverage_and_all_state_fallback_are_separate_estimands():
    jobs, rows = [], []
    for checkpoint in ("b60-step08", "b60-step24"):
        for repetition in (0, 1):
            for strategy in ("I", "B"):
                job = _job(60, checkpoint, strategy, repetition)
                jobs.append(job)
                if strategy == "I":
                    rows.append(_row(job, 8, 5.0))
                elif checkpoint.endswith("step08"):
                    rows.append(_row(job, 8, 4.8))
    conditional = analyze_phase_b._contrast(rows, jobs, "I", "B", 8,
        branch_available_checkpoints={"b60-step08"}, conditional_branch=True)
    all_states = analyze_phase_b._contrast(rows, jobs, "I", "B", 8,
        branch_available_checkpoints={"b60-step08"}, fallback_unavailable=True)
    assert conditional["expected_pairs_by_block"] == {"60": 2}
    assert conditional["block_differences_pp"]["60"] == pytest.approx(-.2)
    assert all_states["expected_pairs_by_block"] == {"60": 4}
    assert all_states["block_differences_pp"]["60"] == pytest.approx(-.1)
import pytest
