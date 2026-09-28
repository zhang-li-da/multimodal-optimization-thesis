"""Prepare, freeze, execute and read out E2 component validation."""
from __future__ import annotations

import copy
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import hashlib
import json
from pathlib import Path
import random
import subprocess
import time

from chapter6_demo import benchmarks
from chapter6_demo.agent_search.s3_tsp_r3.study import TERMINAL
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, environment, file_sha, git, read_json, run_lock, save_json, source_record, utcnow
from chapter6_demo.v12_2.data import content_hash

from .runner import run_search
from .service import DiagnosticDurableCalls, DiagnosticHTTPTransport, GlobalPauseGate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL = HERE / "protocol.final.json"


def tooling_source(commit=None, names=None):
    names = names or ["__init__.py", "controller.py", "runner.py", "service.py", "study.py", "protocol.final.json"]
    files = {}
    for name in names:
        path = f"experiments/chapter6/agent_search/component_validation/{name}"
        if commit:
            data = subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=ROOT)
        else:
            data = (HERE / name).read_bytes()
        files[path] = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
    return {"format": "component-validation-source-files-lf-sha256-v1", "files": files,
            "sha256": digest(files)}


def jobs(protocol):
    groups = protocol["e2"]["groups"]
    out = []
    for block in protocol["e2"]["blocks"]:
        for seed_label in protocol["e2"]["search_seeds"]:
            for group in groups:
                out.append({
                    "job_id": f"{group['id'].lower()}-b{block}-s{seed_label}",
                    "provider": protocol["model"]["provider"], "model": protocol["model"]["requested_model"],
                    "arm_id": group["id"], "policy": group["id"],
                    "protection": group["scheduling_priority"],
                    "scheduling_priority": group["scheduling_priority"],
                    "eviction_protection": group["eviction_protection"],
                    "role": "e2_protection_factorial", "task": protocol["task"],
                    "data_block": block, "search_seed": block * 1000 + seed_label,
                    "search_seed_label": seed_label, "steps": protocol["e2"]["fixed_slots"]["total"],
                    "token_budget": protocol["generation"]["token_budget_per_job"],
                    "request_limit": protocol["generation"]["request_limit_per_job"],
                    "wall_limit_seconds": protocol["generation"]["wall_limit_seconds_per_job"],
                    "capacity": protocol["e2"]["active_pool_capacity"],
                    "grant": protocol["e2"]["trial_slots_per_new_direction"],
                    "maximum_direction_attempts": protocol["e2"]["maximum_lineage_slots"],
                    "quality_tolerance": protocol["e0"]["quality_tolerance"],
                    "gain_epsilon": protocol["e0"]["gain_epsilon"],
                    "behavior_radius": protocol["e0"]["behavior_radius"],
                    "adaptive_unit_steps": 2,
                })
    random.Random(9273101).shuffle(out)
    return out


def _old_data():
    ids, hashes = set(), set()
    for block in range(3, 40):
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                ids.add(item["id"]); hashes.add(content_hash(item))
    return ids, hashes


def prepare(output):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new component-validation draft directory")
    protocol = read_json(PROTOCOL)
    old_ids, old_hashes = _old_data()
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    output.mkdir(parents=True)
    files, splits, count = {}, {}, 0
    for block in protocol["e2"]["blocks"]:
        current = {split: copy.deepcopy(benchmarks._instances_cached(
            "tsp", split, benchmarks.V12_TSP_PROFILE, block))
                   for split in ("probe", "validation", "test")}
        for split, items in current.items():
            for item in items:
                hashed = content_hash(item)
                if item["id"] in seen_ids or hashed in seen_hashes:
                    raise ValueError(f"Data overlap at block {block}/{split}/{item['id']}")
                seen_ids.add(item["id"]); seen_hashes.add(hashed); count += 1
        splits[str(block)] = {key: {"count": len(value), "sha256": digest(value)}
                              for key, value in current.items()}
        search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                  "probe": current["probe"], "validation": current["validation"]}
        test = {"profile": benchmarks.V12_TSP_PROFILE, "block": block, "test": current["test"]}
        files[str(block)] = {}
        for role, value in (("search", search), ("test", test)):
            rel = f"data/{role}-b{block}.json"
            save_json(output / rel, value, immutable=True)
            files[str(block)][role] = {"path": rel, "sha256": file_sha(output / rel)}
    manifest = {
        "schema": "chapter6-component-validation-study-v1", "status": "DRAFT_NOT_EXECUTABLE",
        "created_utc": utcnow(), "study_id": protocol["study_id"],
        "source_commit": git("rev-parse", "HEAD"), "source": source_record(),
        "tooling_source": tooling_source(), "environment": environment(), "protocol": protocol,
        "protocol_file": {"path": PROTOCOL.relative_to(ROOT).as_posix(), "sha256": file_sha(PROTOCOL)},
        "data": files, "splits": splits, "jobs": jobs(protocol), "new_model_calls": 0,
        "overlap_check": {"old_instances_checked": len(old_ids), "new_instances": count,
                          "id_or_exact_coordinate_collisions": 0,
                          "geometric_equivalence_checked": False},
    }
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study, *, frozen=False, roles=None):
    study = Path(study).resolve()
    manifest = read_json(study / "manifest.json")
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]:
        raise ValueError("Component manifest digest mismatch")
    if manifest["protocol"] != read_json(PROTOCOL):
        raise ValueError("Component protocol differs from committed source")
    tooling_matches = manifest["tooling_source"] == tooling_source()
    if not tooling_matches:
        # A frozen historical archive may predate a guarded runner update.
        # Validate its exact recorded files against its recorded Git commit,
        # rather than silently accepting today's source as a replay match.
        recorded_files = list(manifest["tooling_source"].get("files", {}))
        old_names = [Path(path).name for path in recorded_files]
        try:
            tooling_matches = (tooling_source(manifest.get("source_commit"), old_names) ==
                               manifest["tooling_source"])
        except (OSError, subprocess.CalledProcessError):
            tooling_matches = False
    if manifest["source"] != source_record() or not tooling_matches:
        raise ValueError("Component source differs; create a new study version")
    if manifest["jobs"] != jobs(manifest["protocol"]):
        raise ValueError("Component job matrix differs")
    if frozen and manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("Component study is not frozen")
    selected_roles = set(roles) if roles is not None else {"search", "test"}
    for block, role_records in manifest["data"].items():
        for role, rec in role_records.items():
            if role not in selected_roles:
                continue
            path = (study / rec["path"]).resolve()
            if not path.is_relative_to(study) or file_sha(path) != rec["sha256"]:
                raise ValueError("Component data snapshot changed")
    return manifest


def freeze(draft, output):
    draft, output = Path(draft).resolve(), Path(output).resolve()
    if output.exists() or git("status", "--porcelain"):
        raise ValueError("Freeze requires a clean committed source and a new output directory")
    d = verify(draft)
    if read_json(PROTOCOL)["status"] != "FINAL_PROTOCOL_PENDING_EXECUTION":
        raise ValueError("Protocol is not final")
    for block, roles in d["data"].items():
        for _, rec in roles.items():
            target = output / rec["path"]; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((draft / rec["path"]).read_bytes())
    manifest = {**d, "status": "FROZEN_PENDING_EXECUTION", "created_utc": utcnow(),
                "source_commit": git("rev-parse", "HEAD")}
    manifest.pop("manifest_sha256", None); manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def preflight(output):
    output = Path(output).resolve(); protocol = read_json(PROTOCOL)
    config = {"purpose": "engineering_connectivity_only_not_search",
              "provider": protocol["model"]["provider"], "model": protocol["model"]["requested_model"],
              "parameters": {"temperature": 0.0}, "source": source_record(),
              "tooling_source": tooling_source(), "environment": environment()}
    with run_lock(output):
        save_json(output / "config.json", config, immutable=True)
        calls = DiagnosticDurableCalls(
            output, config,
            DiagnosticHTTPTransport(config["provider"], config["model"], 120,
                                    min_interval_seconds=float(protocol.get("execution", {}).get(
                                        "min_request_interval_seconds", 1.0))))
        try:
            response = calls.complete(0, "connectivity", "You are a coding assistant.",
                                      'Return exactly the JSON object {"ready":true}.', 512)
            report = {"status": "passed" if response["returned_model"] == config["model"] else "model_identity_unconfirmed",
                      "requested_model": config["model"], "returned_model": response["returned_model"],
                      "response_text_present": bool(response["text"]), "usage": calls.usage(),
                      "new_search_runs": 0, "utc": utcnow()}
        except (IndeterminateCall, ProviderFailure) as exc:
            report = {"status": "failed", "error_type": type(exc).__name__,
                      "error": str(exc), "diagnostics": getattr(exc, "diagnostics", None),
                      "usage": calls.usage(), "new_search_runs": 0, "utc": utcnow()}
        save_json(output / "summary.json", report, immutable=True); return report


def _latest_diagnostics(run_dir):
    diagnostics = None
    for call_state in sorted((Path(run_dir) / "calls").glob("*/state.json")):
        diagnostics = read_json(call_state).get("diagnostics") or diagnostics
    return diagnostics


def run_one(job, study, manifest):
    study = Path(study); run_dir = study / "runs" / job["job_id"]
    status_path = run_dir / "status.json"
    if status_path.exists() and read_json(status_path).get("status") in TERMINAL:
        status = read_json(status_path)
    else:
        snapshot = read_json(study / manifest["data"][str(job["data_block"])] ["search"]["path"])
        params = {"temperature": manifest["protocol"]["generation"]["temperature"],
                  "planner_max_tokens": manifest["protocol"]["generation"]["planner_max_tokens"],
                  "coder_max_tokens": manifest["protocol"]["generation"]["coder_max_tokens"],
                  "timeout_seconds": manifest["protocol"]["generation"]["timeout_seconds"],
                  "token_budget": job["token_budget"], "request_limit": job["request_limit"],
                  "wall_limit_seconds": job["wall_limit_seconds"]}
        try:
            transport = DiagnosticHTTPTransport(
                job["provider"], job["model"], params["timeout_seconds"],
                min_interval_seconds=float(manifest["protocol"].get("execution", {}).get(
                    "min_request_interval_seconds", 1.0)))
            result = run_search(job, snapshot, run_dir, {"manifest_sha256": manifest["manifest_sha256"]},
                                params, transport, mode="live")
            diagnostics = _latest_diagnostics(run_dir)
            status = {"status": result["status"], "summary": result.get("summary"),
                      "usage": result.get("usage"), "diagnostics": diagnostics}
        except (IndeterminateCall, ProviderFailure, OSError, TimeoutError) as exc:
            status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                      "error": str(exc), "diagnostics": getattr(exc, "diagnostics", None),
                      "no_automatic_retry": True}
            save_json(status_path, status)
    save_json(study / "dispatch" / f"{job['job_id']}.json",
              {"job_id": job["job_id"], "status": status["status"],
               "diagnostics": status.get("diagnostics"), "utc": utcnow()})
    return status


def search_all(study, preflight_dir):
    manifest = verify(study, frozen=True, roles=("search",))
    pre = read_json(Path(preflight_dir) / "summary.json")
    if pre.get("status") != "passed" or pre.get("returned_model") != "MiniMax-M3":
        raise ValueError("MiniMax M3 preflight did not pass")
    study = Path(study).resolve(); (study / "dispatch").mkdir(parents=True, exist_ok=True)
    if (study / "dispatch" / "halt.json").exists():
        raise ValueError("Study explicitly halted; no restart or replacement")
    execution = manifest["protocol"].get("execution", {})
    workers = int(execution.get("max_concurrency", 6))
    workers = max(1, min(workers, len(manifest["jobs"])))
    jobs = list(manifest["jobs"])
    pending = iter(jobs)
    active = {}
    dispatch_interval = max(0.0, float(execution.get("dispatch_interval_seconds", 1.0)))
    last_dispatch = 0.0
    pause_gate = GlobalPauseGate(execution.get("global_pause_after_rate_limits", 2))
    paused = None

    def mark_unstarted(job, reason, diagnostics=None):
        run_dir = study / "runs" / job["job_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        save_json(run_dir / "status.json", {
            "status": "not_started", "reason": reason,
            "diagnostics": diagnostics, "no_automatic_retry": True,
        })
        save_json(study / "dispatch" / f"{job['job_id']}.json", {
            "job_id": job["job_id"], "status": "not_started", "reason": reason,
            "diagnostics": diagnostics, "utc": utcnow()})

    def fill(pool):
        """Fill only available worker slots; pending jobs remain untouched on pause."""
        nonlocal paused, last_dispatch
        while paused is None and len(active) < workers:
            try:
                job = next(pending)
            except StopIteration:
                break
            wait = dispatch_interval - (time.monotonic() - last_dispatch)
            if wait > 0:
                time.sleep(wait)
            future = pool.submit(run_one, job, str(study), manifest)
            active[future] = job
            last_dispatch = time.monotonic()

    with ProcessPoolExecutor(max_workers=workers) as pool:
        fill(pool)
        while active:
            completed, _ = wait(tuple(active), return_when=FIRST_COMPLETED)
            for future in completed:
                job = active.pop(future)
                status = future.result()
                diagnostics = status.get("diagnostics") if isinstance(status, dict) else None
                gate = pause_gate.observe(diagnostics)
                if gate["pause"] and paused is None:
                    paused = {"reason": "provider_global_pause", "trigger_job": job["job_id"],
                              "category": gate["category"], "diagnostics": diagnostics,
                              "consecutive_rate_limits": gate["consecutive_rate_limits"],
                              "pending_jobs": None,
                              "utc": utcnow()}
            if paused is None:
                fill(pool)
        if paused is not None:
            # The iterator cannot be counted without consuming it.  Mark the
            # remaining jobs by their known manifest order and existing status.
            dispatched = {entry.get("job_id") for entry in
                          (read_json(path) for path in (study / "dispatch").glob("*.json"))}
            paused["pending_jobs"] = sum(job["job_id"] not in dispatched for job in jobs)
            for job in jobs:
                if job["job_id"] not in dispatched:
                    mark_unstarted(job, "global_provider_pause", paused.get("diagnostics"))
            save_json(study / "dispatch" / "global_pause.json", paused)
            save_json(study / "dispatch" / "halt.json", {
                "reason": "provider_global_pause", "category": paused.get("category"),
                "diagnostics": paused.get("diagnostics"), "utc": paused.get("utc")})


def test_all(study):
    manifest = verify(study, frozen=True, roles=("search",)); study = Path(study).resolve()
    statuses = {job["job_id"]: read_json(study / "runs" / job["job_id"] / "status.json")
                if (study / "runs" / job["job_id"] / "status.json").exists() else {}
                for job in manifest["jobs"]}
    terminal = set(TERMINAL) | {"not_started"}
    if any(v.get("status") not in terminal for v in statuses.values()):
        raise ValueError("Search jobs are not all terminal")
    manifest = verify(study, frozen=True); tested = 0
    from .evaluator import evaluate_test
    for job in manifest["jobs"]:
        run_dir = study / "runs" / job["job_id"]
        readout_path = run_dir / "selection_frozen.json"
        if not readout_path.exists():
            continue
        readout = read_json(readout_path); snapshot = read_json(study / manifest["data"][str(job["data_block"])] ["test"]["path"])
        programs = {p["id"]: p for p in readout["programs"]}; best = programs[readout["best_id"]]; seed = programs[readout["seed_best_id"]]
        output = study / "tests" / f"{job['job_id']}.json"; binding = {"readout_sha256": file_sha(readout_path), "test_snapshot_sha256": digest(snapshot)}
        if output.exists(): tested += 1; continue
        report = {"binding": binding, "search_status": statuses[job["job_id"]]["status"], "config": read_json(run_dir / "config.json"),
                  "evaluated_utc": utcnow(), "best_id": readout["best_id"], "seed_best_id": readout["seed_best_id"],
                  "selected_on": "validation", "primary_test_gap": None, "seed_test_gap": None, "new_model_calls": 0}
        try:
            primary, seed_eval = evaluate_test(best["code"], snapshot), evaluate_test(seed["code"], snapshot)
            report.update(primary_test_gap=primary["loss"] if primary["valid"] else 1.0, primary_test_valid=primary["valid"],
                          seed_test_gap=seed_eval["loss"] if seed_eval["valid"] else 1.0, evaluations={"best": primary, "seed": seed_eval})
        except Exception as exc:
            report.update(primary_test_gap=1.0, seed_test_gap=1.0, test_error_type=type(exc).__name__, evaluations={})
        save_json(output, report, immutable=True); tested += 1
    return {"status": "test_complete", "planned_jobs": len(manifest["jobs"]), "tested_jobs": tested}


def main():
    parser = __import__("argparse").ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "freeze", "preflight", "search-all", "test-all", "verify"):
        p = sub.add_parser(command)
        if command == "prepare": p.add_argument("--output", type=Path, required=True)
        elif command == "freeze": p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
        elif command == "preflight": p.add_argument("--output", type=Path, required=True)
        elif command == "search-all": p.add_argument("--study", type=Path, required=True); p.add_argument("--preflight", type=Path, required=True); p.add_argument("--live", action="store_true", required=True)
        else: p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output)
        print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]), "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
        print(json.dumps({"status": result["status"], "jobs": len(result["jobs"]), "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "preflight":
        result = preflight(args.output); print(json.dumps(result));
        if result["status"] != "passed": raise SystemExit(2)
    elif args.command == "search-all": search_all(args.study, args.preflight)
    elif args.command == "test-all": print(json.dumps(test_all(args.study)))
    else: print(json.dumps({"status": "verified", "jobs": len(verify(args.study)["jobs"])}))


if __name__ == "__main__": main()
