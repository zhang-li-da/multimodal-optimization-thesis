"""Create and run a formally recorded recovery segment after a paused batch.

The parent segment is immutable evidence.  Only jobs which had no claim,
request, config, or terminal outcome in the parent are released.  Parent
requests remain available through an in-segment snapshot and are included in
the cumulative ledger without being copied into the new live ``runs`` tree.
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
from collections import Counter
from pathlib import Path

from chapter6_demo.v12_2.common import digest, file_sha, git, read_json, run_lock, save_json, utcnow

from . import continuation_resume as base


TERMINAL_PARENT = {
    "historical_attempt_preserved",
    "continuation_complete",
    "infrastructure_incomplete",
    "budget_exhausted",
    "preparation_incomplete",
    "branch_unavailable",
}


def _call_rows(run: Path) -> dict:
    totals = {"requests": 0, "known_tokens": 0, "unknown_requests": 0,
              "unknown_reservation": 0}
    calls = run / "calls"
    if not calls.exists():
        return totals
    for request_path in sorted(calls.glob("*/request.json")):
        request = read_json(request_path)
        response_path = request_path.with_name("response.json")
        raw_path = request_path.with_name("raw_response.json")
        response = read_json(response_path) if response_path.exists() else {}
        if not response and raw_path.exists():
            try:
                raw = read_json(raw_path)
                if raw.get("envelope_sha256") != digest(raw.get("envelope")):
                    raise ValueError("raw response hash mismatch")
                response = base.decode_response(raw["envelope"])
            except Exception:
                response = {}
        row = base.call_cost(request, response)
        for key in totals:
            totals[key] += int(row.get(key, 0))
    return totals


def _parent_attempts(parent: Path, manifest: dict) -> list[dict]:
    """Return historical rows plus the parent segment's per-job costs."""
    rows = copy.deepcopy(manifest.get("historical_attempts", []))
    for job in manifest["jobs"]:
        run = parent / "runs" / job["job_id"]
        costs = _call_rows(run)
        status_path = run / "terminal_status.json"
        status = read_json(status_path).get("status", "not_started") if status_path.exists() else "not_started"
        if not any(costs.values()) and status == "not_started":
            continue
        rows.append({"job_id": job["job_id"], "segment": "parent",
                     "status": status, "completed_proposals":
                     (read_json(run / "search_result.json").get("summary", {}).get("completed_proposals", 0)
                      if (run / "search_result.json").exists() else 0), **costs})
    return rows


def _parent_status(parent: Path, job_id: str) -> str:
    path = parent / "runs" / job_id / "terminal_status.json"
    return read_json(path).get("status", "not_started") if path.exists() else "not_started"


def _copy_parent_snapshot(parent: Path, out: Path) -> dict:
    snapshot = out / "parent_snapshot"
    shutil.copytree(parent, snapshot)
    files = []
    for path in sorted(p for p in snapshot.rglob("*") if p.is_file()):
        files.append({"path": path.relative_to(snapshot).as_posix(),
                      "sha256": file_sha(path), "bytes": path.stat().st_size})
    value = {"schema": "chapter6-parent-segment-snapshot-v1",
             "source": str(parent.resolve()), "files": files,
             "parent_manifest_sha256": file_sha(parent / "manifest.json"),
             "parent_halt_sha256": file_sha(parent / "halt.json") if (parent / "halt.json").exists() else None}
    value["snapshot_sha256"] = digest(value)
    save_json(out / "parent_snapshot_manifest.json", value, immutable=True)
    return value


def prepare(parent: Path, out: Path, registry: Path) -> dict:
    parent, out, registry = map(lambda p: Path(p).resolve(), (parent, out, registry))
    if out.exists():
        raise ValueError("recovery segment output already exists")
    parent_manifest = read_json(parent / "manifest.json")
    if digest({k: v for k, v in parent_manifest.items() if k != "manifest_sha256"}) != parent_manifest["manifest_sha256"]:
        raise ValueError("parent manifest changed")
    parent_halt = read_json(parent / "halt.json") if (parent / "halt.json").exists() else None
    if not parent_halt:
        raise ValueError("parent segment is not halted")
    owner_path = registry / "owner.json"
    owner = read_json(owner_path)
    if owner.get("output") != str(parent) or owner.get("manifest_sha256") != parent_manifest["manifest_sha256"]:
        raise ValueError("registry owner does not point to the parent segment")
    if git("status", "--porcelain"):
        raise ValueError("commit source before freezing recovery segment")

    out.mkdir(parents=True)
    snapshot = _copy_parent_snapshot(parent, out)
    for name in ("data", "checkpoints"):
        shutil.copytree(parent / name, out / name)
    # Bind copied inputs to the parent hashes before any live dispatch.
    for row in parent_manifest["files"]:
        source = parent / row["path"]
        copied = out / row["path"]
        if not source.exists() or not copied.exists() or file_sha(copied) != row["sha256"]:
            raise ValueError(f"frozen input copy mismatch: {row['path']}")

    jobs = copy.deepcopy(parent_manifest["jobs"])
    for job in jobs:
        status = _parent_status(parent, job["job_id"])
        if status != "not_started":
            job["eligibility"] = "parent_terminal_preserved"
        elif job.get("eligibility") == "preparation_incomplete":
            job["eligibility"] = "preparation_incomplete"
        elif job.get("eligibility") == "ready":
            job["eligibility"] = "ready"
        else:
            job["eligibility"] = job.get("eligibility", "preparation_incomplete")

    source_commit = git("rev-parse", "HEAD")
    manifest = {
        "schema": "chapter6-registered-continuation-segment-v1",
        "created_utc": utcnow(), "source_commit": source_commit,
        "parent_output": str(parent), "parent_manifest_sha256": parent_manifest["manifest_sha256"],
        "parent_halt": parent_halt, "parent_snapshot_sha256": snapshot["snapshot_sha256"],
        "source_index_sha256": file_sha(base.HERE / "SOURCE_SHA256.json"),
        "protocol_sha256": file_sha(base.HERE / "protocol.b.json"),
        "revision_sha256": file_sha(base.HERE / "RESUME_METHOD_ZH.md"),
        "prefix_manifest_sha256": parent_manifest["prefix_manifest_sha256"],
        "registry": str(registry), "output": str(out),
        "model": copy.deepcopy(parent_manifest["model"]),
        "parameters": copy.deepcopy(parent_manifest["parameters"]),
        "jobs": jobs, "files": copy.deepcopy(parent_manifest["files"]),
        "historical_attempts": _parent_attempts(parent, parent_manifest),
        "historical_archives": copy.deepcopy(parent_manifest.get("historical_archives", [])),
        "limits": copy.deepcopy(parent_manifest["limits"]),
        "authorization": {
            "user_instruction": "继续完善方法然后开展下一轮实验",
            "scope": "one formally recorded recovery segment for never-started tasks only; no retries, no new blocks, no Test",
            "parent_segment": parent_manifest["manifest_sha256"],
        },
        "service_acceptance": {"basis": "same frozen local OpenCode MiniMax-M3 workload",
                               "new_calls": 0, "first_queued_task_is_workload_check": True},
        "rules": {"request_retry": False, "task_retry": False,
                  "pause_on": parent_manifest.get("rules", {}).get("pause_on", []),
                  "test_access": False, "unstarted_are_not_terminal": True,
                  "parent_is_read_only": True},
        "test_access": False,
    }
    # Remaining capacity is derived after excluding parent attempts, not by resetting the ledger.
    ready = sum(job["eligibility"] == "ready" for job in jobs)
    manifest["limits"].update({"new_jobs": ready, "new_proposals": ready * 8,
                                "new_requests": ready * 16, "new_tokens": ready * 100000})
    manifest["manifest_sha256"] = digest(manifest)

    with run_lock(registry):
        current = read_json(owner_path)
        if current != owner:
            raise ValueError("registry owner changed while preparing segment")
        history = registry / "ownership_history"
        history.mkdir(parents=True, exist_ok=True)
        save_json(history / f"parent-{parent_manifest['manifest_sha256']}.json",
                  {"role": "parent", "owner": owner, "halt": parent_halt}, immutable=True)
        save_json(out / "parent_halt.json", parent_halt, immutable=True)
        save_json(out / "parent_manifest.json", parent_manifest, immutable=True)
        save_json(out / "manifest.json", manifest, immutable=True)
        # Preserve only terminal parent status markers in the live tree.  Their calls stay in parent_snapshot.
        for job in jobs:
            status = _parent_status(parent, job["job_id"])
            if status == "not_started":
                continue
            run = out / "runs" / job["job_id"]
            run.mkdir(parents=True, exist_ok=True)
            source = parent / "runs" / job["job_id"] / "terminal_status.json"
            marker = read_json(source)
            marker.update({"parent_output": str(parent), "parent_status_sha256": file_sha(source),
                           "parent_segment_read_only": True})
            save_json(run / "terminal_status.json", marker, immutable=True)
        save_json(owner_path, {"output": str(out), "manifest_sha256": manifest["manifest_sha256"],
                               "parent_output": str(parent), "parent_manifest_sha256": parent_manifest["manifest_sha256"]},
                  immutable=False)
    return manifest


def audit(out: Path) -> dict:
    manifest = base.verify(out)
    rows = []
    for job in manifest["jobs"]:
        run = out / "runs" / job["job_id"]
        terminal = read_json(run / "terminal_status.json") if (run / "terminal_status.json").exists() else {}
        result = read_json(run / "search_result.json") if (run / "search_result.json").exists() else {}
        rows.append({"job_id": job["job_id"], "eligibility": job.get("eligibility"),
                     "status": terminal.get("status", "not_started"),
                     "completed_proposals": result.get("summary", {}).get("completed_proposals", 0)})
    costs = base.ledger(out, manifest)
    value = {"schema": "chapter6-continuation-segment-audit-v1",
             "manifest_sha256": manifest["manifest_sha256"], "created_utc": utcnow(),
             "status_counts": dict(Counter(row["status"] for row in rows)),
             "rows": rows, "cost_including_parent": costs,
             "parent_manifest_sha256": manifest["parent_manifest_sha256"],
             "all_released_tasks_terminal": all(row["status"] != "not_started" for row in rows),
             "test_access": False}
    save_json(out / "SEGMENT_AUDIT.json", value, immutable=False)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "audit"))
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if not args.parent or not args.registry:
            parser.error("prepare requires --parent and --registry")
        result = prepare(args.parent, args.output, args.registry)
    elif args.action == "run":
        result = base.dispatch(args.output.resolve())
    else:
        result = audit(args.output.resolve())
    print(json.dumps({k: result[k] for k in ("manifest_sha256", "status_counts",
                                              "cost_including_parent", "halted") if k in result},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
