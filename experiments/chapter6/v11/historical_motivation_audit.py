"""Reproduce the historical 49 local gains and TSP probe counterexample.

Reads the immutable prior delivery ZIP. No API calls and no new search.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import zipfile

from chapter6_demo.benchmarks import evaluate, behavior_distance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",default="experiments/chapter6/v11/historical_motivation.json")
    args=parser.parse_args()
    archive=Path("experiments/chapter6/archives/chapter6_validation_delivery_20260923.zip")
    counts=Counter()
    examples=[]
    runs=Counter()
    redundancy=Counter()
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            if not name.endswith("/result.json") or not name.startswith("chapter6_validation/runs/"): continue
            result=json.loads(source.read(name))
            config=result["config"]
            summary=result["summary"]
            if config["method"]!="relational" or not summary.get("cost_valid"): continue
            if not (("/confirm_v1/" in name and config["model"]=="qwen3.7-plus")
                    or "/confirm_minimax_recovery/" in name): continue
            nodes={node["id"]:node for node in result["nodes"]}
            events={event["node_id"]:event for event in result["events"]}
            best=None
            previous=[]
            cell=config["model"]+"/"+config["task"]
            runs[cell]+=1
            for node in result["nodes"]:
                evaluation=node["evaluation"]
                event=events[node["id"]]
                parent=nodes.get(node.get("parent_id"))
                if evaluation["valid"] and parent and parent["evaluation"]["valid"] and best is not None:
                    delta=parent["evaluation"]["loss"]-evaluation["loss"]
                    eligible=evaluation["loss"]<=best+config["quality_tolerance"]
                    if delta>1e-9 and eligible and not event["useful_gain"] and event["terminal_collision"]:
                        identical=[old for old in previous if old["evaluation"]["behavior"]==evaluation["behavior"]
                                   and old["evaluation"]["per_instance_loss"]==evaluation["per_instance_loss"]]
                        dominant=[old for old in previous if all(a<=b+1e-12 for a,b in
                                  zip(old["evaluation"]["per_instance_loss"],evaluation["per_instance_loss"]))]
                        redundancy["same_probe_and_validation_loss_vector"]+=bool(identical)
                        redundancy["weakly_dominated_by_one_existing_validation_rule"]+=bool(dominant)
                        redundancy[config["task"]+"_identical"]+=bool(identical)
                        counts[cell]+=1
                        examples.append({"archive_path":name,"node_id":node["id"],
                            "parent_id":node["parent_id"],"parent_loss":parent["evaluation"]["loss"],
                            "candidate_loss":evaluation["loss"],"global_best_before":best,"parent_gain":delta,
                            "terminal_collision":event["terminal_collision"],"useful_gain":event["useful_gain"],
                            "identical_prior_node_ids":[old["id"] for old in identical],
                            "weakly_dominating_prior_node_ids":[old["id"] for old in dominant]})
                if evaluation["valid"]:
                    best=evaluation["loss"] if best is None else min(best,evaluation["loss"])
                    previous.append(node)
    counterexample=json.loads(Path("experiments/chapter6/validation/artifacts/behavior_counterexample.json").read_text(encoding="utf-8"))
    os.environ.pop("CHAPTER6_BENCHMARK_PROFILE",None)
    os.environ.pop("CHAPTER6_DATA_BLOCK",None)
    before=evaluate(counterexample["before"]["code"],"tsp")
    after=evaluate(counterexample["after"]["code"],"tsp")
    pair={"before_code":counterexample["before"]["code"],"after_code":counterexample["after"]["code"],
          "before_validation_loss":before["loss"],"after_validation_loss":after["loss"],
          "fixed_probe_distance":behavior_distance(before["behavior"],after["behavior"]),
          "is_historical_instance_profile":True}
    output={"scope":"historical motivation replay only; no new search and no performance counterfactual",
        "archive":str(archive),"archive_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
        "selection":"relational; saved cost_valid flag; Qwen confirm_v1 plus complete MiniMax recovery batch",
        "old_quality_epsilon":1e-9,"total_selected_runs":sum(runs.values()),
        "selected_runs_by_cell":dict(runs),"uncredited_parent_improvements":sum(counts.values()),
        "counts_by_cell":dict(counts),"examples":examples,"counterexample_replay":pair}
    output["validation_redundancy_audit"]=dict(redundancy)
    output["interpretation"]="Parent improvement is not automatically an archive or utility improvement. Identical observed behavior and loss vectors can rediscover known rules; finite validation dominance does not establish distributional dominance or absence of future branch potential."
    path=Path(args.output)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"historical_gains_reproduced":sum(counts.values()),"counts":dict(counts),
                      "validation_redundancy_audit":dict(redundancy),
                      "tsp_counterexample":pair},ensure_ascii=False))


if __name__=="__main__":
    main()
