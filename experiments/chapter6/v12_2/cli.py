"""Separate dry-run preparation, explicit future freeze, and one-job live entry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .calls import HTTPTransport
from .common import ROOT, environment, file_sha, read_json, run_lock
from .data import freeze, prepare, split_path, verify_manifest
from .runner import run_search


def parameters(protocol):
    return {key: protocol[key] for key in ("temperature", "planner_max_tokens", "coder_max_tokens", "timeout_seconds")}


def live_job(study, job_id, *, transport_factory=HTTPTransport):
    manifest = verify_manifest(study)
    if manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("Draft study is not executable; a committed final protocol and separate freeze are required.")
    protocol_file = (ROOT / manifest["protocol_file"]["path"]).resolve()
    if not protocol_file.is_relative_to(ROOT) or file_sha(protocol_file) != manifest["protocol_file"]["sha256"]:
        raise ValueError("Final protocol differs from its freeze.")
    if (read_json(protocol_file) != manifest["protocol"] or
            manifest["protocol"]["status"] != "FINAL_PROTOCOL_PENDING_EXECUTION"):
        raise ValueError("Executable manifest must reference the exact final protocol.")
    if manifest["environment"] != environment():
        raise ValueError("Frozen execution environment differs; resume in the recorded environment.")
    job = next((item for item in manifest["jobs"] if item["job_id"] == job_id), None)
    if job is None:
        raise ValueError("Job is not in the frozen matrix.")
    # This process opens only the search snapshot. Test has a separate entry.
    snapshot = read_json(split_path(study, manifest, job["data_block"], "search"))
    # Same-provider jobs are sequential in the manifest's predeclared order.
    # A terminal infrastructure failure stays in that position and is not replaced.
    with run_lock(Path(study) / "lanes" / job["provider"]):
        for prior in manifest["jobs"]:
            if prior["job_id"] == job_id:
                break
            if prior["provider"] != job["provider"]:
                continue
            status_path = Path(study) / "runs" / prior["job_id"] / "status.json"
            if not status_path.exists() or read_json(status_path)["status"] not in (
                    "search_complete_test_not_run", "infrastructure_incomplete"):
                raise ValueError("An earlier job in this provider lane has not reached a terminal state.")
        client = transport_factory(job["provider"], job["model"], manifest["protocol"]["timeout_seconds"])
        return run_search(job, snapshot, Path(study) / "runs" / job_id,
                          {"manifest_sha256": manifest["manifest_sha256"]},
                          parameters(manifest["protocol"]), client, mode="live")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prepare")
    pre.add_argument("--output", type=Path, required=True)
    pre.add_argument("--dry-run", action="store_true", required=True)
    frozen = sub.add_parser("freeze")
    frozen.add_argument("--draft-study", type=Path, required=True)
    frozen.add_argument("--output", type=Path, required=True)
    frozen.add_argument("--protocol", type=Path, required=True)
    search = sub.add_parser("search")
    search.add_argument("--study", type=Path, required=True)
    search.add_argument("--job", required=True)
    search.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if args.command == "search":
        result = live_job(args.study, args.job)
        print(json.dumps({"status": result["status"], "job": args.job}))
        if result["status"] == "infrastructure_incomplete":
            raise SystemExit(2)
    else:
        from chapter6_demo.v12_1.audit_r2 import offline_only
        with offline_only():
            manifest = prepare(args.output) if args.command == "prepare" else freeze(args.draft_study, args.output, args.protocol)
        print(json.dumps({"status": manifest["status"], "jobs": len(manifest["jobs"]),
                          "source_sha256": manifest["source"]["sha256"], "new_model_calls": 0}))


if __name__ == "__main__":
    main()
