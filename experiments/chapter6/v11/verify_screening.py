"""Audit frozen screening results without issuing any model requests.

Run from the repository root. This post-search audit is deliberately separate
from the preregistered search and analysis source fingerprint.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time

from chapter6_demo.benchmarks import SEEDS, evaluate, instances, split_fingerprint
from chapter6_demo.discovery import (SearchState, _token_reservation, report_archive,
                                    source_fingerprint, QUALITY_TOLERANCE, _behavior_mode_count)


def verify(root: Path, output: Path, limit: int | None = None) -> dict:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    stable = {k: v for k,v in manifest.items() if k not in ("created_utc", "manifest_sha256")}
    digest = hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    if digest != manifest["manifest_sha256"]:
        raise ValueError("Manifest hash mismatch")
    if source_fingerprint() != manifest["source_fingerprint_sha256"]:
        raise ValueError("Audit must use the frozen search source files")
    jobs=sorted(manifest["jobs"],key=lambda job:(job["block"],job["task"],job["job_id"]))
    rows=[]
    missing=[]
    start=time.perf_counter()
    for job in jobs:
        directory=root/"runs"/job["job_id"]
        path=directory/"result.json"
        if not path.exists():
            missing.append(job["job_id"])
            continue
        if limit is not None and len(rows)>=limit: break
        result=json.loads(path.read_text(encoding="utf-8"))
        errors=[]
        def check(condition, message):
            if not condition: errors.append(message)
        os.environ["CHAPTER6_BENCHMARK_PROFILE"]="chapter6-v11-independent-v1"
        os.environ["CHAPTER6_DATA_BLOCK"]=str(job["block"])
        config=result["config"]
        check(config["source_fingerprint"]==manifest["source_fingerprint_sha256"],"source fingerprint")
        for key in ("provider","model","task","method","steps","token_budget"):
            check(config[key]==job[key],"config: "+key)
        check(config["data_block"]==job["block"]==config["seed"],"paired block / search seed")
        for split in ("probe","validation","test"):
            fingerprint=split_fingerprint(job["task"],split)
            check(config["splits"][split]==fingerprint,"split: "+split)
            check(manifest["splits"][str(job["block"])][job["task"]][split]["sha256"]==fingerprint,
                  "manifest split: "+split)
        state=SearchState(job["task"],job["method"],job["block"])
        validation_replayed=0
        test_replayed=0
        generated=0
        for node,event in zip(result["nodes"],result["events"]):
            if node["source"]=="live_llm":
                selection=state.choose(generated)
                expected_parent=selection["parent"]["id"] if selection["parent"] else None
                expected_reference=selection["reference"]["id"] if selection["reference"] else None
                check(selection["target"]==node["allocated_tag"],f"node {node['id']} selected tag")
                check(selection["action"]==node["action"],f"node {node['id']} selected action")
                check(expected_parent==node["parent_id"],f"node {node['id']} selected parent")
                check(expected_reference==node["reference_id"],f"node {node['id']} selected reference")
                generated+=1
            replay=evaluate(node["code"],job["task"])
            validation_replayed+=1
            expected=node["evaluation"]
            check(replay["valid"]==expected["valid"],f"node {node['id']} validation validity")
            if expected["valid"]:
                for key in ("loss","per_instance_loss","behavior","trajectory_behavior","solutions","features_called","local_checks"):
                    check(replay[key]==expected[key],f"node {node['id']} validation {key}")
            state.observe(node)
            check(state.events[-1]==event,f"node {node['id']} event replay")
        check(len(result["nodes"])==len(result["events"]),"nodes and events aligned")
        seed_nodes=[n for n in state.nodes if n["source"]=="handwritten_seed"]
        seed_quality=min(n["evaluation"]["loss"] for n in seed_nodes)
        archive=report_archive(state.nodes,job["task"],quality_reference=seed_quality)
        check(result["archive_ids"]==[n["id"] for n in archive],"common validation archive")
        by_id={str(n["id"]):n for n in state.nodes}
        for node_id,expected in result["test"].items():
            replay=evaluate(by_id[node_id]["code"],job["task"],split="test",with_probes=False)
            test_replayed+=1
            check(replay["valid"]==expected["valid"],f"node {node_id} test validity")
            if expected["valid"]:
                for key in ("loss","per_instance_loss","behavior","solutions"):
                    check(replay[key]==expected[key],f"node {node_id} test {key}")
        summary=result["summary"]
        check(summary["generated"]==generated,"generated count")
        check(generated<=job["steps"],"proposal slot ceiling")
        if job["regime"]=="slots8": check(generated==8,"fixed slot completion")
        check(summary["best_validation_loss"]==state.best,"best validation loss")
        best=state._simple_parent()
        best_test=result["test"][str(best["id"])]
        check(summary["validation_selected_test_loss"]==(best_test["loss"] if best_test["valid"] else 1.0),
              "validation-selected test readout")
        threshold=min(result["test"][str(n["id"])]["loss"] for n in seed_nodes)+QUALITY_TOLERANCE[job["task"]]
        eligible=[n for n in archive if result["test"][str(n["id"])]["valid"] and result["test"][str(n["id"])]["loss"]<=threshold]
        check(summary["common_gate_test_behavior_modes"]==_behavior_mode_count(eligible,result["test"]),
              "test mode count")
        tokens=0
        output_cap_violations=[]
        for usage in result["usage"]:
            stage=usage["stage"]
            suffix="plan" if stage=="planner" else "code"
            artifact=directory/f"{usage['iteration']:03d}_{suffix}.json"
            check(artifact.exists(),f"usage artifact {artifact.name}")
            if artifact.exists():
                raw=json.loads(artifact.read_text(encoding="utf-8"))
                check(all(usage.get(k)==v for k,v in raw["usage"].items()),f"usage agreement {artifact.name}")
                cap=1800 if stage=="planner" else 2000
                reserve=_token_reservation(raw["prompt"],cap)
                if job["token_budget"] is not None:
                    check(tokens+reserve<=job["token_budget"],f"admission violated {artifact.name}")
                if usage["output_tokens"]>cap: output_cap_violations.append(artifact.name)
            tokens+=usage["input_tokens"]+usage["output_tokens"]
        check(summary["tokens_used"]==tokens,"token total")
        check(summary["model_calls"]==len(result["usage"]),"known model call count")
        if job["token_budget"] is not None and summary["token_budget_valid"]:
            check(tokens<=job["token_budget"],"token ceiling")
            check(not result["usage_missing"] and not result["reservation_violations"],"strict cost eligibility")
        check(not (directory/"pending_call.json").exists(),"no pending call in completed run")
        rows.append({"job_id":job["job_id"],"ok":not errors,"errors":errors,
            "validation_programs_replayed":validation_replayed,"test_programs_replayed":test_replayed,
            "tokens_checked":tokens,"usage_complete":summary["usage_complete"],
            "request_errors":len(result["llm_errors"]),"output_cap_violations":output_cap_violations,
            "result_sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
        print(json.dumps({"verified":len(rows),"job_id":job["job_id"],"ok":not errors}),flush=True)
    report={"created_utc":datetime.now(timezone.utc).isoformat(),
        "scope":"no model calls; deterministic evaluator/controller/usage replay",
        "source_commit":manifest["source_commit"],"source_fingerprint":source_fingerprint(),
        "planned_runs":len(jobs),"checked_runs":len(rows),"missing_results":missing,"limit":limit,
        "all_checked_pass":all(row["ok"] for row in rows),
        "validation_programs_replayed":sum(row["validation_programs_replayed"] for row in rows),
        "test_programs_replayed":sum(row["test_programs_replayed"] for row in rows),
        "elapsed_seconds":time.perf_counter()-start,"runs":rows}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output")
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    root=Path(args.root)
    report=verify(root,Path(args.output) if args.output else root/"analysis"/"verification.json",args.limit)
    if not report["all_checked_pass"]: raise SystemExit(1)


if __name__=="__main__":
    main()
