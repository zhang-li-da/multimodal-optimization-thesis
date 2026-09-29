"""Prepare, freeze, execute and test the short-horizon diagnostic study."""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path
import subprocess
import time

from chapter6_demo import benchmarks
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, environment, file_sha, git, read_json, save_json, source_record, utcnow

from .evaluator import evaluate_test
from .service import DiagnosticHTTPTransport, GlobalPauseGate
from .short_horizon import (DEFAULT_SOURCE_STUDY, NEW_BLOCKS, PHASES, SOURCE_ARM,
                            check_new_data_overlap, load_search_snapshot, load_test_snapshot,
                            prepare_checkpoints, short_horizon_jobs, write_checkpoint_set)
from .short_horizon_runner import run_short_horizon

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL = HERE / "protocol.short_horizon.draft.json"
TERMINAL = {"continuation_complete", "infrastructure_incomplete", "budget_exhausted"}


def tooling_source():
    names = ["short_horizon.py", "short_horizon_runner.py", "study_short_horizon.py", "protocol.short_horizon.draft.json", "evaluator.py", "service.py"]
    files = {f"experiments/chapter6/agent_search/component_validation/{name}": file_sha(HERE / name) for name in names}
    return {"format": "short-horizon-source-files-sha256-v1", "files": files, "sha256": digest(files)}


def _acceptance_summary(path: Path, expected_model: str) -> dict:
    summary_path = Path(path) / "summary.json" if Path(path).is_dir() else Path(path)
    summary = read_json(summary_path)
    workload = summary.get("workload") or summary.get("acceptance_workload") or {}
    if summary.get("status") != "passed" or summary.get("returned_model") != expected_model:
        raise ValueError("short-horizon service acceptance did not pass for the frozen model")
    planner = int(workload.get("planner_completed", summary.get("planner_completed", 0)) or 0)
    coder = int(workload.get("coder_completed", summary.get("coder_completed", 0)) or 0)
    if planner < 3 or coder < 3:
        raise ValueError("short-horizon requires the existing 3+3 planner/coder workload acceptance")
    return {"path": str(summary_path.resolve()), "sha256": file_sha(summary_path),
            "status": summary["status"], "returned_model": summary["returned_model"],
            "planner_completed": planner, "coder_completed": coder}


def prepare(output: Path, *, source_study: Path = DEFAULT_SOURCE_STUDY) -> dict:
    output = Path(output).resolve()
    if output.exists(): raise ValueError("Use a new short-horizon draft directory")
    protocol = read_json(PROTOCOL)
    overlap = check_new_data_overlap()
    output.mkdir(parents=True)
    data = []
    for block in NEW_BLOCKS:
        for role, snapshot in (("search", load_search_snapshot(block)), ("test", load_test_snapshot(block))):
            rel = Path("data") / f"{role}-b{block}.json"
            save_json(output / rel, snapshot, immutable=True)
            data.append({"block": block, "role": role, "path": rel.as_posix(), "sha256": file_sha(output / rel)})
    checkpoints = prepare_checkpoints(source_study)
    checkpoint_records = write_checkpoint_set(output, checkpoints)
    checkpoint_map = {x["checkpoint_id"]: x for x in checkpoint_records}
    data_map = {(x["block"], x["role"]): x for x in data}
    jobs = short_horizon_jobs(checkpoints, steps=protocol["matrix"]["steps_per_job"])
    for job in jobs:
        job.update(provider=protocol["model"]["provider"], model=protocol["model"]["requested_model"],
                   checkpoint_path=checkpoint_map[job["checkpoint_id"]]["path"],
                   checkpoint_sha256=checkpoint_map[job["checkpoint_id"]]["sha256"],
                   search_snapshot_path=data_map[(job["data_block"], "search")]["path"],
                   search_snapshot_sha256=data_map[(job["data_block"], "search")]["sha256"],
                   test_snapshot_path=data_map[(job["data_block"], "test")]["path"],
                   test_snapshot_sha256=data_map[(job["data_block"], "test")]["sha256"],
                   token_budget=protocol["generation"]["token_budget_per_job"],
                   request_limit=protocol["generation"]["request_limit_per_job"],
                   wall_limit_seconds=protocol["generation"]["wall_limit_seconds_per_job"])
    manifest = {"schema": "chapter6-short-horizon-study-v1", "status": "DRAFT_NOT_EXECUTABLE",
                "created_utc": utcnow(), "study_id": protocol["study_id"], "source_commit": git("rev-parse", "HEAD"),
                "source": source_record(), "tooling_source": tooling_source(), "environment": environment(),
                "protocol": protocol, "protocol_path": PROTOCOL.relative_to(ROOT).as_posix(),
                "protocol_sha256": file_sha(PROTOCOL), "source_study": {"path": str(Path(source_study).resolve()),
                "manifest_sha256": file_sha(Path(source_study) / "manifest.json"), "source_arm": SOURCE_ARM},
                "data": data, "checkpoints": checkpoint_records, "jobs": jobs, "overlap_check": overlap,
                "planned_checkpoints": len(checkpoints), "planned_jobs": len(jobs),
                "planned_proposals": sum(x["steps"] for x in jobs), "planned_model_requests": sum(x["steps"] * 2 for x in jobs),
                "new_model_calls_before_freeze": 0,
                "status_note": "offline checkpoint preparation only; no short-horizon continuation has run"}
    manifest["manifest_sha256"] = digest(manifest); save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study: Path, *, frozen=False) -> dict:
    study = Path(study).resolve(); manifest = read_json(study / "manifest.json")
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest.get("manifest_sha256"):
        raise ValueError("short-horizon manifest digest mismatch")
    if file_sha(ROOT / manifest["protocol_path"]) != manifest["protocol_sha256"] or manifest["protocol"] != read_json(PROTOCOL):
        raise ValueError("short-horizon protocol changed")
    if manifest.get("source") != source_record() or manifest.get("tooling_source") != tooling_source():
        raise ValueError("short-horizon source files changed; create a new study version")
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION": raise ValueError("study is not frozen")
    for record in manifest["checkpoints"] + manifest["data"]:
        path = (study / record["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != record["sha256"]:
            raise ValueError(f"short-horizon archive changed: {record}")
    return manifest


def freeze(draft: Path, output: Path) -> dict:
    draft, output = Path(draft).resolve(), Path(output).resolve()
    if output.exists() or git("status", "--porcelain"): raise ValueError("freeze requires clean committed source and new output")
    manifest = verify(draft)
    if any(read_json(draft / x["path"]).get("preparation", {}).get("status") != "ready" for x in manifest["checkpoints"]):
        raise ValueError("cannot freeze a study with an incomplete checkpoint")
    output.mkdir(parents=True)
    for record in manifest["checkpoints"] + manifest["data"]:
        target = output / record["path"]; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes((draft / record["path"]).read_bytes())
    frozen = copy.deepcopy(manifest); frozen["status"] = "FROZEN_PENDING_EXECUTION"; frozen["created_utc"] = utcnow(); frozen.pop("manifest_sha256", None); frozen["manifest_sha256"] = digest(frozen)
    save_json(output / "manifest.json", frozen, immutable=True); return frozen


def _run_one(job, study, manifest):
    study, run_dir = Path(study).resolve(), Path(study).resolve() / "runs" / job["job_id"]
    status_path = run_dir / "status.json"
    if status_path.exists(): return read_json(status_path)
    checkpoint, snapshot = read_json(study / job["checkpoint_path"]), read_json(study / job["search_snapshot_path"])
    p = manifest["protocol"]["generation"]
    params = {"temperature": p["temperature"], "planner_max_tokens": p["planner_max_tokens"], "coder_max_tokens": p["coder_max_tokens"], "timeout_seconds": p["timeout_seconds"], "token_budget": job["token_budget"], "request_limit": job["request_limit"], "wall_limit_seconds": job["wall_limit_seconds"]}
    try:
        transport = DiagnosticHTTPTransport(job["provider"], job["model"], params["timeout_seconds"], min_interval_seconds=float(manifest["protocol"].get("execution", {}).get("dispatch_interval_seconds", 1.0)))
        result = run_short_horizon(job, checkpoint, snapshot, run_dir, {"manifest_sha256": manifest["manifest_sha256"], "checkpoint_sha256": job["checkpoint_sha256"], "search_snapshot_sha256": job["search_snapshot_sha256"]}, params, transport, mode="live")
        status = {"status": result["status"], "summary": result.get("summary"), "usage": result.get("usage"), "no_automatic_retry": True}
    except (IndeterminateCall, ProviderFailure, OSError, TimeoutError) as exc:
        status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__, "error": str(exc), "diagnostics": getattr(exc, "diagnostics", None), "no_automatic_retry": True}
        run_dir.mkdir(parents=True, exist_ok=True)
    save_json(status_path, status); return status


def search_all(study: Path, acceptance: Path, *, max_concurrency=None) -> dict:
    study = Path(study).resolve(); manifest = verify(study, frozen=True); acceptance_result = _acceptance_summary(acceptance, manifest["jobs"][0]["model"])
    if any(read_json(study / x["path"]).get("preparation", {}).get("status") != "ready" for x in manifest["checkpoints"]): raise ValueError("study contains incomplete checkpoints")
    dispatch = study / "dispatch"; dispatch.mkdir(parents=True, exist_ok=True)
    if (dispatch / "halt.json").exists(): raise ValueError("study halted; no replay or retry")
    protocol = manifest["protocol"]; workers = max(1, min(int(max_concurrency or protocol["execution"].get("max_concurrency", 1)), len(manifest["jobs"]))); interval = float(protocol["execution"].get("dispatch_interval_seconds", 1.0)); gate = GlobalPauseGate(protocol["execution"].get("global_pause_after_rate_limits", 2)); jobs = manifest["jobs"]; submitted, active, paused, last = set(), {}, None, 0.0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        while len(active) or len(submitted) < len(jobs):
            while paused is None and len(active) < workers and len(submitted) < len(jobs):
                if time.monotonic() - last < interval: time.sleep(interval - (time.monotonic() - last))
                job = next(x for x in jobs if x["job_id"] not in submitted); fut = pool.submit(_run_one, job, str(study), manifest); active[fut] = job; submitted.add(job["job_id"]); last = time.monotonic()
            if not active: break
            done, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
            for fut in done:
                job = active.pop(fut)
                try: status = fut.result()
                except Exception as exc: status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__, "error": str(exc), "diagnostics": None}; save_json(study / "runs" / job["job_id"] / "status.json", status)
                decision = gate.observe(status.get("diagnostics"))
                if decision["pause"] and paused is None: paused = {"reason": "provider_global_pause", "trigger_job": job["job_id"], **decision, "diagnostics": status.get("diagnostics"), "utc": utcnow()}
    for job in jobs:
        path = dispatch / f"{job['job_id']}.json"
        if path.exists():
            continue
        status_path = study / "runs" / job["job_id"] / "status.json"
        if job["job_id"] not in submitted:
            record = {"job_id": job["job_id"], "status": "not_started",
                      "reason": "global_provider_pause" if paused else "dispatcher_exit"}
        elif status_path.exists():
            status = read_json(status_path)
            record = {"job_id": job["job_id"], "status": status.get("status"),
                      "summary": status.get("summary"), "usage": status.get("usage")}
        else:
            record = {"job_id": job["job_id"], "status": "infrastructure_incomplete",
                      "reason": "submitted_without_terminal_status"}
        save_json(path, record)
    if paused:
        save_json(dispatch / "global_pause.json", paused); save_json(dispatch / "halt.json", paused)
    records = [read_json(dispatch / f"{job['job_id']}.json") for job in jobs]
    summary = {"schema": "chapter6-short-horizon-dispatch-v1", "study_id": manifest["study_id"], "status": "provider_paused" if paused else "dispatch_complete", "manifest_sha256": manifest["manifest_sha256"], "acceptance": acceptance_result, "planned_jobs": len(jobs), "submitted_jobs": len(submitted), "completed_jobs": sum(x.get("status") in TERMINAL for x in records), "not_started_jobs": sum(x.get("status") == "not_started" for x in records), "paused": bool(paused), "utc": utcnow()}
    save_json(dispatch / "summary.json", summary); return summary


def test_all(study: Path) -> dict:
    study = Path(study).resolve(); manifest = verify(study, frozen=True); outputs = 0
    data = {(x["block"], x["role"]): x for x in manifest["data"]}
    for job in manifest["jobs"]:
        status_path = study / "runs" / job["job_id"] / "status.json"
        if not status_path.exists(): raise ValueError(f"job has no terminal status: {job['job_id']}")
        status = read_json(status_path)
        if status.get("status") not in TERMINAL: raise ValueError(f"job is not terminal: {job['job_id']}")
        if status["status"] != "continuation_complete": continue
        selection_path = study / "runs" / job["job_id"] / "selection_frozen.json"
        if not selection_path.exists(): continue
        selection = read_json(selection_path); snapshot_path = study / data[(job["data_block"], "test")]["path"]; snapshot = read_json(snapshot_path); output = study / "tests" / f"{job['job_id']}.json"
        if output.exists(): outputs += 1; continue
        evaluated = evaluate_test(selection["code"], snapshot)
        report = {"schema": "chapter6-short-horizon-test-v1", "job_id": job["job_id"], "strategy": job["strategy"], "phase": selection_path.parent.name, "search_status": status["status"], "selection_sha256": file_sha(selection_path), "test_snapshot_sha256": file_sha(snapshot_path), "selected_on": "validation", "primary_test_gap": evaluated["loss"] if evaluated["valid"] else 1.0, "test_valid": evaluated["valid"], "evaluation": evaluated, "new_model_calls": 0}
        save_json(output, report, immutable=True); outputs += 1
    return {"status": "test_complete", "planned_jobs": len(manifest["jobs"]), "tested_jobs": outputs, "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True); p.add_argument("--source-study", type=Path, default=DEFAULT_SOURCE_STUDY)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify"); p.add_argument("--study", type=Path, required=True); p.add_argument("--frozen", action="store_true")
    p = sub.add_parser("search-all"); p.add_argument("--study", type=Path, required=True); p.add_argument("--acceptance", type=Path, required=True); p.add_argument("--max-concurrency", type=int, default=None); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("test-all"); p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output, source_study=args.source_study)
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
    elif args.command == "verify":
        with offline_only(): result = verify(args.study, frozen=args.frozen)
    elif args.command == "test-all":
        with offline_only(): result = test_all(args.study)
    else: result = search_all(args.study, args.acceptance, max_concurrency=args.max_concurrency)
    print({"status": result.get("status"), "jobs": result.get("planned_jobs", len(result.get("jobs", []))), "checkpoints": len(result.get("checkpoints", [])), "manifest_sha256": result.get("manifest_sha256")})


if __name__ == "__main__": main()
