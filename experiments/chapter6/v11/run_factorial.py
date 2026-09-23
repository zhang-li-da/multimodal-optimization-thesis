"""Execute the frozen v1.1 factorial while preserving each run's raw evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import threading

from chapter6_demo.discovery import METHODS, source_fingerprint
from chapter6_demo.benchmarks import instances, split_fingerprint


MODEL_CELLS = (
    ("alibaba-token-plan-cn", "qwen3.7-plus"),
    ("minimax-cn-coding-plan", "MiniMax-M3"),
)
TASKS = ("tsp", "binpack")
METHODS_SCREENING = ("niche", "relational", "relational_qp", "relational_rr", "relational_qp_rr")
REGIMES = ("slots8", "tokens30000")
BLOCKS = (0, 1, 2, 3, 4)
ORDER_SEED = 602023


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _atomic_json(path: Path, value) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def _job_id(provider: str, task: str, method: str, block: int, regime: str) -> str:
    short = "qwen" if provider.startswith("alibaba") else "minimax"
    return f"{short}-{task}-{method}-b{block}-{regime}"


def planned_jobs():
    jobs = []
    for provider, model in MODEL_CELLS:
        for task in TASKS:
            for method in METHODS_SCREENING:
                for block in BLOCKS:
                    for regime in REGIMES:
                        jobs.append({
                            "job_id": _job_id(provider, task, method, block, regime),
                            "provider": provider, "model": model, "task": task, "method": method,
                            "block": block, "regime": regime,
                            "steps": 8 if regime == "slots8" else 32,
                            "token_budget": None if regime == "slots8" else 30_000,
                        })
    order = list(range(len(jobs)))
    random.Random(ORDER_SEED).shuffle(order)
    return [jobs[i] for i in order]


def create_manifest(output: Path, run_name: str) -> dict:
    repo = Path(__file__).resolve().parents[3]
    if Path.cwd().resolve() != repo:
        raise RuntimeError(f"Run this module from the repository root: {repo}")
    dirty = _git("status", "--porcelain")
    if dirty:
        raise RuntimeError("The preregistered worktree must be clean before any live search calls.")
    source_commit = _git("rev-parse", "HEAD")
    prereg = repo / "experiments/chapter6/v11/preregistration.md"
    old_profile=os.environ.get("CHAPTER6_BENCHMARK_PROFILE")
    old_block=os.environ.get("CHAPTER6_DATA_BLOCK")
    splits={}
    try:
        os.environ["CHAPTER6_BENCHMARK_PROFILE"]="chapter6-v11-independent-v1"
        for block in BLOCKS:
            os.environ["CHAPTER6_DATA_BLOCK"]=str(block)
            splits[str(block)]={task:{split:{"sha256":split_fingerprint(task,split),
                "instance_ids":[x["id"] for x in instances(task,split)]}
                for split in ("probe","validation","test")} for task in TASKS}
    finally:
        for name,value in (("CHAPTER6_BENCHMARK_PROFILE",old_profile),("CHAPTER6_DATA_BLOCK",old_block)):
            if value is None: os.environ.pop(name,None)
            else: os.environ[name]=value
    manifest = {
        "schema_version": 1,
        "study_id": run_name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "preregistered live screening; no outcome-based exclusions",
        "source_commit": source_commit,
        "source_fingerprint_sha256": source_fingerprint(),
        "preregistration_sha256": hashlib.sha256(prereg.read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies":{name:version(name) for name in ("numpy","scikit-learn","pytest")},
        "splits":splits,
        "protocol": {
            "profile": "chapter6-v11-independent-v1",
            "block_seed_is_search_seed": True,
            "factorial": {"quality_protection": [False, True], "restart_correction": [False, True]},
            "additional_baseline": "niche",
            "per_call_output_ceilings": {"planner": 1800, "coder": 2000},
            "temperature": 0.7,
            "local_gain_epsilon": 1e-4,
            "maximum_local_credits_per_family": 2,
            "online_binpack_selector": "not evaluated; full-sequence descriptors leak future items",
            "provider_concurrency": 1,
            "total_input_output_token_ceiling": 30_000,
            "screening_runs": 200,
            "analysis_seed": 602023,
        },
        "jobs": planned_jobs(),
    }
    stable = {key: value for key, value in manifest.items() if key not in ("created_utc", "manifest_sha256")}
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
    return manifest


def execute_job(job: dict, root: Path, semaphore: threading.Semaphore) -> dict:
    run_dir = root / "runs" / job["job_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.json"
    if result_path.exists():
        try:
            cached = json.loads(result_path.read_text(encoding="utf-8"))
            expected_block = job["block"]
            if (cached["config"]["task"] == job["task"]
                    and cached["config"]["method"] == job["method"]
                    and cached["config"]["provider"] == job["provider"]
                    and cached["config"]["model"] == job["model"]
                    and cached["config"]["data_block"] == expected_block
                    and cached["config"]["source_fingerprint"] == source_fingerprint()
                    and cached["config"]["token_budget"] == job["token_budget"]
                    and cached["config"]["steps"] == job["steps"]):
                return {"job_id": job["job_id"], "status": "cached_result", "exit_code": 0,
                        "summary": cached["summary"]}
        except (KeyError, OSError, json.JSONDecodeError):
            pass
        return {"job_id": job["job_id"], "status": "invalid_existing_result", "exit_code": 2}

    cmd = [sys.executable, "-m", "chapter6_demo.discovery", "--task", job["task"],
           "--method", job["method"], "--seed", str(job["block"]),
           "--steps", str(job["steps"]), "--provider", job["provider"],
           "--model", job["model"], "--output", str(run_dir)]
    if job["token_budget"] is not None:
        cmd.extend(["--token-budget", str(job["token_budget"])])
    env = os.environ.copy()
    env["CHAPTER6_BENCHMARK_PROFILE"] = "chapter6-v11-independent-v1"
    env["CHAPTER6_DATA_BLOCK"] = str(job["block"])
    log_path = run_dir / "console.log"
    with semaphore:
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.run(cmd, cwd=Path.cwd(), env=env, stdout=log,
                                     stderr=subprocess.STDOUT, check=False)
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            status = "completed" if process.returncode == 0 else "completed_with_process_error"
            return {"job_id": job["job_id"], "status": status,
                    "exit_code": process.returncode, "summary": result["summary"]}
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return {"job_id": job["job_id"], "status": "failed_without_result",
            "exit_code": process.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="experiments/chapter6/v11/screening-20260923-r1")
    parser.add_argument("--dry-run", action="store_true", help="Write and validate the 200-run plan only.")
    args = parser.parse_args()
    root = Path(args.output)
    manifest_path = root / "manifest.json"
    status_path = root / "status.json"
    expected = create_manifest(root, root.name)
    root.mkdir(parents=True, exist_ok=True)
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
        if old.get("manifest_sha256") != expected["manifest_sha256"]:
            raise SystemExit("Existing manifest differs from the frozen source/protocol; use a new output directory.")
        manifest = old
    else:
        _atomic_json(manifest_path, expected)
        manifest = expected
    if args.dry_run:
        print(json.dumps({"manifest": str(manifest_path), "runs": len(manifest["jobs"]),
                          "manifest_sha256": manifest["manifest_sha256"]}, ensure_ascii=False))
        return

    prior = {}
    if status_path.exists():
        try:
            prior = {r["job_id"]: r for r in json.loads(status_path.read_text(encoding="utf-8"))}
        except (OSError, json.JSONDecodeError, KeyError):
            prior = {}
    locks = {provider: threading.Semaphore(1) for provider, _ in MODEL_CELLS}
    outcomes = dict(prior)
    pending = [j for j in manifest["jobs"] if j["job_id"] not in outcomes]
    status_lock=threading.Lock()
    def provider_lane(provider):
        for job in pending:
            if job["provider"]!=provider: continue
            try:
                result=execute_job(job,root,locks[provider])
            except Exception as exc:
                result={"job_id":job["job_id"],"status":"runner_error",
                        "error_type":type(exc).__name__,"error":str(exc)[:200],"exit_code":2}
            with status_lock:
                outcomes[job["job_id"]]=result
                _atomic_json(status_path,[outcomes[j["job_id"]] for j in manifest["jobs"] if j["job_id"] in outcomes])
                print(json.dumps({"completed":len(outcomes),"planned":len(manifest["jobs"]),
                      **{k:v for k,v in result.items() if k!="summary"}},ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(provider_lane,provider) for provider,_ in MODEL_CELLS]
        for future in as_completed(futures):
            future.result()
    if len(outcomes) < len(manifest["jobs"]) or any(r.get("exit_code", 1) != 0 for r in outcomes.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
