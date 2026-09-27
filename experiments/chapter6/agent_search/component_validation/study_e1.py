"""Prepare and verify the E1 checkpoint/continuation study.

Preparation and verification are offline.  ``search-all`` is intentionally not
implemented here; live dispatch must use the service-diagnostic scheduler after
the provider has passed a full planner/coder workload check.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import digest, environment, file_sha, git, read_json, save_json, source_record, utcnow

from .e1 import (DEFAULT_SOURCE_STUDY, NEW_BLOCKS, SOURCE_ARMS, SOURCE_BLOCK_FOR_NEW,
                 SOURCE_STEP, continuation_jobs, load_search_snapshot,
                 prepare_checkpoints, write_checkpoint_set)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL = HERE / "protocol.final.json"


def tooling_source() -> dict:
    names = ["__init__.py", "e1.py", "e1_runner.py", "study_e1.py", "protocol.final.json"]
    files = {f"experiments/chapter6/agent_search/component_validation/{name}":
             file_sha(HERE / name) for name in names}
    return {"format": "e1-source-files-sha256-v1", "files": files, "sha256": digest(files)}


def prepare(output: Path, *, source_study: Path = DEFAULT_SOURCE_STUDY) -> dict:
    """Create the eight new-block checkpoints and immutable 48-job manifest."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new E1 draft directory")
    protocol = read_json(PROTOCOL)
    output.mkdir(parents=True)
    data_records = []
    for block in NEW_BLOCKS:
        snapshot = load_search_snapshot(block)
        rel = Path("data") / f"search-b{block}.json"
        save_json(output / rel, snapshot, immutable=True)
        data_records.append({"block": block, "path": rel.as_posix(),
                             "sha256": file_sha(output / rel)})
    checkpoints = prepare_checkpoints(source_study)
    if len(checkpoints) != 8:
        raise ValueError("E1 protocol requires exactly eight checkpoints")
    checkpoint_records = write_checkpoint_set(output, checkpoints)
    checkpoint_map = {record["checkpoint_id"]: record for record in checkpoint_records}
    data_map = {record["block"]: record for record in data_records}
    jobs = continuation_jobs(checkpoints, steps=protocol["e1"]["continuation_steps"],
                             repetitions=(0, 1))
    for job in jobs:
        job["provider"] = protocol["model"]["provider"]
        job["model"] = protocol["model"]["requested_model"]
        job["checkpoint_path"] = checkpoint_map[job["checkpoint_id"]]["path"]
        job["search_snapshot_path"] = data_map[job["data_block"]]["path"]
        job["search_snapshot_sha256"] = data_map[job["data_block"]]["sha256"]
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
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("E1 study is not frozen")
    for record in manifest["checkpoints"]:
        path = (study / record["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != record["sha256"]:
            raise ValueError(f"E1 checkpoint changed: {record['checkpoint_id']}")
    for record in manifest.get("data", []):
        path = (study / record["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != record["sha256"]:
            raise ValueError(f"E1 data snapshot changed: block {record['block']}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-study", type=Path, default=DEFAULT_SOURCE_STUDY)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify"); p.add_argument("--study", type=Path, required=True); p.add_argument("--frozen", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output, source_study=args.source_study)
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
    else:
        with offline_only(): result = verify(args.study, frozen=args.frozen)
    print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]),
                      "checkpoints": len(result.get("checkpoints", [])),
                      "manifest_sha256": result.get("manifest_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
