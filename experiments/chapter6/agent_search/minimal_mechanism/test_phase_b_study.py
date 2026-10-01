import json

import pytest

from chapter6_demo.v12_2.common import digest, file_sha, save_json
from experiments.chapter6.agent_search.minimal_mechanism import phase_b_study as study


@pytest.fixture
def protocol():
    return study.read_json(study.PROTOCOL_PATH)


def test_frozen_matrix_has_eight_prefixes_and_128_balanced_continuations(protocol):
    prefixes = study.prefix_jobs(protocol)
    jobs = study.continuation_jobs(protocol)
    assert len(prefixes) == 8
    assert all(job["policy"] == "SP" and job["steps"] == 24 for job in prefixes)
    assert len(jobs) == len({job["job_id"] for job in jobs}) == 128
    assert {(job["strategy"], job["public_prefix_steps"], job["repetition"])
            for job in jobs} == {
        (strategy, checkpoint, repetition)
        for strategy in ("I", "B", "E0", "EG")
        for checkpoint in (8, 24)
        for repetition in (0, 1)
    }
    assert jobs == study.continuation_jobs(protocol)
    assert protocol["generation"]["temperature"] == 0.7
    assert protocol["generation"]["planner_max_tokens"] == 16384
    assert protocol["generation"]["coder_max_tokens"] == 8192
    assert protocol["generation"]["timeout_seconds"] == 180


def test_checkpoint_selects_incumbent_and_branch_only_from_validation_nodes():
    same_behavior = [[0, 0, 0]] * 4
    different_behavior = [[1, 1, 1]] * 4
    nodes = [{"id": 0, "code": "inc", "evaluation": {
        "valid": True, "loss": 0.10, "behavior": same_behavior}}]
    nodes += [{"id": i, "code": f"seed-{i}", "evaluation": {
        "valid": True, "loss": .30 + i / 100, "behavior": same_behavior}}
              for i in range(1, 8)]
    nodes.extend([
        {"id": 8, "code": "near-same-behavior", "evaluation": {
            "valid": True, "loss": 0.12, "behavior": same_behavior}},
        {"id": 9, "code": "outside", "evaluation": {
            "valid": True, "loss": 0.14, "behavior": different_behavior}},
        {"id": 10, "code": "near-different-behavior", "evaluation": {
            "valid": True, "loss": 0.13, "behavior": different_behavior}},
        {"id": 11, "code": "invalid", "evaluation": {"valid": False, "loss": None}},
    ])
    checkpoint = study._select_prefix_checkpoint(60, 9, nodes, "search-hash")
    assert checkpoint["status"] == "ready"
    assert checkpoint["incumbent"] == {"id": 0, "loss": 0.10}
    assert checkpoint["branch"] == {"id": 10, "loss": 0.13}
    audit = {row["node_id"]: row for row in checkpoint["branch_candidate_audit"]}
    assert audit[8]["probe_behavior_distance"] == 0.0 and not audit[8]["eligible"]
    assert audit[10]["probe_behavior_distance"] == 1.0 and audit[10]["eligible"]
    assert checkpoint["selected_on"] == "validation"
    no_branch = study._select_prefix_checkpoint(60, 9, nodes[:8] + [nodes[8]], "search-hash")
    assert no_branch["branch"] is None
    assert no_branch["branch_unavailable"] is True


def test_sent_unknown_is_terminal_missing_and_unknown_cost_not_zero_filled(tmp_path):
    run = tmp_path / "run"
    call = run / "calls" / "000-planner"
    call.mkdir(parents=True)
    save_json(call / "state.json", {"status": "sent_unknown", "request_sha256": "request-hash"})
    terminal = study._canonical_terminal(run, "infrastructure_incomplete")
    assert terminal["status"] == "sent_unknown"
    assert terminal["unknown_cost"] is True
    assert terminal["missing_outcome"] is True
    assert terminal["outcome_counted_as_zero"] is False
    assert terminal["no_automatic_retry"] is True
    assert terminal["attempted_calls"] == [{"call": "000-planner", "request_sha256": "request-hash"}]


def test_terminal_missing_prefix_is_analyzable_and_skips_have_reasons(tmp_path):
    run_dir = tmp_path / "runs" / "unknown"
    run_dir.mkdir(parents=True)
    save_json(run_dir / "status.json", {"status": "infrastructure_incomplete"})
    save_json(run_dir / "terminal_status.json", {
        "status": "sent_unknown", "unknown_cost": True, "missing_outcome": True,
        "outcome_counted_as_zero": False, "reason": "sent_unknown",
        "attempted_calls": [{"call": "000-planner", "request_sha256": "request-hash"}],
    })
    selections = {
        "4": {"status": "missing_prefix", "completed_proposals": 0},
        "8": {"status": "missing_prefix", "completed_proposals": 0},
    }
    save_json(run_dir / "selection_candidates.json", selections)
    save_json(run_dir / "search_result.json", {
        "selection_candidates_sha256": file_sha(run_dir / "selection_candidates.json")})
    manifest = {"jobs": [{"job_id": "unknown"}]}
    statuses = study._validate_candidate_freezes(tmp_path, manifest)
    assert statuses["audited_job_ids"] == ["unknown"]
    assert statuses["all_available_prefixes_frozen_on_validation"] is True

    skipped = tmp_path / "runs" / "skipped"
    skipped.mkdir()
    save_json(skipped / "terminal_status.json", {
        "status": "not_started"})
    with pytest.raises(ValueError, match="auditable outcome reason"):
        study._validate_candidate_freezes(tmp_path, {"jobs": [{"job_id": "skipped"}]})
    save_json(skipped / "terminal_status.json", {
        "status": "not_started", "reason": "global_service_pause"})
    assert study._validate_candidate_freezes(
        tmp_path, {"jobs": [{"job_id": "skipped"}]})["not_applicable_job_ids"] == ["skipped"]


def test_prepare_manifest_does_not_materialize_test_snapshots(tmp_path, monkeypatch):
    protocol = study.read_json(study.PROTOCOL_PATH)
    monkeypatch.setattr(study, "_validate_protocol", lambda _value: None)
    monkeypatch.setattr(study, "_inventory_check", lambda: {"full_manifest_scan": True})
    monkeypatch.setattr(study, "_data_overlap_check", lambda _blocks: {"id_or_exact_coordinate_collisions": 0})
    original = study.benchmarks._instances_cached
    calls = []

    def search_only(task, split, profile, block):
        calls.append((split, block))
        if split == "test" and block in protocol["stages"]["B"]["blocks"]:
            raise AssertionError("prepare accessed a new Test split")
        return original(task, split, profile, block)

    monkeypatch.setattr(study.benchmarks, "_instances_cached", search_only)
    monkeypatch.setattr(study, "PROTOCOL_PATH", study.PROTOCOL_PATH)
    output = tmp_path / "draft"
    manifest = study.prepare(output)
    assert all(row["role"] == "search" for row in manifest["data"])
    assert not list((output / "data").glob("test-*.json"))
    assert not any(split == "test" and block >= 60 for split, block in calls)


def test_materialize_test_refuses_to_open_data_before_terminal_freeze_gate(tmp_path, monkeypatch):
    manifest = {"jobs": [{"job_id": "one"}],
                "protocol": {"stages": {"B": {"blocks": [60]}}}}
    monkeypatch.setattr(study, "_continuation_statuses", lambda *_args: {"one": "missing"})
    monkeypatch.setattr(study.benchmarks, "_instances_cached",
                        lambda *_args: (_ for _ in ()).throw(AssertionError("Test opened before gate")))
    with pytest.raises(ValueError, match="cannot be opened before every continuation task is terminal"):
        study._materialize_test_snapshots(tmp_path, manifest)
    assert not (tmp_path / "data" / "test-b60.json").exists()


def test_authorization_is_scoped_to_one_stage_budget(tmp_path):
    manifest = {"manifest_sha256": "frozen-digest",
                "protocol": {"model": {"requested_model": "MiniMax-M3"},
                             "stages": {"B": {"authorization_caps": {
                                 "public_prefix": {"max_requests": 384, "max_tokens": 2000000},
                                 "continuation": {"max_requests": 2048, "max_tokens": 12800000}}}}}}
    auth_path = tmp_path / "authorization.json"
    save_json(auth_path, {"status": "approved", "study_manifest_sha256": "frozen-digest",
                          "approved_stages": ["public_prefix"], "requested_model": "MiniMax-M3",
                          "max_requests": 384, "max_tokens": 2000000})
    accepted = study._authorization(tmp_path, manifest, auth_path, "prefix")
    assert accepted["approved_stage"] == "public_prefix"
    assert accepted["max_requests"] == 384
    with pytest.raises(ValueError, match="does not include continuation"):
        study._authorization(tmp_path, manifest, auth_path, "continuation")


def test_test_freeze_audit_rejects_missing_status_or_candidate_manifest(tmp_path):
    manifest = {"jobs": [{"job_id": "j1"}, {"job_id": "j2"}]}
    save_json(tmp_path / "runs" / "j1" / "terminal_status.json", {
        "status": "not_started", "reason": "global_service_pause"})
    with pytest.raises(ValueError, match="all 128 continuation tasks"):
        study._validate_candidate_freezes(tmp_path, manifest)

    save_json(tmp_path / "runs" / "j2" / "terminal_status.json", {
        "status": "continuation_complete"})
    with pytest.raises(ValueError, match="missing frozen prefix selections"):
        study._validate_candidate_freezes(tmp_path, manifest)


def test_live_prefix_dispatch_rejects_missing_authorization_before_transport(tmp_path, monkeypatch):
    manifest = {"manifest_sha256": "frozen-digest",
                "protocol": {"model": {"requested_model": "MiniMax-M3"},
                             "stages": {"B": {"authorization_caps": {
                                 "public_prefix": {"max_requests": 384, "max_tokens": 2000000},
                                 "continuation": {"max_requests": 2048, "max_tokens": 12800000}}}}}}
    monkeypatch.setattr(study, "verify", lambda *_args, **_kwargs: manifest)
    called = []
    with pytest.raises(FileNotFoundError):
        study.dispatch_prefixes(tmp_path, authorization_path=tmp_path / "authorization.json",
                                acceptance_path=tmp_path / "acceptance.json",
                                transport_factory=lambda job: called.append(job))
    assert called == []
    assert not (tmp_path / "dispatch").exists()


def test_inventory_gate_rejects_candidate_blocks_claimed_by_history(monkeypatch):
    inventory = {
        "scan": {"complete": True},
        "data_source_index": {"path": "index.tsv"},
        "manifest_scan": {"claimed_candidate_blocks": [60]},
        "candidate_blocks": {
            "exact_coordinate_collisions_vs_blocks_3_59_and_between_candidates": [],
            "exact_coordinate_collisions_vs_repository_json_and_zip_json": [],
            "groups": {"stage_b_diagnostic": {"blocks": list(range(60, 68))}},
        },
    }
    inventory["inventory_sha256"] = study.digest(inventory)
    monkeypatch.setattr(study, "read_json", lambda _path: inventory)
    monkeypatch.setattr(study, "verify_source_index", lambda **_kwargs: {"path": "index.tsv"})

    with pytest.raises(ValueError, match="already claimed by a historical batch"):
        study._inventory_check()


def test_checkpoint_truncates_future_incumbent_and_branch_before_selection():
    nodes = [{"id": i, "code": str(i), "evaluation": {"valid": True,
              "loss": .1 if i == 0 else .2, "behavior": [[0, 0]]}} for i in range(11)]
    nodes.append({"id": 11, "code": "future", "evaluation": {
        "valid": True, "loss": .09, "behavior": [[1, 1]]}})
    cp = study._select_prefix_checkpoint(60, 8, nodes, "search-hash")
    assert cp["incumbent"]["id"] == 0
    assert len(cp["nodes"]) == 3 + 8
    assert cp["branch_unavailable"]


def test_search_snapshot_accessor_refuses_test_before_read(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "read_json", lambda *_: pytest.fail("opened Test"))
    with pytest.raises(ValueError, match="cannot access a Test"):
        study._snapshot_by_block(tmp_path, {}, 60, "test")


def test_prefix_rejects_test_snapshot_before_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "run_search", lambda *_args, **_kwargs: pytest.fail("runner entered"))
    with pytest.raises(ValueError, match="Test data supplied"):
        study._run_prefix_with_budget({}, {"test": []}, tmp_path, {}, {}, None)


@pytest.mark.parametrize("kind", ["unknown", "tokens", "wall"])
def test_prefix_resume_cannot_reset_usage_or_wall_budget(tmp_path, monkeypatch, kind):
    from experiments.chapter6.agent_search.s3_tsp_r3 import runner as runtime
    save_json(tmp_path / "checkpoint.json", {"elapsed_seconds": 59.5 if kind == "wall" else 0})
    calls_seen = []
    class PriorCalls:
        def usage(self):
            return {"call_attempts": 1, "known_tokens": 9999 if kind == "tokens" else 30,
                    "usage_complete": kind != "unknown"}
        def complete(self, *_):
            calls_seen.append(True)
            return {}
    class Transport:
        deadline = None
        def set_wall_deadline(self, deadline):
            self.deadline = deadline
    transport = Transport()
    monkeypatch.setattr(runtime, "DurableCalls", PriorCalls)
    monkeypatch.setattr(study.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(study, "run_search", lambda *_args, **_kwargs:
        runtime.DurableCalls().complete(1, "planner", "system", "prompt", 20))
    args = ({}, {}, tmp_path, {}, {"request_limit": 48, "token_budget": 10000,
            "wall_limit_seconds": 60}, transport)
    if kind == "wall":
        study._run_prefix_with_budget(*args)
        assert transport.deadline == pytest.approx(100.5)
    else:
        with pytest.raises(runtime.BudgetStop):
            study._run_prefix_with_budget(*args)
        assert calls_seen == []


def test_restart_reconstructs_halt_from_unknown_terminal_before_transport(tmp_path, monkeypatch):
    job = {"job_id": "prefix-sp-b60"}
    manifest = {"prefix_jobs": [job], "protocol": {"model": {
        "requested_model": "MiniMax-M3", "pause_after_consecutive_rate_limits": 2}}}
    run = tmp_path / "prefix_runs" / job["job_id"]
    save_json(run / "terminal_status.json", {"status": "sent_unknown"})
    save_json(run / "status.json", {"status": "sent_unknown"})
    monkeypatch.setattr(study, "verify", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(study, "_authorization", lambda *_args: {})
    monkeypatch.setattr(study, "_acceptance_record", lambda *_args: {})
    result = study.dispatch_prefixes(tmp_path, authorization_path=tmp_path / "unused",
        acceptance_path=tmp_path / "unused", transport_factory=lambda *_: pytest.fail("new request"))
    assert result["halted"]
    assert study.read_json(tmp_path / "dispatch" / "halt.json")["no_new_jobs"]


def test_durable_restart_keeps_known_usage_and_does_not_resend_unknown(tmp_path):
    from chapter6_demo.v12_2.calls import DurableCalls, IndeterminateCall, response_envelope
    config = {"provider": "fixture", "model": "fixture",
              "parameters": {"temperature": .7}}
    sent = []
    class Transport:
        def send(self, request, persist):
            sent.append(request["step"])
            if request["step"] == 1:
                raise TimeoutError("synthetic unknown response")
            body = {"model": "fixture", "choices": [{"message": {"content": "{}"},
                    "finish_reason": "stop"}], "usage": {"prompt_tokens": 17, "completion_tokens": 13}}
            persist(response_envelope(json.dumps(body).encode(), seconds=.01,
                                      request_id="fixture", protocol="openai"))
    first = DurableCalls(tmp_path, config, Transport())
    first.complete(0, "planner", "system", "prompt", 32)
    with pytest.raises(IndeterminateCall):
        first.complete(1, "planner", "system", "prompt", 32)
    recovered = DurableCalls(tmp_path, config, Transport())
    with pytest.raises(IndeterminateCall):
        recovered.complete(1, "planner", "system", "prompt", 32)
    assert sent == [0, 1]
    assert recovered.usage()["known_tokens"] == 30
    assert recovered.usage()["call_attempts"] == 2
    assert recovered.usage()["total_tokens"] is None
