"""TSP deployment-cost replay for frozen selector choices and single rules.

Post-search descriptive timing on this machine; no API, no selector tuning.
Exact reference computation is precomputed outside deployment timing.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import time

from chapter6_demo.benchmarks import instances, optimum_tsp, tsp_execute
from chapter6_demo.programs import Program


def evaluate_choices(programs,choices,test):
    losses=[]
    feature_calls=0
    local_checks=0
    start=time.perf_counter()
    cpu=time.process_time()
    for choice,instance in zip(choices,test):
        outcome=tsp_execute(programs[choice],instance)
        losses.append(outcome["loss"])
        feature_calls+=outcome["features_called"]
        local_checks+=outcome["local_checks"]
    return {"loss":statistics.fmean(losses),"per_instance_loss":losses,
            "feature_calls":feature_calls,"local_checks":local_checks,
            "cpu_seconds":time.process_time()-cpu,"wall_seconds":time.perf_counter()-start}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args=parser.parse_args()
    root=Path(args.root)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    rows=[]
    for job in sorted(manifest["jobs"],key=lambda j:(j["block"],j["job_id"])):
        if job["task"]!="tsp":continue
        path=root/"runs"/job["job_id"]/"result.json"
        if not path.exists():continue
        result=json.loads(path.read_text(encoding="utf-8"))
        selector=result["selector"]
        if selector["status"]!="ok":continue
        os.environ["CHAPTER6_BENCHMARK_PROFILE"]="chapter6-v11-independent-v1"
        os.environ["CHAPTER6_DATA_BLOCK"]=str(job["block"])
        test=instances("tsp","test")
        for case in test: optimum_tsp(tuple(tuple(p) for p in case["points"]))
        nodes={n["id"]:n for n in result["nodes"]}
        programs={i:Program(nodes[i]["code"],"tsp") for i in selector["candidate_ids"]}
        single_choices=[selector["validation_selected_single_id"]]*len(test)
        choices=selector["test_selected_ids"]
        # Alternate order by paired block to avoid assigning all warm-cache
        # advantage to one comparator. Reference distances are already cached.
        if job["block"]%2:
            selected=evaluate_choices(programs,choices,test)
            single=evaluate_choices(programs,single_choices,test)
        else:
            single=evaluate_choices(programs,single_choices,test)
            selected=evaluate_choices(programs,choices,test)
        valid=abs(selected["loss"]-selector["learned_selector_test_loss"])<1e-12
        valid=valid and abs(single["loss"]-selector["validation_selected_single_test_loss"])<1e-12
        if not valid: raise ValueError("Frozen-choice replay disagrees for "+job["job_id"])
        overhead=selector["test_feature_and_selection_seconds"]
        rows.append({**job,"single_test_loss":single["loss"],"selector_test_loss":selected["loss"],
            "single_algorithm_seconds":single["wall_seconds"],
            "selected_algorithm_seconds":selected["wall_seconds"],
            "saved_selector_feature_and_predict_seconds":overhead,
            "selector_total_seconds":overhead+selected["wall_seconds"],
            "end_to_end_time_ratio":(overhead+selected["wall_seconds"])/single["wall_seconds"],
            "single_feature_calls":single["feature_calls"],"selected_feature_calls":selected["feature_calls"],
            "selected_local_checks":selected["local_checks"],"test_instances":len(test),
            "prediction_timing_mode":"saved 36-instance batch time averaged per instance",
            "exact_reference_generation_timed":False,"replay_matches":valid})
    output=root/"analysis"
    with (output/"deployment_cost.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else [])
        writer.writeheader();writer.writerows(rows)
    data={"scope":"post-search timing audit; one replay on this machine; batch predictor time from original run plus selected algorithm execution replay",
          "tsp_runs":len(rows),"all_choices_match":all(r["replay_matches"] for r in rows),
          "mean_end_to_end_time_ratio":statistics.fmean(r["end_to_end_time_ratio"] for r in rows) if rows else None,
          "mean_selector_ms_per_instance":1000*statistics.fmean(r["selector_total_seconds"]/r["test_instances"] for r in rows) if rows else None,
          "mean_single_ms_per_instance":1000*statistics.fmean(r["single_algorithm_seconds"]/r["test_instances"] for r in rows) if rows else None,
          "runs":rows}
    (output/"deployment_cost.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in data.items() if k!="runs"},ensure_ascii=False))


if __name__=="__main__":
    main()
