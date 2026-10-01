"""Run a fresh, serial Phase-B SP prefix with the local OpenCode MiniMax client.

The halted historical study is never modified. Search snapshots are copied from
that study, and this run records its own manifest, authorization, calls, and
checkpoint states. It never reads or evaluates Test data.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time

from chapter6_demo.v12_2.common import digest, file_sha, git, read_json, save_json, utcnow
from experiments.chapter6.agent_search.component_validation.service import (
    DiagnosticHTTPTransport, GlobalPauseGate,
)
from experiments.chapter6.agent_search.minimal_mechanism import phase_b_study
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_study import (
    _TrackedDiagnosticTransport, _run_prefix_with_budget, _select_prefix_checkpoint,
)
from chapter6_demo.benchmarks import SEEDS


OLD = Path("C:/Users/67473/Desktop/5/phase_b_public_prefix_20260930/frozen")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
PROVIDER = "minimax-cn-coding-plan"
MODEL = "MiniMax-M3"


def load_old():
    manifest = read_json(OLD / "manifest.json")
    if len(manifest.get("prefix_jobs", [])) != 8:
        raise ValueError("historical prefix matrix is not the expected 8-job SP matrix")
    if manifest.get("jobs") and any(job.get("steps") != 8 for job in manifest["jobs"]):
        raise ValueError("unexpected historical continuation matrix")
    for rec in manifest["data"]:
        if rec["role"] != "search" or rec["instance_count"] != 48:
            raise ValueError("rerun may copy only 48-instance search snapshots")
        source = OLD / rec["path"]
        if file_sha(source) != rec["sha256"]:
            raise ValueError(f"historical search snapshot changed: {rec['path']}")
        snapshot = read_json(source)
        if "test" in snapshot:
            raise ValueError("Test data appeared in the copied search snapshot")
    return manifest


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def prepare(out: Path, old: dict) -> dict:
    if out.exists() and any(out.iterdir()):
        raise ValueError("rerun output must be a new empty directory")
    out.mkdir(parents=True, exist_ok=True)
    data_records = []
    for rec in old["data"]:
        target = out / rec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OLD / rec["path"], target)
        data_records.append({**rec, "sha256": file_sha(target)})
    jobs = copy.deepcopy(old["prefix_jobs"])
    for job in jobs:
        job["rerun_parent_manifest_sha256"] = old["manifest_sha256"]
        job["execution_mode"] = "live_opencode_rerun"
    value = {
        "schema": "chapter6-phase-b-opencode-rerun-v1",
        "status": "FROZEN_PENDING_EXECUTION",
        "created_utc": utcnow(),
        "study_id": "chapter6-phase-b-opencode-rerun-20261001",
        "source_commit": git("rev-parse", "HEAD"),
        "protocol": "historical phase-B public-prefix SP policy; no Test access",
        "historical_parent_manifest_sha256": old["manifest_sha256"],
        "historical_parent_study": str(OLD),
        "model": {"provider": PROVIDER, "requested_model": MODEL,
                  "max_concurrency": 1, "retry_policy": "none"},
        "limits": {"jobs": 8, "proposals_per_job": 24, "requests_per_job": 48,
                   "tokens_per_job": 250000, "max_requests": 384,
                   "max_tokens": 2000000, "wall_seconds_per_job": 3600},
        "policy": "SP incumbent-only",
        "checkpoints": [8, 24],
        "data": data_records,
        "prefix_jobs": jobs,
        "test_access": False,
        "historical_batch_untouched": True,
        "new_model_calls": 0,
    }
    value["manifest_sha256"] = digest(value)
    write_json(out / "RUN_MANIFEST.json", value)
    authorization = {
        "schema": "chapter6-opencode-user-authorization-v1",
        "status": "approved",
        "basis": "latest user instruction authorizing local OpenCode MiniMax-M3 complete experiment",
        "approved_stage": "public_prefix_SP_only",
        "provider": PROVIDER, "requested_model": MODEL,
        "max_requests": 384, "max_tokens": 2000000,
        "max_concurrency": 1, "automatic_retries": False,
        "test_authorized": False, "continuation_authorized": False,
        "historical_parent_manifest_sha256": old["manifest_sha256"],
        "new_run_manifest_sha256": value["manifest_sha256"],
    }
    authorization["sha256"] = digest(authorization)
    write_json(out / "AUTHORIZATION.json", authorization)
    # Existing full-workload acceptance is retained as evidence; the local
    # OpenCode probe is recorded separately and does not contain credentials.
    with __import__("zipfile").ZipFile(HERE.parent / "phase-b-prefix-review-20261001" / "supporting-evidence.zip") as archive:
        acceptance = json.loads(archive.read("provider-acceptance.json"))
    write_json(out / "HISTORICAL_ACCEPTANCE.json", acceptance)
    return value


def run(out: Path, manifest: dict) -> dict:
    # Added after the archived run: a saved halt and a changed execution
    # revision must never silently dispatch another request.
    out = Path(out).resolve()
    if (out / "dispatch" / "halt.json").exists():
        raise ValueError("Archived run is halted; automatic dispatch is forbidden")
    expected = digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    if expected != manifest.get("manifest_sha256"):
        raise ValueError("Run manifest digest mismatch")
    if manifest.get("source_commit") != git("rev-parse", "HEAD"):
        raise ValueError("Execution source changed since manifest preparation")
    for rec in manifest["data"]:
        path = (out / rec["path"]).resolve()
        if not path.is_relative_to(out) or rec["role"] != "search" or file_sha(path) != rec["sha256"]:
            raise ValueError("Frozen search snapshot changed")
        if "test" in read_json(path):
            raise ValueError("Test data in search snapshot")
    gate = GlobalPauseGate(2)
    statuses = {}
    for job in manifest["prefix_jobs"]:
        run_dir = out / "prefix_runs" / job["job_id"]
        snapshot = read_json(out / next(rec["path"] for rec in manifest["data"] if rec["block"] == job["data_block"]))
        transport = _TrackedDiagnosticTransport(PROVIDER, MODEL, 180, min_interval_seconds=1.0)
        parameters = {key: job[key] for key in ("temperature", "planner_max_tokens", "coder_max_tokens",
                     "timeout_seconds", "token_budget", "request_limit", "wall_limit_seconds")}
        try:
            result = _run_prefix_with_budget(
                job, snapshot, run_dir,
                {"rerun_manifest_sha256": manifest["manifest_sha256"],
                 "authorization_sha256": read_json(out / "AUTHORIZATION.json")["sha256"]},
                parameters, transport)
            status = result.get("status", "infrastructure_incomplete")
        except Exception as exc:
            status = "infrastructure_incomplete"
            write_json(run_dir / "runner_exception.json", {"error_type": type(exc).__name__,
                       "no_automatic_retry": True, "utc": utcnow()})
        diagnostics = getattr(transport, "last_diagnostics", None)
        if diagnostics:
            write_json(run_dir / "service_diagnostics.json", {"diagnostics": diagnostics})
        statuses[job["job_id"]] = status
        category = (diagnostics or {}).get("error_category")
        decision = gate.observe(diagnostics)
        if status in {"infrastructure_incomplete", "sent_unknown", "provider_failed"} and category != "rate_limit":
            decision["pause"] = True
            decision["reason"] = "unclassified or non-rate-limit provider interruption"
        write_json(out / "dispatch" / f"{job['job_id']}.json", {"job_id": job["job_id"],
                   "status": status, "service_pause": decision, "utc": utcnow()})
        if decision["pause"]:
            write_json(out / "dispatch" / "halt.json", {"status": "paused", "after_job": job["job_id"],
                       "reason": decision, "new_model_calls": 0, "no_automatic_retry": True})
            break
    for job in manifest["prefix_jobs"]:
        if job["job_id"] not in statuses:
            statuses[job["job_id"]] = "not_started"
            write_json(out / "prefix_runs" / job["job_id"] / "terminal_status.json",
                       {"status": "not_started", "reason": "global_service_pause", "missing_outcome": True})
    summary = {
        "statuses": statuses,
        "completed_proposals": 0, "requests": 0, "known_tokens": 0,
        "unknown_requests": 0, "test_access": False,
    }
    for job in manifest["prefix_jobs"]:
        result_path = out / "prefix_runs" / job["job_id"] / "search_result.json"
        if not result_path.exists():
            continue
        result = read_json(result_path)
        usage = result.get("usage", {})
        summary["completed_proposals"] += int(result.get("summary", {}).get("completed_proposals", 0))
        summary["requests"] += int(usage.get("call_attempts", 0))
        summary["known_tokens"] += int(usage.get("known_tokens", 0) or 0)
        summary["unknown_requests"] += sum(1 for row in usage.get("calls", []) if row.get("status") != "response_persisted")
    summary["new_model_calls"] = summary["requests"]
    write_json(out / "RUN_SUMMARY.json", summary)
    return summary


def freeze_checkpoints(out: Path, manifest: dict) -> dict:
    records = []
    for job in manifest["prefix_jobs"]:
        run_dir = out / "prefix_runs" / job["job_id"]
        checkpoint_path = run_dir / "checkpoint.json"
        saved = read_json(checkpoint_path) if checkpoint_path.exists() else {}
        proposals, seeds = saved.get("records", []), saved.get("seeds", [])
        snapshot_sha = next(rec["sha256"] for rec in manifest["data"] if rec["block"] == job["data_block"])
        for step in manifest["checkpoints"]:
            path = out / "checkpoints" / f"b{job['data_block']}-step{step:02d}.json"
            if len(proposals) < step or len(seeds) != len(SEEDS["tsp"]):
                cp = {"checkpoint_id": path.stem, "block": job["data_block"], "prefix_step": step,
                      "status": "preparation_incomplete", "completed_proposals": len(proposals)}
            else:
                nodes = list(seeds) + [row["node"] for row in proposals[:step]]
                cp = _select_prefix_checkpoint(job["data_block"], step, nodes, snapshot_sha)
                cp["public_prefix_job_id"] = job["job_id"]
            write_json(path, cp)
            records.append({"checkpoint_id": cp["checkpoint_id"], "block": job["data_block"],
                            "prefix_step": step, "path": str(path.relative_to(out)).replace('\\','/'),
                            "sha256": file_sha(path), "status": cp["status"]})
    value = {"schema": "chapter6-opencode-rerun-checkpoints-v1",
             "run_manifest_sha256": manifest["manifest_sha256"], "records": records,
             "test_access": False, "created_utc": utcnow()}
    value["checkpoint_manifest_sha256"] = digest(value)
    write_json(out / "CHECKPOINT_MANIFEST.json", value)
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    old = load_old()
    manifest_path = args.output / "RUN_MANIFEST.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else prepare(args.output, old)
    if manifest.get("historical_parent_manifest_sha256") != old["manifest_sha256"]:
        raise ValueError("rerun manifest is bound to a different historical parent")
    if args.prepare_only:
        print(json.dumps({"prepared": True, "manifest_sha256": manifest["manifest_sha256"]}))
        return
    summary = run(args.output, manifest)
    checkpoints = freeze_checkpoints(args.output, manifest)
    print(json.dumps({"summary": summary, "checkpoints": len(checkpoints["records"]),
                      "ready_checkpoints": sum(r["status"] == "ready" for r in checkpoints["records"])}))


if __name__ == "__main__":
    main()
