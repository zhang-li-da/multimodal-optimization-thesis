"""Prepare, freeze, execute and read out the S2 TSP protection study."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys

from chapter6_demo import benchmarks
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import DurableCalls, HTTPTransport, IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, environment, file_sha, git, read_json, run_lock, save_json, source_record, utcnow
from chapter6_demo.v12_2.data import content_hash, evaluate_test

from .runner import run_search

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL = HERE / "protocol.p1.final.json"
TERMINAL = {"search_complete_test_not_run", "infrastructure_incomplete", "budget_exhausted"}


def tooling_source():
    names = ["__init__.py", "controller.py", "runner.py", "study.py", "protocol.p1.final.json"]
    files = {f"experiments/chapter6/agent_search/s2_tsp/{name}": hashlib.sha256(
        (HERE / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in names}
    return {"format": "s2-p1-source-files-lf-sha256-v1", "files": files,
            "sha256": digest(files)}


def _old_data():
    """Return IDs and exact point hashes from all previous public TSP batches."""
    ids, hashes = set(), set()
    for block in range(3, 14):
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                ids.add(item["id"]); hashes.add(content_hash(item))
    return ids, hashes


def jobs(protocol):
    out = []
    for block in protocol["blocks"]:
        for seed_label in protocol["search_seeds"]:
            for controller in protocol["controllers"]:
                protection = controller == "fb_protected"
                out.append({
                    "job_id": f"minimax-{controller}-b{block}-s{seed_label}",
                    "provider": protocol["models"][0][0], "model": protocol["models"][0][1],
                    "controller": controller, "controller_class": "chapter6.agent_search.s2_tsp.controller.S2State",
                    "task": "tsp", "data_block": block,
                    "search_seed": block * 100 + seed_label, "search_seed_label": seed_label,
                    "steps": protocol["steps"], "token_budget": protocol["token_budget_per_job"],
                    "request_limit": protocol["request_limit_per_job"],
                    "wall_limit_seconds": protocol["wall_limit_seconds_per_job"],
                    "capacity": protocol["capacity"], "grant": protocol["initial_grant"],
                    "maximum_direction_attempts": protocol["maximum_direction_attempts"],
                    "quality_tolerance": protocol["quality_tolerance"],
                    "gain_epsilon": protocol["gain_epsilon"], "protection": protection,
                })
    random.Random(protocol["order_seed"]).shuffle(out)
    return out


def prepare(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new S2 draft directory")
    protocol = read_json(PROTOCOL)
    old_ids, old_hashes = _old_data()
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    output.mkdir(parents=True)
    files, splits = {}, {}
    count = 0
    for block in protocol["blocks"]:
        current = {split: copy.deepcopy(benchmarks._instances_cached(
            "tsp", split, benchmarks.V12_TSP_PROFILE, block))
                   for split in ("probe", "validation", "test")}
        for split, items in current.items():
            for item in items:
                hashed = content_hash(item)
                if item["id"] in seen_ids or hashed in seen_hashes:
                    raise ValueError(f"Data overlap at block {block}/{split}/{item['id']}")
                seen_ids.add(item["id"]); seen_hashes.add(hashed); count += 1
        splits[str(block)] = {k: {"count": len(v), "sha256": digest(v)} for k, v in current.items()}
        search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                  "probe": current["probe"], "validation": current["validation"]}
        test = {"profile": benchmarks.V12_TSP_PROFILE, "block": block, "test": current["test"]}
        files[str(block)] = {}
        for role, value in (("search", search), ("test", test)):
            rel = f"data/{role}-b{block}.json"
            save_json(output / rel, value, immutable=True)
            files[str(block)][role] = {"path": rel, "sha256": file_sha(output / rel)}
    manifest = {
        "schema": "chapter6-s2-p1-study-v1", "status": "DRAFT_NOT_EXECUTABLE",
        "created_utc": utcnow(), "study_id": protocol["study_id"],
        "source_commit": git("rev-parse", "HEAD"), "source": source_record(),
        "tooling_source": tooling_source(), "environment": environment(),
        "protocol": protocol, "protocol_file": {"path": PROTOCOL.relative_to(ROOT).as_posix(),
        "sha256": file_sha(PROTOCOL)}, "data": files, "splits": splits,
        "jobs": jobs(protocol), "new_model_calls": 0,
        "overlap_check": {"old_instances_checked": len(old_ids), "new_instances": count,
                          "id_or_exact_coordinate_collisions": 0,
                          "geometric_equivalence_checked": False},
    }
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study, *, frozen=False):
    study = Path(study).resolve()
    manifest = read_json(study / "manifest.json")
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]:
        raise ValueError("S2 manifest digest mismatch")
    if manifest["protocol"] != read_json(PROTOCOL):
        raise ValueError("S2 protocol differs from the frozen source")
    if manifest["tooling_source"] != tooling_source():
        raise ValueError("S2 tooling source differs")
    if manifest["jobs"] != jobs(manifest["protocol"]):
        raise ValueError("S2 job order differs")
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("S2 study is not frozen")
    for block, roles in manifest["data"].items():
        for role, rec in roles.items():
            path = (study / rec["path"]).resolve()
            if not path.is_relative_to(study) or file_sha(path) != rec["sha256"]:
                raise ValueError("S2 data snapshot changed")
    return manifest


def freeze(draft, output):
    draft, output = Path(draft).resolve(), Path(output).resolve()
    if output.exists() or git("status", "--porcelain"):
        raise ValueError("S2 freeze requires a clean committed source and new output")
    d = verify(draft)
    protocol = read_json(PROTOCOL)
    manifest = {**d, "status": "FROZEN_PENDING_EXECUTION", "created_utc": utcnow(),
                "source_commit": git("rev-parse", "HEAD"), "protocol": protocol}
    for block, roles in d["data"].items():
        for role, rec in roles.items():
            target = output / rec["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((draft / rec["path"]).read_bytes())
    manifest.pop("manifest_sha256", None)
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def preflight(output):
    output = Path(output).resolve()
    protocol = read_json(PROTOCOL)
    config = {"purpose": "engineering_connectivity_only_not_search",
              "provider": protocol["models"][0][0], "model": protocol["models"][0][1],
              "parameters": {"temperature": 0.0}, "source": source_record(),
              "tooling_source": tooling_source(), "environment": environment()}
    with run_lock(output):
        save_json(output / "config.json", config, immutable=True)
        calls = DurableCalls(output, config, HTTPTransport(config["provider"], config["model"], 120))
        try:
            response = calls.complete(0, "connectivity", "You are a coding assistant.",
                                      'Return exactly the JSON object {"ready":true}.', 512)
            report = {"status": "passed" if response["returned_model"] == config["model"] else "model_identity_unconfirmed",
                      "requested_model": config["model"], "returned_model": response["returned_model"],
                      "response_text_present": bool(response["text"]), "usage": calls.usage(),
                      "new_search_runs": 0, "utc": utcnow()}
        except (IndeterminateCall, ProviderFailure) as exc:
            report = {"status": "failed", "error_type": type(exc).__name__,
                      "usage": calls.usage(), "new_search_runs": 0, "utc": utcnow()}
        save_json(output / "summary.json", report, immutable=True)
        return report


def search_all(study, preflight_dir):
    manifest = verify(study, frozen=True)
    pre = read_json(Path(preflight_dir) / "summary.json")
    if pre.get("status") != "passed" or pre.get("returned_model") != "MiniMax-M3":
        raise ValueError("MiniMax M3 preflight did not pass")
    study = Path(study).resolve()
    (study / "dispatch").mkdir(parents=True, exist_ok=True)
    consecutive = 0
    for job in manifest["jobs"]:
        status_path = study / "runs" / job["job_id"] / "status.json"
        if status_path.exists() and read_json(status_path).get("status") in TERMINAL:
            status = read_json(status_path)
        else:
            print(json.dumps({"event": "job_start", "job": job["job_id"], "utc": utcnow()}), flush=True)
            snapshot = read_json(study / manifest["data"][str(job["data_block"])] ["search"]["path"])
            params = {"temperature": manifest["protocol"]["temperature"],
                      "planner_max_tokens": manifest["protocol"]["planner_max_tokens"],
                      "coder_max_tokens": manifest["protocol"]["coder_max_tokens"],
                      "timeout_seconds": manifest["protocol"]["timeout_seconds"],
                      "token_budget": job["token_budget"], "request_limit": job["request_limit"],
                      "wall_limit_seconds": job["wall_limit_seconds"]}
            try:
                transport = HTTPTransport(job["provider"], job["model"], params["timeout_seconds"])
                result = run_search(job, snapshot, study / "runs" / job["job_id"],
                                    {"manifest_sha256": manifest["manifest_sha256"]}, params, transport, mode="live")
                status = {"status": result["status"], "summary": result.get("summary"),
                          "usage": result.get("usage")}
            except (IndeterminateCall, ProviderFailure, OSError, TimeoutError, BudgetStop) as exc:
                status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                          "no_automatic_retry": True}
                save_json(status_path, status)
            print(json.dumps({"event": "job_terminal", "job": job["job_id"],
                              "status": status["status"], "utc": utcnow()}), flush=True)
        save_json(study / "dispatch" / f"{job['job_id']}.json",
                  {"job_id": job["job_id"], "status": status["status"], "utc": utcnow()}, immutable=True)
        consecutive = consecutive + 1 if status["status"] == "infrastructure_incomplete" else 0
        if consecutive >= 2:
            save_json(study / "dispatch" / "halt.json",
                      {"reason": "two_consecutive_infrastructure_failures", "after_job": job["job_id"], "utc": utcnow()}, immutable=True)
            break


def test_all(study):
    manifest = verify(study, frozen=True)
    study = Path(study).resolve()
    statuses = {job["job_id"]: read_json(study / "runs" / job["job_id"] / "status.json")
                if (study / "runs" / job["job_id"] / "status.json").exists() else {}
                for job in manifest["jobs"]}
    if any(v.get("status") not in TERMINAL for v in statuses.values()) and not (study / "dispatch" / "halt.json").exists():
        raise ValueError("S2 searches are not all terminal")
    for job in manifest["jobs"]:
        if statuses[job["job_id"]].get("status") != "search_complete_test_not_run":
            continue
        run_dir = study / "runs" / job["job_id"]
        readout = read_json(run_dir / "selection_frozen.json")
        snapshot = read_json(study / manifest["data"][str(job["data_block"])] ["test"]["path"])
        by_id = {p["id"]: p for p in read_json(run_dir / "checkpoint.json")["seeds"]}
        # Candidate code is retained in the frozen readout.  The test stage is
        # deliberately independent of search replay and opens only test data.
        for p in read_json(run_dir / "selection_frozen.json")["programs"]:
            by_id[p["id"]] = p
        best = next(p for p in read_json(run_dir / "selection_frozen.json")["programs"] if p["id"] == readout["best_id"])
        seed = next(p for p in read_json(run_dir / "selection_frozen.json")["programs"] if p["id"] == readout["seed_best_id"])
        output = study / "tests" / f"{job['job_id']}.json"
        report = {"binding": {"readout_sha256": file_sha(run_dir / "selection_frozen.json"),
                               "test_snapshot_sha256": digest(snapshot)},
                  "config": read_json(run_dir / "config.json"), "evaluated_utc": utcnow(),
                  "best_id": readout["best_id"], "seed_best_id": readout["seed_best_id"],
                  "selected_on": "validation", "primary_test_gap": None,
                  "primary_test_valid": False, "seed_test_gap": None,
                  "test_failure_penalty": 1.0, "selector": None, "new_model_calls": 0}
        try:
            primary = evaluate_test(best["code"], snapshot)
            seed_eval = evaluate_test(seed["code"], snapshot)
            report.update(primary_test_gap=primary["loss"] if primary["valid"] else 1.0,
                          primary_test_valid=primary["valid"], seed_test_gap=seed_eval["loss"] if seed_eval["valid"] else 1.0,
                          evaluations={"best": primary, "seed": seed_eval})
        except Exception as exc:
            report.update(primary_test_gap=1.0, seed_test_gap=1.0,
                          test_error_type=type(exc).__name__, evaluations={})
        save_json(output, report, immutable=True)
    return {"status": "test_complete", "jobs": len(manifest["jobs"])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("preflight"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("search-all"); p.add_argument("--study", type=Path, required=True); p.add_argument("--preflight", type=Path, required=True); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("test-all"); p.add_argument("--study", type=Path, required=True)
    p = sub.add_parser("verify"); p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output)
        print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]), "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
        print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]), "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "preflight":
        result = preflight(args.output); print(json.dumps(result))
        if result["status"] != "passed": raise SystemExit(2)
    elif args.command == "search-all":
        search_all(args.study, args.preflight)
    elif args.command == "test-all":
        print(json.dumps(test_all(args.study)))
    else:
        result = verify(args.study); print(json.dumps({"status": result["status"], "jobs": len(result["jobs"])}))


if __name__ == "__main__":
    main()

