import copy
import json
from pathlib import Path

import pytest

from chapter6_demo import benchmarks
from chapter6_demo.programs import Program, ProgramError
from chapter6_demo.v12_1.test_mechanism import two_branches
from chapter6_demo.v12_2.cli import live_job, parameters
from chapter6_demo.v12_2.common import CONTROLLER, ROOT, read_json, run_lock, save_json
from chapter6_demo.v12_2.data import (DRAFT, attach_identity, evaluate_search, planned_jobs,
                                    freeze, prepare, snapshot_evaluator, verify_manifest)
from chapter6_demo.v12_2.fixtures import FakeTransport, FIXTURE_CODES
from chapter6_demo.v12_2.runner import branch_exposure, restore, run_search, state_snapshot
from chapter6_demo.v12_2.test_stage import evaluate_frozen

PARAMETERS = parameters(read_json(DRAFT))


def small_snapshot():
    return {"profile": benchmarks.V12_TSP_PROFILE, "block": 3,
            "probe": [benchmarks._make_instance("tsp", "uniform", 12011, 6)],
            "validation": [benchmarks._make_instance("tsp", "uniform", 12012, 6)]}


def job(controller="niche_fixed_dev", steps=8):
    value = next(item for item in planned_jobs() if item["controller"] == controller and item["data_block"] == 3)
    return {**value, "steps": steps}


def synthetic_evaluator(code, snapshot):
    """Handcrafted losses exercise state transitions, never performance claims."""
    known = {seed[2]: .2 + .04 * i for i, seed in enumerate(benchmarks.SEEDS["tsp"])}
    known.update({FIXTURE_CODES[i]: value for i, value in ((0, .19), (1, .18), (4, .17), (5, .16), (6, .15), (7, .14))})
    try:
        program = Program(code, "tsp")
        loss = known[code]
        evaluation = {"valid": True, "loss": loss, "per_instance_loss": [loss],
                      "per_instance_value": [loss + 1], "family_loss": {"fixture": loss},
                      "behavior": [[1, 0, 0]], "trajectory_behavior": [[1, 0, 0]],
                      "trajectory_values": [loss], "program_hash": program.hash,
                      "ast_nodes": program.ast_nodes, "failure_type": None, "solutions": [[0, 1, 2]]}
    except ProgramError:
        evaluation = {"valid": False, "loss": None, "behavior": [],
                      "trajectory_behavior": [], "failure_type": "ProgramError", "error": "fixture invalid program"}
    evaluation.update(split="validation", features_called=3, local_checks=24,
                      instance_evaluations=2, wall_seconds=0., cpu_seconds=0.)
    return attach_identity(evaluation, code)


def run(directory, client, *, method="niche_fixed_dev", steps=8, hook=lambda *args: None, real=False):
    return run_search(job(method, steps), small_snapshot(), directory, {"fixture": True}, PARAMETERS,
                      client, hook=hook, evaluator=None if real else synthetic_evaluator)


class Interrupted(BaseException):
    pass


@pytest.mark.parametrize("method", ["niche_fixed_dev", "relational_branch"])
@pytest.mark.parametrize("phase,step,stage", [
    ("decision_saved", 0, None), ("decision_saved", 1, None),
    ("checkpoint_committed", 0, None), ("checkpoint_committed", 1, None),
    ("planner_parsed", 1, "planner"), ("response_persisted", 1, "planner"),
    ("response_persisted", 1, "coder"), ("candidate_saved", 1, None),
    ("readout_frozen", 8, None)])
def test_resume_matches_uninterrupted_state_and_does_not_repeat_calls(tmp_path, method, phase, step, stage):
    reference_client = FakeTransport()
    reference = run(tmp_path / "continuous", reference_client, method=method)
    def hook(current, index, role):
        if (current, index, role) == (phase, step, stage):
            raise Interrupted()
    client = FakeTransport()
    with pytest.raises(Interrupted):
        run(tmp_path / "interrupted", client, method=method, hook=hook)
    resumed = run(tmp_path / "interrupted", client, method=method)
    left = read_json(tmp_path / "continuous/checkpoint.json")
    right = read_json(tmp_path / "interrupted/checkpoint.json")
    assert left["records"] == right["records"]
    assert state_snapshot(restore(left)) == state_snapshot(restore(right))
    assert reference["summary"] == resumed["summary"]
    assert client.requests == reference_client.requests
    assert len(client.requests) == 16
    assert resumed["summary"]["branch_development_attempts"] >= 3
    assert resumed["summary"]["model_calls"] == 0


@pytest.mark.parametrize("stage", ["planner", "coder"])
def test_indeterminate_request_stops_and_cannot_restart_by_resume(tmp_path, stage):
    client = FakeTransport(fail_at=(1, stage))
    first = run(tmp_path, client)
    count = len(client.requests)
    assert first["status"] == "infrastructure_incomplete"
    assert first["completed_proposals"] == 1
    assert first["usage"]["total_tokens"] is None
    client.fail_at = None
    second = run(tmp_path, client)
    assert second["status"] == "infrastructure_incomplete"
    assert len(client.requests) == count
    assert not (tmp_path / "selection_frozen.json").exists()


def test_prepared_request_resumes_but_sent_marker_without_response_does_not(tmp_path):
    for phase, resumable in (("request_prepared", True), ("request_sent_marker", False)):
        client = FakeTransport()
        def hook(current, step, stage):
            if current == phase and step == 0:
                raise Interrupted()
        folder = tmp_path / phase
        with pytest.raises(Interrupted):
            run(folder, client, hook=hook)
        result = run(folder, client)
        assert (result["status"] == "search_complete_test_not_run") is resumable
        assert len(client.requests) == (16 if resumable else 0)


def test_missing_usage_is_null_and_candidate_parse_failure_consumes_one_slot(tmp_path):
    client = FakeTransport(missing_usage=True, malformed_planner_steps=(1,))
    result = run(tmp_path, client)
    assert result["summary"]["completed_proposals"] == 8
    assert len(client.requests) == 15
    assert result["usage"]["total_tokens"] is None and not result["usage"]["usage_complete"]
    checkpoint = read_json(tmp_path / "checkpoint.json")
    failed = checkpoint["records"][1]
    assert failed["event"]["valid"] is False
    charged = failed["event"]["branch_parent_id"]
    before = next(item for item in failed["pool_before"] if item["node_id"] == charged)
    after = next(item for item in failed["pool_after"] if item["node_id"] == charged)
    assert after["remaining"] == before["remaining"] - 1


def test_pipeline_uses_real_bounded_evaluation_and_tests_only_frozen_programs(tmp_path):
    result = run(tmp_path / "search", FakeTransport(), real=True)
    assert result["summary"]["completed_proposals"] == 8
    assert result["summary"]["valid_generated"] == 7
    assert not (tmp_path / "search/test.json").exists()
    snapshot = {"block": 3, "profile": benchmarks.V12_TSP_PROFILE,
                "test": [benchmarks._make_instance("tsp", "uniform", 12013, 6)]}
    test = evaluate_frozen(tmp_path / "search", snapshot, tmp_path / "test/results.json")
    assert test["selected_on"] == "validation" and test["new_model_calls"] == 0
    assert len(test["evaluations"]) >= 3
    assert evaluate_frozen(tmp_path / "search", snapshot, tmp_path / "test/results.json") == test


def test_test_data_cannot_enter_search_and_seed_comparator_is_validation_selected(tmp_path, monkeypatch):
    test_file = tmp_path / "test-b3.json"
    test_file.write_text("hidden original test")
    original_open = Path.open
    def guarded(path, *args, **kwargs):
        if path.name == "test-b3.json":
            raise AssertionError("Search opened test data.")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    run(tmp_path / "a", FakeTransport())
    with original_open(test_file, "w") as stream:
        stream.write("arbitrarily changed test contents")
    run(tmp_path / "b", FakeTransport())
    assert read_json(tmp_path / "a/checkpoint.json")["controller_state"] == read_json(tmp_path / "b/checkpoint.json")["controller_state"]
    with snapshot_evaluator(small_snapshot(), ("probe", "validation")):
        with pytest.raises(ValueError, match="cannot access"):
            benchmarks.instances("tsp", "test")
    with pytest.raises(ValueError, match="only probe"):
        evaluate_search(FIXTURE_CODES[0], {**small_snapshot(), "test": []})
    def adversarial_test(code, snapshot):
        value = synthetic_evaluator(code, snapshot)
        # The validation-best initial rule is made WORSE on test deliberately.
        value.update(loss=.9 if code == benchmarks.SEEDS["tsp"][0][2] else .01, split="test")
        return value
    result = evaluate_frozen(tmp_path / "a", {"block": 3, "test": []}, tmp_path / "evaluated/results.json", evaluator=adversarial_test)
    assert result["seed_best_id"] == 0
    assert result["seed_validation_selected_test_gap"] == .9


def test_saved_allocation_and_response_tampering_fail_before_new_calls(tmp_path):
    def stop(phase, step, stage):
        if phase == "decision_saved" and step == 1:
            raise Interrupted()
    client = FakeTransport()
    with pytest.raises(Interrupted):
        run(tmp_path, client, hook=stop)
    saved = read_json(tmp_path / "slots/001/decision.json")
    saved["decision"]["action"] = "restart"
    save_json(tmp_path / "slots/001/decision.json", saved)
    with pytest.raises(ValueError, match="Immutable"):
        run(tmp_path, client)
    assert len(client.requests) == 2


def test_raw_response_tampering_is_not_reclassified_as_bad_model_output(tmp_path):
    def stop(phase, step, stage):
        if (phase, step, stage) == ("response_persisted", 0, "planner"):
            raise Interrupted()
    client = FakeTransport()
    with pytest.raises(Interrupted):
        run(tmp_path, client, hook=stop)
    raw = read_json(tmp_path / "calls/000-planner/raw_response.json")
    raw["envelope"]["body_base64"] = "e30="
    save_json(tmp_path / "calls/000-planner/raw_response.json", raw)
    with pytest.raises(ValueError, match="fingerprint"):
        run(tmp_path, client)
    assert len(client.requests) == 1
    assert len(read_json(tmp_path / "checkpoint.json")["records"]) == 0


@pytest.mark.parametrize("field", ["branch_pool", "decision_history", "success_depths"])
def test_checkpoint_state_corruption_is_rejected(tmp_path, field):
    def stop(phase, step, stage):
        if phase == "checkpoint_committed" and step == 1:
            raise Interrupted()
    client = FakeTransport()
    with pytest.raises(Interrupted):
        run(tmp_path, client, hook=stop)
    checkpoint = read_json(tmp_path / "checkpoint.json")
    checkpoint["controller_state"][field] = {} if field == "success_depths" else []
    save_json(tmp_path / "checkpoint.json", checkpoint)
    with pytest.raises(ValueError, match="Checkpoint"):
        run(tmp_path, client)
    assert len(client.requests) == 4


def test_test_stage_refuses_unfinished_search_and_altered_readout(tmp_path):
    def stop(phase, step, stage):
        if phase == "checkpoint_committed" and step == 0:
            raise Interrupted()
    with pytest.raises(Interrupted):
        run(tmp_path / "search", FakeTransport(), hook=stop)
    with pytest.raises(FileNotFoundError):
        evaluate_frozen(tmp_path / "search", {"block": 3}, tmp_path / "test/result.json")
    run(tmp_path / "search", FakeTransport())
    frozen = read_json(tmp_path / "search/selection_frozen.json")
    frozen["best_id"] = 2
    save_json(tmp_path / "search/selection_frozen.json", frozen)
    with pytest.raises(ValueError, match="identity changed"):
        evaluate_frozen(tmp_path / "search", {"block": 3}, tmp_path / "test/result.json")


def test_decision_exposure_distinguishes_multi_branch_from_family_information():
    state = two_branches("relational_branch")
    exposure = branch_exposure(state, state.choose(1))
    assert exposure["multi_branch_slot"] and exposure["family_priority_varies"]
    assert exposure["full_vs_gain_choice_differs"]
    neutral = two_branches("relational_branch", bad_tag="hybrid")
    exposure = branch_exposure(neutral, neutral.choose(1))
    assert exposure["multi_branch_slot"] and not exposure["family_priority_varies"]
    assert not exposure["full_vs_gain_choice_differs"]


def test_draft_matrix_and_snapshots_are_nonexecuting_and_fresh(tmp_path):
    study = tmp_path / "draft"
    manifest = prepare(study)
    expected = read_json(ROOT / "docs/chapter6/reviews/v121/screening_16_jobs.draft.json")
    assert manifest["jobs"] == expected["jobs"]
    assert manifest["overlap_check"]["new_instances"] == 432
    assert manifest["status"] == "DRAFT_NOT_EXECUTABLE"
    assert verify_manifest(study) == manifest
    with pytest.raises(ValueError, match="Draft study"):
        live_job(study, manifest["jobs"][0]["job_id"])
    with pytest.raises(ValueError, match="new directory"):
        prepare(study)


def test_controller_confusion_and_concurrent_writers_are_rejected(tmp_path):
    wrong = {**job(), "controller_class": "chapter6_demo.v12_controller.V12SearchState"}
    client = FakeTransport()
    with pytest.raises(ValueError, match="requires"):
        run_search(wrong, small_snapshot(), tmp_path, {}, PARAMETERS, client)
    with run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="another process"):
            run(tmp_path, client)
    assert client.requests == []


def test_final_freeze_copies_snapshot_bytes_and_live_gate_enforces_provider_order(tmp_path, monkeypatch):
    # Simulate committed-final-protocol metadata inside this isolated test only.
    # No real final protocol, source freeze or live call is created.
    from chapter6_demo.v12_2 import cli, data
    draft_dir = tmp_path / "draft"
    draft = prepare(draft_dir)
    final = {**draft["protocol"], "status": "FINAL_PROTOCOL_PENDING_EXECUTION",
             "minimum_practical_gain_percentage_points": .2,
             "maximum_acceptable_token_increase_fraction": .25}
    final_path = tmp_path / "final.json"
    save_json(final_path, final)
    monkeypatch.setattr(data, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(data, "git", lambda *args: "" if args[0] == "status" else "fixture-commit")
    frozen_dir = tmp_path / "frozen"
    frozen = freeze(draft_dir, frozen_dir, final_path)
    for block in frozen["data"].values():
        for record in block.values():
            assert (draft_dir / record["path"]).read_bytes() == (frozen_dir / record["path"]).read_bytes()
    assert verify_manifest(frozen_dir)["status"] == "FROZEN_PENDING_EXECUTION"
    called = []
    def fake_run(job, snapshot, *args, **kwargs):
        called.append(job["job_id"])
        assert set(snapshot) == {"profile", "block", "probe", "validation"}
        assert job["controller_class"] == CONTROLLER
        return {"status": "fixture-integration-only"}
    monkeypatch.setattr(cli, "run_search", fake_run)
    transport = lambda *args: object()
    first = frozen["jobs"][0]
    later = next(item for item in frozen["jobs"][1:] if item["provider"] == first["provider"])
    with pytest.raises(ValueError, match="earlier job"):
        live_job(frozen_dir, later["job_id"], transport_factory=transport)
    assert called == []
    live_job(frozen_dir, first["job_id"], transport_factory=transport)
    assert called == [first["job_id"]]
    save_json(frozen_dir / "runs" / first["job_id"] / "status.json", {"status": "infrastructure_incomplete"})
    live_job(frozen_dir, later["job_id"], transport_factory=transport)
    assert called[-1] == later["job_id"]
