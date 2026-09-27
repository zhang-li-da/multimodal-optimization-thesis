"""Prepare and verify the E1 checkpoint/continuation study.

Preparation and verification are offline.  ``search-all`` is intentionally not
implemented here; live dispatch must use the service-diagnostic scheduler after
the provider has passed a full planner/coder workload check.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import json
from pathlib import Path
import time

from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import digest, environment, file_sha, git, read_json, save_json, source_record, utcnow
from chapter6_demo.v12_2.data import content_hash
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo import benchmarks

from .e1 import (DEFAULT_SOURCE_STUDY, NEW_BLOCKS, SOURCE_ARMS, SOURCE_BLOCK_FOR_NEW,
                 SOURCE_STEP, continuation_jobs, load_search_snapshot, load_test_snapshot,
                 prepare_checkpoints, write_checkpoint_set)
from .evaluator import evaluate_test
from .e1_runner import run_continuation
from .service import DiagnosticHTTPTransport, GlobalPauseGate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL = HERE / "protocol.final.json"
E1_TERMINAL = {"continuation_complete", "infrastructure_incomplete", "budget_exhausted"}


def _acceptance_summary(path: Path, *, expected_model: str) -> dict:
    """Validate the separate full-workload service acceptance record.

    A short connectivity preflight is deliberately insufficient for E1.  The
    acceptance record must describe the planner/coder workload used by the
    continuation runner and contain at least three successful exploration and
    three successful development calls.  This check is read-only and does not
    contact the provider.
    """
    path = Path(path)
    summary_path = path / "summary.json" if path.is_dir() else path
    summary = read_json(summary_path)
    if summary.get("status") != "passed":
        raise ValueError("E1 service acceptance did not pass")
    if summary.get("returned_model") != expected_model:
        raise ValueError("E1 service acceptance model identity is not confirmed")
    workload = summary.get("workload") or summary.get("acceptance_workload") or {}
    planner = int(workload.get("planner_completed", summary.get("planner_completed", 0)) or 0)
    coder = int(workload.get("coder_completed", summary.get("coder_completed", 0)) or 0)
    if planner < 3 or coder < 3:
        raise ValueError("E1 requires a passed 3+3 planner/coder workload acceptance")
    return {"path": str(summary_path.resolve()), "sha256": file_sha(summary_path),
            "status": summary["status"], "returned_model": summary["returned_model"],
            "planner_completed": planner, "coder_completed": coder}


def tooling_source() -> dict:
    names = ["__init__.py", "e1.py", "e1_runner.py", "study_e1.py", "protocol.final.json"]
    files = {f"experiments/chapter6/agent_search/component_validation/{name}":
             file_sha(HERE / name) for name in names}
    return {"format": "e1-source-files-sha256-v1", "files": files, "sha256": digest(files)}


def _check_new_data_overlap() -> dict:
    """Check IDs and exact coordinate content against all earlier blocks."""
    old_ids, old_hashes = set(), set()
    for block in range(3, 44):
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                old_ids.add(item["id"]); old_hashes.add(content_hash(item))
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    checked = 0
    for block in NEW_BLOCKS:
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                if item["id"] in seen_ids or content_hash(item) in seen_hashes:
                    raise ValueError(f"E1 data overlap at block {block}/{split}/{item['id']}")
                seen_ids.add(item["id"]); seen_hashes.add(content_hash(item)); checked += 1
    return {"old_instances_checked": len(old_ids), "new_instances": checked,
            "id_or_exact_coordinate_collisions": 0, "geometric_equivalence_checked": False}


def prepare(output: Path, *, source_study: Path = DEFAULT_SOURCE_STUDY) -> dict:
    """Create the eight new-block checkpoints and immutable 48-job manifest."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new E1 draft directory")
    protocol = read_json(PROTOCOL)
    output.mkdir(parents=True)
    overlap_check = _check_new_data_overlap()
    data_records = []
    for block in NEW_BLOCKS:
        for role, snapshot in (("search", load_search_snapshot(block)),
                               ("test", load_test_snapshot(block))):
            rel = Path("data") / f"{role}-b{block}.json"
            save_json(output / rel, snapshot, immutable=True)
            data_records.append({"block": block, "role": role, "path": rel.as_posix(),
                                 "sha256": file_sha(output / rel)})
    checkpoints = prepare_checkpoints(source_study)
    if len(checkpoints) != 8:
        raise ValueError("E1 protocol requires exactly eight checkpoints")
    checkpoint_records = write_checkpoint_set(output, checkpoints)
    checkpoint_map = {record["checkpoint_id"]: record for record in checkpoint_records}
    data_map = {(record["block"], record["role"]): record for record in data_records}
    jobs = continuation_jobs(checkpoints, steps=protocol["e1"]["continuation_steps"],
                             repetitions=(0, 1))
    for job in jobs:
        job["provider"] = protocol["model"]["provider"]
        job["model"] = protocol["model"]["requested_model"]
        job["checkpoint_path"] = checkpoint_map[job["checkpoint_id"]]["path"]
        job["checkpoint_sha256"] = checkpoint_map[job["checkpoint_id"]]["sha256"]
        search_data = data_map[(job["data_block"], "search")]
        test_data = data_map[(job["data_block"], "test")]
        job["search_snapshot_path"] = search_data["path"]
        job["search_snapshot_sha256"] = search_data["sha256"]
        job["test_snapshot_path"] = test_data["path"]
        job["test_snapshot_sha256"] = test_data["sha256"]
        job["continuation_snapshot_sha256"] = next(
            cp["continuation"]["snapshot_sha256"] for cp in checkpoints
            if cp["checkpoint_id"] == job["checkpoint_id"])
        job["token_budget"] = protocol["generation"]["token_budget_per_job"]
        job["request_limit"] = protocol["generation"]["request_limit_per_job"]
    manifest = {
        "schema": "chapter6-e1-same-state-study-v1",
        "status": "DRAFT_NOT_EXECUTABLE",
        "created_utc": utcnow(),
        "study_id": "chapter6-component-validation-e1-20260927",
        "source_commit": git("rev-parse", "HEAD"), "source": source_record(),
        "tooling_source": tooling_source(), "environment": environment(),
        "protocol_path": PROTOCOL.relative_to(ROOT).as_posix(),
        "protocol_sha256": file_sha(PROTOCOL),
        "source_study": {"path": str(Path(source_study).resolve()),
                          "manifest_sha256": file_sha(Path(source_study) / "manifest.json"),
                          "source_blocks": SOURCE_BLOCK_FOR_NEW,
                          "source_arms": list(SOURCE_ARMS), "source_step": SOURCE_STEP},
        "continuation_blocks": list(NEW_BLOCKS),
        "overlap_check": overlap_check,
        "data": data_records,
        "checkpoints": checkpoint_records,
        "jobs": jobs,
        "planned_checkpoints": 8, "planned_jobs": 48, "planned_proposals": 192,
        "new_model_calls_before_freeze": 0,
        "status_note": "offline checkpoint preparation only; no E1 continuation has run",
    }
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study: Path, *, frozen: bool = False) -> dict:
    study = Path(study).resolve(); manifest = read_json(study / "manifest.json")
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if digest(payload) != manifest.get("manifest_sha256"):
        raise ValueError("E1 manifest digest mismatch")
    if file_sha(ROOT / manifest["protocol_path"]) != manifest["protocol_sha256"]:
        raise ValueError("E1 protocol changed after preparation")
    if manifest.get("source") != source_record() or manifest.get("tooling_source") != tooling_source():
        raise ValueError("E1 source files changed; create a new study version")
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("E1 study is not frozen")
    for record in manifest["checkpoints"]:
        path = (study / record["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != record["sha256"]:
            raise ValueError(f"E1 checkpoint changed: {record['checkpoint_id']}")
    for record in manifest.get("data", []):
        path = (study / record["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != record["sha256"]:
            raise ValueError(f"E1 data snapshot changed: block {record['block']} {record['role']}")
    return manifest


def freeze(draft: Path, output: Path) -> dict:
    draft, output = Path(draft).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new E1 frozen directory")
    manifest = verify(draft)
    if git("status", "--porcelain"):
        raise ValueError("Freeze requires a clean committed source")
    output.mkdir(parents=True)
    for record in manifest["checkpoints"]:
        target = output / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((draft / record["path"]).read_bytes())
    for record in manifest.get("data", []):
        target = output / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((draft / record["path"]).read_bytes())
    frozen = copy.deepcopy(manifest); frozen["status"] = "FROZEN_PENDING_EXECUTION"
    frozen["created_utc"] = utcnow(); frozen.pop("manifest_sha256", None)
    frozen["manifest_sha256"] = digest(frozen)
    save_json(output / "manifest.json", frozen, immutable=True)
    return frozen


def _latest_diagnostics(run_dir: Path) -> dict | None:
    diagnostics = None
    for state_path in sorted((Path(run_dir) / "calls").glob("*/state.json")):
        diagnostics = read_json(state_path).get("diagnostics") or diagnostics
    return diagnostics


def _run_one_e1(job: dict, study: str, manifest: dict) -> dict:
    """Execute one frozen E1 fork in an isolated worker.

    The worker reconstructs all inputs from the manifest.  It never reads test
    data and it does not retry a failed provider call.
    """
    study_path = Path(study).resolve()
    run_dir = study_path / "runs" / job["job_id"]
    status_path = run_dir / "status.json"
    if status_path.exists():
        existing = read_json(status_path)
        # A persisted status is evidence that this job was already attempted;
        # never turn a dispatch replay into an automatic retry.
        return existing
    checkpoint = read_json(study_path / job["checkpoint_path"])
    snapshot = read_json(study_path / job["search_snapshot_path"])
    protocol = manifest["protocol"]
    parameters = {
        "temperature": protocol["generation"]["temperature"],
        "planner_max_tokens": protocol["generation"]["planner_max_tokens"],
        "coder_max_tokens": protocol["generation"]["coder_max_tokens"],
        "timeout_seconds": protocol["generation"]["timeout_seconds"],
        "token_budget": job["token_budget"],
        "request_limit": job["request_limit"],
        "wall_limit_seconds": protocol["generation"].get("wall_limit_seconds_per_job"),
    }
    try:
        transport = DiagnosticHTTPTransport(
            job["provider"], job["model"], parameters["timeout_seconds"],
            min_interval_seconds=float(protocol.get("execution", {}).get(
                "min_request_interval_seconds", 1.0)))
        result = run_continuation(
            job, checkpoint, snapshot, run_dir,
            {"manifest_sha256": manifest["manifest_sha256"],
             "checkpoint_sha256": job.get("checkpoint_sha256"),
             "search_snapshot_sha256": job.get("search_snapshot_sha256")},
            parameters, transport, mode="live")
        status = {"status": result["status"], "summary": result.get("summary"),
                  "usage": result.get("usage"),
                  "diagnostics": _latest_diagnostics(run_dir),
                  "no_automatic_retry": True}
    except (IndeterminateCall, ProviderFailure, OSError, TimeoutError) as exc:
        status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                  "error": str(exc), "diagnostics": getattr(exc, "diagnostics", None),
                  "no_automatic_retry": True}
        save_json(status_path, status)
    save_json(study_path / "dispatch" / f"{job['job_id']}.json", {
        "job_id": job["job_id"], "status": status["status"],
        "diagnostics": status.get("diagnostics"), "utc": utcnow()})
    return status


def search_all(study: Path, acceptance_dir: Path, *, max_concurrency: int | None = None) -> dict:
    """Run the frozen 48-job E1 matrix with a provider-aware global pause.

    This function only dispatches already frozen jobs.  It cannot append jobs,
    retry a failed job, or resume a study after ``halt.json`` has been written.
    Unsubmitted jobs are retained as explicit ``not_started`` records when the
    provider gate pauses the study.
    """
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    acceptance = _acceptance_summary(
        Path(acceptance_dir), expected_model=manifest["jobs"][0]["model"])
    dispatch_dir = study / "dispatch"
    dispatch_dir.mkdir(parents=True, exist_ok=True)
    if (dispatch_dir / "halt.json").exists():
        raise ValueError("E1 study explicitly halted; no restart or replacement")

    protocol = manifest["protocol"]
    execution = protocol.get("execution", {})
    workers = int(max_concurrency or execution.get("max_concurrency", 1))
    workers = max(1, min(workers, len(manifest["jobs"])))
    interval = max(0.0, float(execution.get("dispatch_interval_seconds", 1.0)))
    gate = GlobalPauseGate(execution.get("global_pause_after_rate_limits", 2))
    jobs = list(manifest["jobs"])
    submitted: set[str] = set()
    active = {}
    paused = None
    last_dispatch = 0.0

    def mark_not_started(job: dict, reason: str, diagnostics: dict | None = None):
        save_json(dispatch_dir / f"{job['job_id']}.json", {
            "job_id": job["job_id"], "status": "not_started", "reason": reason,
            "diagnostics": diagnostics, "utc": utcnow()})

    def fill(pool):
        nonlocal last_dispatch
        while paused is None and len(active) < workers:
            next_job = next((item for item in jobs if item["job_id"] not in submitted), None)
            if next_job is None:
                break
            elapsed = time.monotonic() - last_dispatch
            if elapsed < interval:
                time.sleep(interval - elapsed)
            future = pool.submit(_run_one_e1, next_job, str(study), manifest)
            active[future] = next_job
            submitted.add(next_job["job_id"])
            last_dispatch = time.monotonic()

    with ProcessPoolExecutor(max_workers=workers) as pool:
        fill(pool)
        while active:
            completed, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
            for future in completed:
                job = active.pop(future)
                try:
                    status = future.result()
                except Exception as exc:  # preserve worker failure as terminal evidence
                    status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                              "error": str(exc), "diagnostics": None,
                              "no_automatic_retry": True}
                    save_json(study / "runs" / job["job_id"] / "status.json", status)
                    save_json(dispatch_dir / f"{job['job_id']}.json", {
                        "job_id": job["job_id"], "status": status["status"],
                        "error_type": status["error_type"], "error": status["error"],
                        "utc": utcnow()})
                decision = gate.observe(status.get("diagnostics"))
                if decision["pause"] and paused is None:
                    paused = {"reason": "provider_global_pause", "trigger_job": job["job_id"],
                              "category": decision["category"],
                              "diagnostics": status.get("diagnostics"),
                              "consecutive_rate_limits": decision["consecutive_rate_limits"],
                              "utc": utcnow()}
            if paused is None:
                fill(pool)

    if paused is not None:
        for job in jobs:
            if job["job_id"] not in submitted:
                mark_not_started(job, "global_provider_pause", paused.get("diagnostics"))
        paused["pending_jobs"] = sum(job["job_id"] not in submitted for job in jobs)
        save_json(dispatch_dir / "global_pause.json", paused)
        save_json(dispatch_dir / "halt.json", {
            "reason": "provider_global_pause", "category": paused.get("category"),
            "diagnostics": paused.get("diagnostics"), "utc": paused.get("utc")})

    records = []
    for job in jobs:
        path = dispatch_dir / f"{job['job_id']}.json"
        records.append(read_json(path) if path.exists() else {
            "job_id": job["job_id"], "status": "not_started", "reason": "dispatcher_exit"})
    summary = {
        "schema": "chapter6-e1-dispatch-summary-v1", "study_id": manifest["study_id"],
        "manifest_sha256": manifest["manifest_sha256"], "acceptance": acceptance,
        "planned_jobs": len(jobs), "submitted_jobs": len(submitted),
        "completed_jobs": sum(record.get("status") in E1_TERMINAL for record in records),
        "not_started_jobs": sum(record.get("status") == "not_started" for record in records),
        "paused": paused is not None, "utc": utcnow(), "new_model_calls": None,
    }
    save_json(dispatch_dir / "summary.json", summary)
    return summary


def test_all(study: Path) -> dict:
    """Evaluate each terminal continuation's validation-selected program on test."""
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    statuses = {}
    for job in manifest["jobs"]:
        status_path = study / "runs" / job["job_id"] / "status.json"
        if not status_path.exists():
            raise ValueError(f"E1 job has no terminal status: {job['job_id']}")
        statuses[job["job_id"]] = read_json(status_path)
    allowed = {"continuation_complete", "infrastructure_incomplete", "budget_exhausted"}
    if any(status.get("status") not in allowed for status in statuses.values()):
        raise ValueError("E1 jobs are not all terminal")
    data = {(record["block"], record["role"]): record for record in manifest["data"]}
    outputs = 0
    for job in manifest["jobs"]:
        run_dir = study / "runs" / job["job_id"]
        selection_path = run_dir / "selection_frozen.json"
        if not selection_path.exists():
            continue
        selection = read_json(selection_path)
        test_record = data[(job["data_block"], "test")]
        snapshot = read_json(study / test_record["path"])
        output = study / "tests" / f"{job['job_id']}.json"
        if output.exists():
            outputs += 1
            continue
        report = {"schema": "chapter6-e1-test-v1", "job_id": job["job_id"],
                  "strategy": job["strategy"], "repetition": job["repetition"],
                  "search_status": statuses[job["job_id"]]["status"],
                  "selection_sha256": file_sha(selection_path),
                  "test_snapshot_sha256": file_sha(study / test_record["path"]),
                  "selected_on": "new-block-validation", "new_model_calls": 0}
        try:
            evaluated = evaluate_test(selection["code"], snapshot)
            report.update(primary_test_gap=evaluated["loss"] if evaluated["valid"] else 1.0,
                          test_valid=evaluated["valid"], evaluation=evaluated)
        except Exception as exc:
            report.update(primary_test_gap=1.0, test_valid=False,
                          test_error_type=type(exc).__name__, evaluation={})
        save_json(output, report, immutable=True)
        outputs += 1
    return {"status": "test_complete", "planned_jobs": len(manifest["jobs"]),
            "tested_jobs": outputs, "new_model_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-study", type=Path, default=DEFAULT_SOURCE_STUDY)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify"); p.add_argument("--study", type=Path, required=True); p.add_argument("--frozen", action="store_true")
    p = sub.add_parser("search-all"); p.add_argument("--study", type=Path, required=True)
    p.add_argument("--acceptance", type=Path, required=True)
    p.add_argument("--max-concurrency", type=int, default=None)
    p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("test-all"); p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output, source_study=args.source_study)
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
    elif args.command == "test-all":
        with offline_only(): result = test_all(args.study)
    elif args.command == "search-all":
        result = search_all(args.study, args.acceptance, max_concurrency=args.max_concurrency)
    else:
        with offline_only(): result = verify(args.study, frozen=args.frozen)
    print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]),
                      "checkpoints": len(result.get("checkpoints", [])),
                      "manifest_sha256": result.get("manifest_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
