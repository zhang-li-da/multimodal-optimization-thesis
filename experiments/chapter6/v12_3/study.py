"""Prepare, freeze and dispatch a single-model study with the unchanged runtime."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from chapter6_demo import benchmarks
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import DurableCalls, HTTPTransport, IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.cli import live_job
from chapter6_demo.v12_2.common import (ROOT, VERSION, digest, environment,
    file_sha, git, read_json, run_lock, save_json, source_record, utcnow)
from chapter6_demo.v12_2.data import (HISTORICAL, content_hash, freeze as runtime_freeze,
    planned_jobs, split_path, verify_manifest)
from chapter6_demo.v12_2.identity import FORMAT

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.final.json"
OLD_DRAFT = ROOT / "experiments/chapter6/v12_2/results/readiness-20260925/evidence/draft-study"
TERMINAL = ("search_complete_test_not_run", "infrastructure_incomplete")


def tooling_source():
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(
        p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for p in (HERE / "__init__.py", HERE / "study.py")}
    return {"format": "named-study-dispatch-files-lf-sha256-v1", "files": files,
            "sha256": digest(files)}


def prepare(directory, protocol_path=PROTOCOL):
    directory = Path(directory)
    if directory.exists():
        raise ValueError("Use a new draft directory.")
    protocol = read_json(protocol_path)
    if protocol["controllers"] != ["niche_fixed_dev", "relational_branch"]:
        raise ValueError("This stage compares only the unchanged two development arms.")
    if protocol["models"] != [["minimax-cn-coding-plan", "MiniMax-M3"]]:
        raise ValueError("This study is limited to the authorized MiniMax M3 provider.")
    if protocol["blocks"] != list(range(3, 11)) or protocol["steps"] != 8:
        raise ValueError("The frozen design is eight paired blocks and eight proposals.")
    old = [item for block in read_json(HISTORICAL)["blocks"].values()
           for split in block.values() for item in split["instances"]]
    ids, contents = {x["id"] for x in old}, {content_hash(x) for x in old}
    old_manifest = read_json(OLD_DRAFT / "manifest.json")
    files, split_hashes, copied, count = {}, {}, [], 0
    for block in protocol["blocks"]:
        if str(block) in old_manifest["data"]:
            search = read_json(split_path(OLD_DRAFT, old_manifest, block, "search"))
            test = read_json(split_path(OLD_DRAFT, old_manifest, block, "test"))
            copied.append(block)
        else:
            splits = {split: copy.deepcopy(benchmarks._instances_cached(
                "tsp", split, benchmarks.V12_TSP_PROFILE, block))
                for split in ("probe", "validation", "test")}
            search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                      "probe": splits["probe"], "validation": splits["validation"]}
            test = {"profile": benchmarks.V12_TSP_PROFILE, "block": block, "test": splits["test"]}
        splits = {"probe": search["probe"], "validation": search["validation"], "test": test["test"]}
        for items in splits.values():
            for item in items:
                hashed = content_hash(item)
                if item["id"] in ids or hashed in contents:
                    raise ValueError("New/old or between-split instance overlap.")
                ids.add(item["id"]); contents.add(hashed); count += 1
        split_hashes[str(block)] = {split: {"count": len(items), "sha256": digest(items)}
                                   for split, items in splits.items()}
        files[str(block)] = {}
        for role, value in (("search", search), ("test", test)):
            name = f"data/{role}-b{block}.json"
            target = directory / name
            if block in copied:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(split_path(OLD_DRAFT, old_manifest, block, role).read_bytes())
            else:
                save_json(target, value, immutable=True)
            files[str(block)][role] = {"path": name, "sha256": file_sha(target)}
    draft_protocol = {**protocol, "status": "DRAFT_NOT_PREREGISTERED"}
    manifest = {"schema": VERSION, "status": "DRAFT_NOT_EXECUTABLE", "created_utc": utcnow(),
        "study_id": protocol["study_id"], "source_commit": git("rev-parse", "HEAD"),
        "source": source_record(), "tooling_source": tooling_source(), "environment": environment(),
        "protocol": draft_protocol, "data": files, "splits": split_hashes,
        "jobs": planned_jobs(draft_protocol), "program_hash_format": FORMAT, "new_model_calls": 0,
        "overlap_check": {"old_instances": len(old), "new_instances": count,
                          "id_or_exact_coordinate_collisions": 0, "geometric_equivalence_checked": False},
        "draft_data_provenance": {"unchanged_blocks_from_v122": copied,
                                  "old_manifest_sha256": old_manifest["manifest_sha256"]}}
    manifest["manifest_sha256"] = digest(manifest)
    save_json(directory / "manifest.json", manifest, immutable=True)
    return manifest


def verify(directory, frozen=False):
    manifest = verify_manifest(directory)
    if manifest.get("tooling_source") != tooling_source():
        raise ValueError("Study dispatch source differs from the prepared version.")
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("Only a separately frozen study can execute.")
    if frozen and manifest["environment"] != environment():
        raise ValueError("Execution environment differs from the freeze.")
    return manifest


def freeze(draft, output, protocol=PROTOCOL):
    verify(draft)
    result = runtime_freeze(draft, output, protocol)
    verify(output, frozen=True)
    return result


def preflight(directory):
    """Exactly one engineering request, unrelated to any study instance."""
    directory = Path(directory)
    protocol = read_json(PROTOCOL)
    provider, model = protocol["models"][0]
    config = {"purpose": "engineering_connectivity_only_not_search",
              "provider": provider, "model": model, "parameters": {"temperature": 0.0},
              "source": source_record(), "tooling_source": tooling_source(), "environment": environment()}
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        calls = DurableCalls(directory, config, HTTPTransport(provider, model, 120))
        try:
            response = calls.complete(0, "connectivity", "You are a coding assistant.",
                                      'Return exactly the JSON object {"ready":true}.', 512)
            returned = response["returned_model"]
            report = {"status": "passed" if returned == model else "model_identity_unconfirmed",
                      "requested_model": model, "returned_model": returned,
                      "response_text_present": bool(response["text"]), "usage": calls.usage(),
                      "new_search_runs": 0, "purpose": config["purpose"], "utc": utcnow()}
        except (IndeterminateCall, ProviderFailure) as exc:
            report = {"status": "failed", "error_type": type(exc).__name__,
                      "usage": calls.usage(), "new_search_runs": 0, "utc": utcnow()}
        save_json(directory / "summary.json", report, immutable=True)
        return report


def search_all(study, preflight_directory):
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    probe = read_json(Path(preflight_directory) / "summary.json")
    if probe["status"] != "passed" or probe["returned_model"] != "MiniMax-M3":
        raise ValueError("The engineering preflight did not confirm MiniMax-M3.")
    # One supervised foreground process; jobs execute sequentially in children.
    # This dispatcher never opens test data, even after a search completes.
    consecutive_failures = 0
    with run_lock(study / "dispatch"):
        for job in manifest["jobs"]:
            status_file = study / "runs" / job["job_id"] / "status.json"
            old = read_json(status_file) if status_file.exists() else {}
            if old.get("status") not in TERMINAL:
                cmd = [sys.executable, "-m", "chapter6_demo.v12_3.study", "search-one",
                       "--study", str(study), "--job", job["job_id"], "--live"]
                launched = utcnow()
                print(json.dumps({"event": "job_start", "job": job["job_id"], "utc": launched}), flush=True)
                log_path = study / "dispatch" / (job["job_id"] + ".txt")
                with log_path.open("a", encoding="utf-8") as log:
                    process = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                if process.returncode not in (0, 2):
                    # Preserve subprocess output and uncertain requests; never retry.
                    save_json(status_file, {"status": "infrastructure_incomplete",
                        "error_type": "SubprocessFailure", "returncode": process.returncode,
                        "completed_proposals": None, "no_automatic_retry": True})
                status = read_json(status_file)
                save_json(study / "dispatch" / (job["job_id"] + ".json"),
                          {"job_id": job["job_id"], "started_utc": launched, "finished_utc": utcnow(),
                           "returncode": process.returncode, "status": status["status"]}, immutable=True)
            else:
                status = old
            consecutive_failures = consecutive_failures + 1 if status["status"] == "infrastructure_incomplete" else 0
            print(json.dumps({"event": "job_terminal", "job": job["job_id"],
                              "status": status["status"], "utc": utcnow()}), flush=True)
            if consecutive_failures >= 2:
                save_json(study / "dispatch" / "halt.json", {"reason": "two_consecutive_infrastructure_failures",
                          "after_job": job["job_id"], "utc": utcnow()}, immutable=True)
                break


def test_all(study):
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    statuses = {j["job_id"]: read_json(study / "runs" / j["job_id"] / "status.json")
                if (study / "runs" / j["job_id"] / "status.json").exists() else {}
                for j in manifest["jobs"]}
    if (any(s.get("status") not in TERMINAL for s in statuses.values())
            and not (study / "dispatch" / "halt.json").exists()):
        raise ValueError("Finish all searches or preserve an infrastructure halt before any test readout.")
    for job in manifest["jobs"]:
        if statuses[job["job_id"]].get("status") == "search_complete_test_not_run":
            subprocess.run([sys.executable, "-m", "chapter6_demo.v12_2.test_stage",
                "--study", str(study), "--job", job["job_id"]], cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("preflight"); p.add_argument("--output", type=Path, required=True); p.add_argument("--live-probe", action="store_true", required=True)
    p = sub.add_parser("search-one"); p.add_argument("--study", type=Path, required=True); p.add_argument("--job", required=True); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("search-all"); p.add_argument("--study", type=Path, required=True); p.add_argument("--preflight", type=Path, required=True); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("test-all"); p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command in ("prepare", "freeze"):
        with offline_only():
            result = prepare(args.output) if args.command == "prepare" else freeze(args.draft, args.output)
        print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]), "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "preflight":
        result = preflight(args.output); print(json.dumps(result))
        if result["status"] != "passed": raise SystemExit(2)
    elif args.command == "search-one":
        verify(args.study, frozen=True)
        result = live_job(args.study, args.job)
        print(json.dumps({"job": args.job, "status": result["status"]}))
        if result["status"] == "infrastructure_incomplete": raise SystemExit(2)
    elif args.command == "search-all":
        search_all(args.study, args.preflight)
    else:
        test_all(args.study)


if __name__ == "__main__":
    main()
