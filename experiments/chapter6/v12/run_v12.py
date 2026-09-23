"""Freeze and run the 18 preregistered Chapter 6 v1.2 screening jobs."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import threading

from chapter6_demo.benchmarks import V12_TSP_PROFILE, instances, split_fingerprint
from chapter6_demo.v12_controller import V12_METHODS, v12_source_fingerprint

MODEL_CELLS=(
    ("alibaba-token-plan-cn","qwen3.7-plus"),
    ("minimax-cn-coding-plan","MiniMax-M3"),
)
BLOCKS=(0,1,2)
ORDER_SEED=9242026
RUN_NAME="screening-20260924-r1"


def planned_jobs():
    jobs=[]
    for provider,model in MODEL_CELLS:
        for method in V12_METHODS:
            for block in BLOCKS:
                jobs.append({"job_id":f"{provider.split('-')[0]}-{method}-b{block}",
                    "provider":provider,"model":model,"method":method,"block":block,
                    "task":"tsp","steps":8,"token_budget":None})
    order=list(range(len(jobs)))
    random.Random(ORDER_SEED).shuffle(order)
    return [jobs[i] for i in order]


def _git(*args):
    return subprocess.check_output(["git",*args],text=True,encoding="utf-8").strip()


def _save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    temp.replace(path)


def create_manifest(root):
    repo=Path(__file__).resolve().parents[3]
    if Path.cwd().resolve()!=repo:
        raise RuntimeError(f"Run from repository root: {repo}")
    if _git("status","--porcelain"):
        raise RuntimeError("Commit the frozen protocol, controller, and tests before making the run manifest.")
    old_profile=os.environ.get("CHAPTER6_BENCHMARK_PROFILE")
    old_block=os.environ.get("CHAPTER6_DATA_BLOCK")
    splits={}
    try:
        os.environ["CHAPTER6_BENCHMARK_PROFILE"]=V12_TSP_PROFILE
        for block in BLOCKS:
            os.environ["CHAPTER6_DATA_BLOCK"]=str(block)
            splits[str(block)]={split:{"sha256":split_fingerprint("tsp",split),
                "instance_ids":[item["id"] for item in instances("tsp",split)]}
                for split in ("probe","validation","test")}
    finally:
        for name,value in (("CHAPTER6_BENCHMARK_PROFILE",old_profile),("CHAPTER6_DATA_BLOCK",old_block)):
            if value is None: os.environ.pop(name,None)
            else: os.environ[name]=value
    manifest={"schema_version":1,"study_id":root.name,
        "created_utc":datetime.now(timezone.utc).isoformat(),
        "status":"preregistered mechanism screening; no outcome-based reruns",
        "source_commit":_git("rev-parse","HEAD"),
        "source_fingerprint_sha256":v12_source_fingerprint(),
        "protocol_sha256":hashlib.sha256(Path("experiments/chapter6/v12/preregistration.md").read_bytes()).hexdigest(),
        "python":sys.version,"platform":platform.platform(),"profile":V12_TSP_PROFILE,
        "splits":splits,"design":{"models":[list(x) for x in MODEL_CELLS],
            "methods":list(V12_METHODS),"blocks":list(BLOCKS),"steps":8,
            "factorial_runs":18,"analysis_seed":ORDER_SEED},
        "jobs":planned_jobs()}
    stable={k:v for k,v in manifest.items() if k not in ("created_utc","manifest_sha256")}
    manifest["manifest_sha256"]=hashlib.sha256(json.dumps(stable,ensure_ascii=False,
        sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return manifest


def execute_job(job,root):
    directory=root/"runs"/job["job_id"]
    directory.mkdir(parents=True,exist_ok=True)
    if (directory/"result.json").exists():
        result=json.loads((directory/"result.json").read_text(encoding="utf-8"))
        if result["config"]["source_fingerprint"]==v12_source_fingerprint():
            return {"job_id":job["job_id"],"status":"cached_result","exit_code":0}
        return {"job_id":job["job_id"],"status":"invalid_existing_result","exit_code":2}
    command=[sys.executable,"-m","chapter6_demo.discovery","--task","tsp",
        "--method",job["method"],"--seed",str(job["block"]),"--steps","8",
        "--provider",job["provider"],"--model",job["model"],"--output",str(directory)]
    env=os.environ.copy()
    env["CHAPTER6_BENCHMARK_PROFILE"]=V12_TSP_PROFILE
    env["CHAPTER6_DATA_BLOCK"]=str(job["block"])
    with (directory/"console.log").open("a",encoding="utf-8") as log:
        process=subprocess.run(command,cwd=Path.cwd(),env=env,stdout=log,stderr=subprocess.STDOUT,check=False)
    status="completed" if process.returncode==0 and (directory/"result.json").exists() else "failed"
    return {"job_id":job["job_id"],"status":status,"exit_code":process.returncode}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",default=f"experiments/chapter6/v12/{RUN_NAME}")
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args()
    root=Path(args.output)
    expected=create_manifest(root)
    root.mkdir(parents=True,exist_ok=True)
    manifest_path=root/"manifest.json"
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("manifest_sha256")!=expected["manifest_sha256"]:
            raise SystemExit("Existing v1.2 manifest differs; use a new output directory.")
    else:
        manifest=expected
        _save(manifest_path,manifest)
    if args.dry_run:
        print(json.dumps({"runs":len(manifest["jobs"]),"manifest_sha256":manifest["manifest_sha256"],
            "source_fingerprint_sha256":manifest["source_fingerprint_sha256"]},ensure_ascii=False))
        return
    status_path=root/"status.json"
    previous=json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else []
    outcomes={row["job_id"]:row for row in previous}
    pending=[job for job in manifest["jobs"] if job["job_id"] not in outcomes]
    lock=threading.Lock()
    def lane(provider):
        for job in pending:
            if job["provider"]!=provider: continue
            try:
                row=execute_job(job,root)
            except Exception as exc:
                row={"job_id":job["job_id"],"status":"runner_error",
                    "error_type":type(exc).__name__,"error":str(exc)[:180],"exit_code":2}
            with lock:
                outcomes[job["job_id"]]=row
                _save(status_path,[outcomes[j["job_id"]] for j in manifest["jobs"] if j["job_id"] in outcomes])
                print(json.dumps({"completed":len(outcomes),"planned":len(manifest["jobs"]),**row},ensure_ascii=False),flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(lane,provider) for provider,_ in MODEL_CELLS]
        for future in futures: future.result()
    print(json.dumps({"results":len(list(root.glob("runs/*/result.json"))),"output":str(root)}))


if __name__=="__main__":
    main()
