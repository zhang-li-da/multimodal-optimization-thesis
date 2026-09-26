"""Prepare, freeze, execute and read out the S1 natural-competition study."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

from chapter6_demo import benchmarks
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import (ROOT, CONTROLLER, digest, environment, file_sha,
    git, read_json, run_lock, save_json, source_record, utcnow)
from chapter6_demo.v12_2.data import content_hash, evaluate_test, evaluate_search, split_path
from chapter6_demo.v12_2.runner import run_search
from chapter6_demo.v12_2.calls import HTTPTransport, IndeterminateCall, ProviderFailure

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.final.json"
OLD = ROOT / "experiments/chapter6/v12_3/studies/minimax-20260926-r1"
S0 = ROOT / "experiments/chapter6/agent_search/studies/s0-minimax-output-acceptance-20260926-r3"
S0_RESULT = ROOT / "experiments/chapter6/agent_search/results/s0-minimax-output-acceptance-20260926-r3/summary.json"
TERMINAL = {"search_complete_test_not_run", "infrastructure_incomplete"}


def tooling_source():
    paths = [HERE / "__init__.py", HERE / "protocol.final.json", HERE / "study.py", HERE / "analyze.py"]
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in paths}
    return {"format": "s1-named-source-files-lf-sha256-v1", "files": files, "sha256": digest(files)}


def jobs(protocol):
    values = [{"job_id": f"minimax-{controller}-b{block}-s{seed}",
        "provider": protocol["provider"], "model": protocol["model"],
        "controller": controller, "controller_class": CONTROLLER, "task": "tsp",
        "data_block": block, "search_seed": block * 100 + seed,
        "search_seed_label": seed, "steps": protocol["steps"], "token_budget": None}
        for block in protocol["blocks"] for seed in protocol["search_seeds"] for controller in protocol["controllers"]]
    random.Random(protocol["order_seed"]).shuffle(values)
    return values


def load_old_hashes():
    ids, hashes = set(), set()
    if OLD.exists():
        manifest = read_json(OLD / "manifest.json")
        for block, items in manifest["data"].items():
            for role in ("search", "test"):
                path = OLD / items[role]["path"]
                data = read_json(path)
                split = data.get("test", data.get("probe", []) + data.get("validation", []))
                for item in split:
                    ids.add(item["id"]); hashes.add(content_hash(item))
    return ids, hashes


def generate_data(directory, protocol):
    old_ids, old_hashes = load_old_hashes()
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    files, splits = {}, {}
    for block in protocol["blocks"]:
        current = {split: copy.deepcopy(benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block)) for split in ("probe", "validation", "test")}
        for split, items in current.items():
            for item in items:
                if item["id"] in seen_ids or content_hash(item) in seen_hashes:
                    raise ValueError(f"Data overlap at block {block}/{split}/{item['id']}")
                seen_ids.add(item["id"]); seen_hashes.add(content_hash(item))
        search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                  "probe": current["probe"], "validation": current["validation"]}
        test = {"profile": benchmarks.V12_TSP_PROFILE, "block": block, "test": current["test"]}
        files[str(block)] = {}
        for role, value in (("search", search), ("test", test)):
            rel = f"data/{role}-b{block}.json"
            save_json(directory / rel, value, immutable=True)
            files[str(block)][role] = {"path": rel, "sha256": file_sha(directory / rel)}
        splits[str(block)] = {k: {"count": len(v), "sha256": digest(v)} for k, v in current.items()}
    return files, splits, {"old_instances_checked": len(old_ids), "new_instances": sum(v["probe"]["count"] + v["validation"]["count"] + v["test"]["count"] for v in splits.values()), "id_or_exact_coordinate_collisions": 0}


def s0_gate():
    result = read_json(S0_RESULT)
    if not result["result"]["ready_for_s1"]:
        raise ValueError("S0 r3 gate is not passed")
    return {"study_id": result["study_id"], "manifest_sha256": result["manifest_sha256"], "result": result["result"]}


def prepare(output):
    output = Path(output).resolve()
    if output.exists(): raise ValueError("Use a new S1 draft directory")
    protocol = read_json(PROTOCOL)
    if protocol["status"] != "FINAL_PROTOCOL_PENDING_EXECUTION": raise ValueError("Protocol is not final")
    if protocol["controllers"] != ["niche_fixed_dev", "relational_branch"]: raise ValueError("Unexpected controllers")
    output.mkdir(parents=True)
    files, splits, overlap = generate_data(output, protocol)
    manifest = {"schema": "chapter6-s1-study-v1", "status": "DRAFT_NOT_EXECUTABLE", "created_utc": utcnow(),
        "study_id": protocol["study_id"], "source_commit": git("rev-parse", "HEAD"),
        "source": source_record(), "tooling_source": tooling_source(), "environment": environment(),
        "protocol": protocol, "protocol_file": {"path": PROTOCOL.relative_to(ROOT).as_posix(), "sha256": file_sha(PROTOCOL)},
        "data": files, "splits": splits, "jobs": jobs(protocol), "s0_gate": s0_gate(), "overlap_check": overlap,
        "new_model_calls": 0}
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(directory, frozen=False):
    directory = Path(directory)
    m = read_json(directory / "manifest.json")
    if digest({k:v for k,v in m.items() if k != "manifest_sha256"}) != m["manifest_sha256"]: raise ValueError("Manifest digest mismatch")
    if m["protocol"] != read_json(PROTOCOL): raise ValueError("Protocol differs from manifest")
    if m["protocol_file"]["sha256"] != file_sha(PROTOCOL): raise ValueError("Protocol bytes differ")
    if m["tooling_source"] != tooling_source(): raise ValueError("S1 tooling source differs")
    if frozen and (m["status"] != "FROZEN_PENDING_EXECUTION" or m["environment"] != environment()): raise ValueError("S1 freeze/environment mismatch")
    if m["jobs"] != jobs(m["protocol"]): raise ValueError("Job order differs")
    for block in m["data"]:
        for role in ("search", "test"):
            path = (directory / m["data"][block][role]["path"]).resolve()
            if not path.is_relative_to(directory) or file_sha(path) != m["data"][block][role]["sha256"]: raise ValueError("Data bytes differ")
    return m


def freeze(draft, output):
    draft, output = Path(draft), Path(output)
    if output.exists() or git("status", "--porcelain"): raise ValueError("Freeze requires clean committed source and a new output")
    d = verify(draft)
    protocol = read_json(PROTOCOL)
    manifest = {**d, "status": "FROZEN_PENDING_EXECUTION", "created_utc": utcnow(), "source_commit": git("rev-parse", "HEAD")}
    manifest["protocol_file"] = {"path": PROTOCOL.relative_to(ROOT).as_posix(), "sha256": file_sha(PROTOCOL)}
    manifest.pop("manifest_sha256", None)
    for block in d["data"]:
        for role in ("search", "test"):
            source = draft / d["data"][block][role]["path"]
            target = output / d["data"][block][role]["path"]
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(source.read_bytes())
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def s0_ready(manifest):
    gate = manifest["s0_gate"]
    return gate["study_id"] == "s0-minimax-output-acceptance-20260926-r3" and gate["result"]["ready_for_s1"]


def search_one(study, job_id):
    manifest = verify(study, frozen=True)
    if not s0_ready(manifest): raise ValueError("S0 gate missing")
    job = next(j for j in manifest["jobs"] if j["job_id"] == job_id)
    run_dir = Path(study) / "runs" / job_id
    status_file = run_dir / "status.json"
    if status_file.exists() and read_json(status_file).get("status") in TERMINAL:
        return read_json(status_file)
    snapshot = read_json(split_path(study, manifest, job["data_block"], "search"))
    parameters = {"temperature": manifest["protocol"]["temperature"], "planner_max_tokens": manifest["protocol"]["planner_max_tokens"], "coder_max_tokens": manifest["protocol"]["coder_max_tokens"], "timeout_seconds": manifest["protocol"]["timeout_seconds"]}
    client = HTTPTransport(job["provider"], job["model"], manifest["protocol"]["timeout_seconds"])
    with run_lock(Path(study) / "lanes" / job["provider"]):
        result = run_search(job, snapshot, run_dir, {"manifest_sha256": manifest["manifest_sha256"]}, parameters, client, mode="live")
    return {"status": result["status"], "summary": result.get("summary"), "usage": result.get("usage")}


def dispatch(study):
    manifest = verify(study, frozen=True)
    (Path(study) / "dispatch").mkdir(parents=True, exist_ok=True)
    consecutive = 0
    for job in manifest["jobs"]:
        path = Path(study) / "runs" / job["job_id"] / "status.json"
        if path.exists() and read_json(path).get("status") in TERMINAL:
            status = read_json(path)
        else:
            print(json.dumps({"event":"job_start","job":job["job_id"],"utc":utcnow()}), flush=True)
            try:
                result = search_one(study, job["job_id"])
                status = {"status": result["status"], "summary": result.get("summary"), "usage": result.get("usage")}
            except (IndeterminateCall, ProviderFailure, OSError, TimeoutError) as exc:
                status = {"status":"infrastructure_incomplete","error_type":type(exc).__name__,"no_automatic_retry":True}
                save_json(Path(study)/"runs"/job["job_id"]/'status.json', status)
            print(json.dumps({"event":"job_terminal","job":job["job_id"],"status":status["status"],"utc":utcnow()}), flush=True)
        save_json(Path(study)/"dispatch"/(job["job_id"]+'.json'), {"job_id":job["job_id"],"status":status["status"],"utc":utcnow()}, immutable=True)
        consecutive = consecutive + 1 if status["status"] == "infrastructure_incomplete" else 0
        if consecutive >= 2:
            save_json(Path(study)/"dispatch"/'halt.json', {"reason":"two_consecutive_infrastructure_failures","after_job":job["job_id"]}, immutable=True)
            break


def test_all(study):
    manifest = verify(study, frozen=True)
    statuses = []
    for job in manifest["jobs"]:
        p = Path(study)/"runs"/job["job_id"]/'status.json'
        statuses.append(read_json(p) if p.exists() else {})
    if any(s.get("status") not in TERMINAL for s in statuses) and not (Path(study)/'dispatch/halt.json').exists():
        raise ValueError("Search jobs not terminal")
    tests = Path(study)/'tests'; tests.mkdir(exist_ok=True)
    for job in manifest["jobs"]:
        run_dir=Path(study)/'runs'/job["job_id"]
        result_path=run_dir/'search_result.json'
        if not result_path.exists() or read_json(result_path)["status"] != "search_complete_test_not_run": continue
        test_snapshot=read_json(split_path(study,manifest,job["data_block"],"test"))
        out=tests/(job["job_id"]+'.json')
        evaluate_test_for_run(run_dir,test_snapshot,out)


def evaluate_test_for_run(run_dir,test_snapshot,out):
    from chapter6_demo.v12_2.test_stage import evaluate_frozen
    return evaluate_frozen(run_dir,test_snapshot,out)


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest='command',required=True)
    x=sub.add_parser('prepare');x.add_argument('--output',type=Path,required=True)
    x=sub.add_parser('freeze');x.add_argument('--draft',type=Path,required=True);x.add_argument('--output',type=Path,required=True)
    x=sub.add_parser('search-one');x.add_argument('--study',type=Path,required=True);x.add_argument('--job',required=True);x.add_argument('--live',action='store_true',required=True)
    x=sub.add_parser('search-all');x.add_argument('--study',type=Path,required=True);x.add_argument('--live',action='store_true',required=True)
    x=sub.add_parser('test-all');x.add_argument('--study',type=Path,required=True)
    x=sub.add_parser('verify');x.add_argument('--study',type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':
        with offline_only(): m=prepare(a.output); print(json.dumps({'status':m['status'],'jobs':len(m['jobs']),'manifest_sha256':m['manifest_sha256']}))
    elif a.command=='freeze':
        with offline_only(): m=freeze(a.draft,a.output); print(json.dumps({'status':m['status'],'jobs':len(m['jobs']),'manifest_sha256':m['manifest_sha256']}))
    elif a.command=='verify': print(json.dumps({'status':verify(a.study)['status'],'jobs':len(verify(a.study)['jobs'])}))
    elif a.command=='search-one': print(json.dumps(search_one(a.study,a.job)))
    elif a.command=='search-all': dispatch(a.study)
    else: test_all(a.study); print(json.dumps({'status':'test_complete'}))

if __name__=='__main__': main()
