"""Post-search descriptive mechanism exposure and fixed-history decision audit.

No counterfactual model calls or counterfactual performance are fabricated.
This file is separate from the frozen confirmatory/screening analysis.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import statistics

from chapter6_demo.discovery import CONTROLLER_FACTORS, SearchState


def decision(selection):
    return {"target":selection["target"],"action":selection["action"],
            "parent_id":selection["parent"]["id"] if selection["parent"] else None,
            "reference_id":selection["reference"]["id"] if selection["reference"] else None}


def audit_result(result):
    config=result["config"]
    states={method:SearchState(config["task"],method,config["seed"]) for method in CONTROLLER_FACTORS}
    actual=SearchState(config["task"],config["method"],config["seed"])
    counts=Counter()
    exposures=[]
    comparisons=[]
    step=0
    future_parents=Counter(n["parent_id"] for n in result["nodes"] if n["source"]=="live_llm")
    future_refs=Counter(n["reference_id"] for n in result["nodes"] if n["source"]=="live_llm")
    for node in result["nodes"]:
        if node["source"]!="live_llm":
            actual.observe(node)
            for state in states.values(): state.observe(node)
            continue
        actual_selection=actual.choose(step)
        alternative={method:decision(state.choose(step)) for method,state in states.items()}
        for method in ("relational_qp","relational_rr","relational_qp_rr"):
            baseline=alternative["relational"]
            changed=alternative[method]
            counts[method+"_fixed_history_decision_changes"]+=int(baseline!=changed)
            counts[method+"_fixed_history_action_changes"]+=int(baseline["action"]!=changed["action"])
            counts[method+"_fixed_history_parent_changes"]+=int(baseline["parent_id"]!=changed["parent_id"])
            if baseline!=changed:
                comparisons.append({"iteration":step,"contrast":method+"_vs_relational",
                    "baseline_decision":baseline,"modified_decision":changed,
                    "scope":"same saved history only; future model responses are unknown"})
        allocation=node.get("allocation",{})
        if node["action"]=="restart":
            counts["restarts"]+=1
            target=allocation.get("selected_tag")
            history=allocation.get("tag_statistics",{}).get(target,{})
            counts["restart_trigger_untried"]+=int(history.get("attempts")==0)
            counts["restart_trigger_saturation"]+=int(bool(allocation.get("saturated")))
            counts["restart_trigger_no_growth"]+=int(bool(allocation.get("stalled")))
        actual.observe(node)
        event=actual.events[-1]
        for state in states.values(): state.observe(node)
        counts["proposals"]+=1
        counts["valid_proposals"]+=int(event["valid"])
        counts["global_improvements"]+=int(event["improved"])
        counts["global_improvements_colliding"]+=int(event["improved"] and event["terminal_collision"])
        counts["local_development_events"]+=int(event["competitive_local_development"])
        local_only=event["competitive_local_development"] and not event["improved"] and event["terminal_collision"]
        counts["local_only_collisions"]+=int(local_only)
        counts["local_only_credit_eligible"]+=int(local_only and event["local_credit_eligible"])
        counts["local_only_credit_exhausted"]+=int(local_only and not event["local_credit_eligible"])
        counts["parent_improvements"]+=int(event["parent_improved"])
        counts["neighborhood_improvements"]+=int(event["neighborhood_improved"])
        if local_only:
            in_archive=any(n["id"]==node["id"] for n in actual.A)
            counts["local_only_retained_in_search_archive"]+=int(in_archive)
            counts["local_only_later_used_as_parent"]+=int(future_parents[node["id"]]>0)
            counts["local_only_later_used_as_reference"]+=int(future_refs[node["id"]]>0)
            exposures.append({"node_id":node["id"],"parent_id":node["parent_id"],
                "loss":event["loss"],"parent_gain":event["parent_improvement_margin"],
                "neighbor_gain":event["neighborhood_improvement_margin"],"credit_eligible":event["local_credit_eligible"],
                "retained_in_search_archive_after_observe":in_archive,
                "later_parent_uses":future_parents[node["id"]],"later_reference_uses":future_refs[node["id"]]})
        step+=1
    return {"counts":dict(counts),"local_development_examples":exposures,"fixed_history_decision_changes":comparisons}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output")
    args=parser.parse_args()
    root=Path(args.root)
    output=Path(args.output) if args.output else root/"analysis"
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    rows=[]
    details=[]
    for job in manifest["jobs"]:
        path=root/"runs"/job["job_id"]/"result.json"
        if not path.exists(): continue
        result=json.loads(path.read_text(encoding="utf-8"))
        audit=audit_result(result)
        summary=result["summary"]
        known_tokens=summary["tokens_used"]
        row={**job,**audit["counts"],"reported_tokens":known_tokens,
             "usage_complete":summary["usage_complete"],
             "unused_token_fraction":(1-known_tokens/job["token_budget"]) if job["token_budget"] else None,
             "mean_planner_input_tokens":statistics.fmean(u["input_tokens"] for u in result["usage"] if u["stage"]=="planner")
                 if any(u["stage"]=="planner" for u in result["usage"]) else None,
             "mean_coder_input_tokens":statistics.fmean(u["input_tokens"] for u in result["usage"] if u["stage"]=="coder")
                 if any(u["stage"]=="coder" for u in result["usage"]) else None}
        rows.append(row)
        details.append({"job_id":job["job_id"],**audit})
    output.mkdir(parents=True,exist_ok=True)
    fields=list(dict.fromkeys(key for row in rows for key in row))
    with (output/"mechanism_runs.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    grouped=[]
    for cell in sorted({(r["model"],r["task"],r["regime"],r["method"]) for r in rows}):
        matches=[r for r in rows if (r["model"],r["task"],r["regime"],r["method"])==cell]
        stats={key:sum(r.get(key,0) for r in matches) for key in (
            "proposals","valid_proposals","global_improvements","global_improvements_colliding",
            "local_only_collisions","local_only_credit_eligible","local_only_credit_exhausted",
            "local_only_retained_in_search_archive","local_only_later_used_as_parent",
            "local_only_later_used_as_reference","restarts","restart_trigger_untried",
            "restart_trigger_saturation","restart_trigger_no_growth",
            "relational_qp_fixed_history_decision_changes","relational_rr_fixed_history_decision_changes",
            "relational_qp_rr_fixed_history_decision_changes")}
        stats.update(model=cell[0],task=cell[1],regime=cell[2],method=cell[3],runs=len(matches))
        stats["mean_generated"]=statistics.fmean(r["proposals"] for r in matches)
        unused=[r["unused_token_fraction"] for r in matches if r["unused_token_fraction"] is not None]
        stats["mean_unused_token_fraction"]=statistics.fmean(unused) if unused else None
        grouped.append(stats)
    report={"scope":"post-search descriptive audit; same-history interventions do not predict alternative LLM outputs",
        "source_commit":manifest["source_commit"],"results_present":len(rows),"groups":grouped,"runs":details}
    (output/"mechanism_audit.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    text=["# Post-search mechanism audit","",
          "This audit is descriptive and was added after the live run started. It cannot establish a causal performance improvement.",
          "Fixed-history comparisons replay the same saved programs under four controllers; the model responses under changed prompts remain unknown.",
          "Restart triggers may overlap. A local-only collision improves a parent or same-behavior neighbor inside the quality envelope, without refreshing the global best.","",
          "| Model | Task | Budget | Method | Runs | Local-only collisions | Credited | Retained in A | Later parent | Restarts | Untried trigger | Stalled trigger | Saturation trigger |",
          "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for group in grouped:
        keys=("model","task","regime","method","runs","local_only_collisions","local_only_credit_eligible",
              "local_only_retained_in_search_archive","local_only_later_used_as_parent","restarts",
              "restart_trigger_untried","restart_trigger_no_growth","restart_trigger_saturation")
        text.append("| "+" | ".join(str(group[k]) for k in keys)+" |")
    text.extend(["","Full program examples, fixed-history decision differences and token under-utilization are in `mechanism_audit.json` and `mechanism_runs.csv`."])
    (output/"MECHANISM_AUDIT.md").write_text("\n".join(text)+"\n",encoding="utf-8")
    print(json.dumps({"runs_audited":len(rows),"output":str(output)},ensure_ascii=False))


if __name__=="__main__":
    main()
