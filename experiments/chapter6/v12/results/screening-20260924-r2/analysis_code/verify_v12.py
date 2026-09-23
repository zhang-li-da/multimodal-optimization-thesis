"""Deterministically replay v1.2 evaluations, decisions, and branch budgets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from chapter6_demo.benchmarks import (V12_TSP_PROFILE, evaluate, instances, split_fingerprint)
from chapter6_demo.v12_controller import V12SearchState, v12_source_fingerprint


def _same_evaluation(actual,expected):
    keys=("valid","loss","per_instance_loss","behavior","trajectory_behavior","solutions",
          "features_called","local_checks","failure_type","error")
    return all(actual.get(key)==expected.get(key) for key in keys if key in actual or key in expected)


def verify(root:Path,output:Path):
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    stable={k:v for k,v in manifest.items() if k not in ("created_utc","manifest_sha256")}
    manifest_hash=hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    errors=[]
    if manifest_hash!=manifest["manifest_sha256"]: errors.append("manifest hash mismatch")
    if v12_source_fingerprint()!=manifest["source_fingerprint_sha256"]: errors.append("frozen source fingerprint mismatch")
    rows=[]
    validation_count=test_count=0
    schema_sets=[]
    group_terms=("niche_fixed_dev","relational_branch")
    for job in manifest["jobs"]:
        directory=root/"runs"/job["job_id"]
        path=directory/"result.json"
        if not path.exists():
            errors.append(job["job_id"]+": missing result")
            continue
        result=json.loads(path.read_text(encoding="utf-8"))
        config=result["config"]
        job_errors=[]
        def check(condition,label):
            if not condition: job_errors.append(label)
        check(config.get("source_fingerprint")==manifest["source_fingerprint_sha256"],"source fingerprint")
        for key in ("provider","model","method","task","steps","token_budget"):
            check(config.get(key)==job.get(key),"config "+key)
        check(config.get("data_block")==job["block"],"block")
        os.environ["CHAPTER6_BENCHMARK_PROFILE"]=V12_TSP_PROFILE
        os.environ["CHAPTER6_DATA_BLOCK"]=str(job["block"])
        instances.cache_clear()
        for split in ("probe","validation","test"):
            actual=split_fingerprint("tsp",split)
            check(config.get("splits",{}).get(split)==actual,"split "+split)
            check(manifest["splits"][str(job["block"])][split]["sha256"]==actual,"manifest split "+split)
        state=V12SearchState("tsp",job["method"],job["block"])
        expected_nodes=result["nodes"]
        expected_events=result["events"]
        check(len(expected_nodes)==len(expected_events),"node/event alignment")
        generated=0
        for node,event in zip(expected_nodes,expected_events):
            if node["source"]=="live_llm":
                selection=state.choose(generated)
                selected_parent=selection["parent"]["id"] if selection["parent"] else None
                selected_reference=selection["reference"]["id"] if selection["reference"] else None
                check(selection["target"]==node["allocated_tag"],f"node {node['id']} target")
                check(selection["action"]==node["action"],f"node {node['id']} action")
                check(selected_parent==node["parent_id"],f"node {node['id']} parent")
                check(selected_reference==node["reference_id"],f"node {node['id']} reference")
                check(selection.get("audit",{})==node.get("allocation",{}),f"node {node['id']} branch allocation")
                generated+=1
            replay=evaluate(node["code"],"tsp")
            validation_count+=1
            check(_same_evaluation(replay,node["evaluation"]),f"node {node['id']} validation replay")
            state.observe(node)
            check(state.events[-1]==event,f"node {node['id']} event replay")
        check(generated==8,"eight proposal slots")
        by_id={str(node["id"]):node for node in expected_nodes}
        for node_id,expected in result["test"].items():
            replay=evaluate(by_id[node_id]["code"],"tsp",split="test",with_probes=False)
            test_count+=1
            check(_same_evaluation(replay,expected),f"node {node_id} test replay")
        summary=state.summary()
        for key,value in summary.items(): check(result["summary"].get(key)==value,"summary "+key)
        check(result.get("branch_pool")==state.branch_pool,"final branch pool")
        check(result["summary"].get("tokens_used")==sum(x["input_tokens"]+x["output_tokens"] for x in result["usage"]),"usage sum")
        check(result["summary"].get("model_calls")==len(result["usage"]),"model call count")
        check(not result["llm_errors"],"API errors present")
        check(result["summary"].get("usage_complete") is True,"incomplete usage")
        check(result["summary"].get("generated")==8,"summary proposal count")
        for step in range(8):
            plan_path=directory/f"{step:03d}_plan.json"
            code_path=directory/f"{step:03d}_code.json"
            check(plan_path.exists() and code_path.exists(),f"prompt artifacts {step}")
            if not plan_path.exists() or not code_path.exists(): continue
            plan=json.loads(plan_path.read_text(encoding="utf-8"))["prompt"]
            code=json.loads(code_path.read_text(encoding="utf-8"))["prompt"]
            try:
                parsed=json.loads(plan)
                schema_sets.append(tuple(sorted(parsed)))
                serialized=plan+code
                for term in group_terms: check(term not in serialized,f"prompt exposes arm {term}")
                check(set(parsed)==set(("task","task_contract","iteration","target_strategy_family","action",
                    "parent","complementary_reference","allocation_evidence","allowed_tags","common_seed_rules",
                    "recent_execution_feedback","quality_requirement","request")),f"planner schema {step}")
            except json.JSONDecodeError:
                check(False,f"invalid planner prompt {step}")
        result_sha=hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"job_id":job["job_id"],"ok":not job_errors,"errors":job_errors,
            "generated":generated,"model_calls":len(result["usage"]),
            "tokens_used":result["summary"]["tokens_used"],"result_sha256":result_sha,
            "local_developments":result["summary"]["branch_development_attempts"],
            "valid_branch_children":sum(e.get("branch_parent_development",False) and e.get("valid",False) for e in result["events"]),
            "parent_improving_branch_children":result["summary"]["branch_development_parent_improvements"]})
        if job_errors: errors.extend(job["job_id"]+": "+item for item in job_errors)
    verification={"study_id":manifest["study_id"],"source_commit":manifest["source_commit"],
        "source_fingerprint":v12_source_fingerprint(),"manifest_sha256":manifest["manifest_sha256"],
        "checked_runs":len(rows),"planned_runs":len(manifest["jobs"]),
        "all_checked_pass":len(rows)==len(manifest["jobs"]) and not errors,
        "validation_programs_replayed":validation_count,"test_programs_replayed":test_count,
        "planner_prompt_schema_count":len(set(schema_sets)),"errors":errors,"runs":rows}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding="utf-8")
    return verification


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output")
    args=parser.parse_args()
    root=Path(args.root)
    output=Path(args.output) if args.output else root/"analysis"/"verification.json"
    report=verify(root,output)
    print(json.dumps({k:report[k] for k in ("checked_runs","planned_runs","all_checked_pass",
        "validation_programs_replayed","test_programs_replayed","planner_prompt_schema_count")},ensure_ascii=False))
    if not report["all_checked_pass"]: raise SystemExit(1)


if __name__=="__main__": main()
