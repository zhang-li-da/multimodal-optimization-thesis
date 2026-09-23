"""Post-search sensitivity tables for token eligibility and execution failures.

The frozen primary analysis retains all finished runs, including truncated
infrastructure failures. This audit separately shows complete-cost paired
contrasts; it does not replace the primary analysis or repair missing outcomes.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

from chapter6_demo.v11.analyze_factorial import _bootstrap_interval


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args=parser.parse_args()
    root=Path(args.root)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    records=[]
    exclusions=[]
    for job in manifest["jobs"]:
        path=root/"runs"/job["job_id"]/"result.json"
        if not path.exists():
            exclusions.append({"job_id":job["job_id"],"reasons":["no result"]})
            continue
        result=json.loads(path.read_text(encoding="utf-8"))
        summary=result["summary"]
        reasons=[]
        if not summary["usage_complete"]: reasons.append("incomplete provider token usage")
        if result["reservation_violations"]: reasons.append("reservation violation")
        if job["token_budget"] and not summary["token_budget_valid"]: reasons.append("not a compliant hard-ceiling run")
        if job["regime"]=="slots8" and summary["generated"]!=8: reasons.append("incomplete proposal-slot run")
        if not summary["validation_selected_test_valid"]: reasons.append("validation-selected algorithm invalid on test")
        if reasons: exclusions.append({"job_id":job["job_id"],"reasons":reasons,
            "request_errors":result["llm_errors"],"known_tokens":summary["tokens_used"]})
        records.append({**job,"eligible":not reasons,"test_loss":summary["validation_selected_test_loss"],
            "modes":summary["common_gate_test_behavior_modes"],
            "selector_loss":result.get("selector",{}).get("learned_selector_test_loss"),
            "known_tokens":summary["tokens_used"]})
    cells=sorted({(row["model"],row["task"],row["regime"]) for row in records})
    contrasts=[]
    for model,task,regime in cells:
        cell=[r for r in records if (r["model"],r["task"],r["regime"])==(model,task,regime)]
        lookup={(r["method"],r["block"]):r for r in cell}
        for method in ("relational","relational_qp","relational_rr","relational_qp_rr"):
            for metric in ("test_loss","modes","selector_loss","known_tokens"):
                values=[]
                blocks=[]
                for block in range(5):
                    first,second=lookup.get((method,block)),lookup.get(("niche",block))
                    if (first and second and first["eligible"] and second["eligible"]
                            and first[metric] is not None and second[metric] is not None):
                        values.append(first[metric]-second[metric])
                        blocks.append(block)
                low,high=_bootstrap_interval(values)
                contrasts.append({"model":model,"task":task,"regime":regime,
                    "contrast":method+"_minus_niche","metric":metric,"eligible_paired_blocks":len(values),
                    "block_ids":json.dumps(blocks),"mean_difference":statistics.fmean(values) if values else None,
                    "bootstrap_95_low":low,"bootstrap_95_high":high})
    output=root/"analysis"
    output.mkdir(exist_ok=True)
    with (output/"sensitivity_complete_cost_pairs.csv").open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(contrasts[0]) if contrasts else [])
        writer.writeheader()
        writer.writerows(contrasts)
    report={"scope":"post-search sensitivity; complete-case estimates can be selected by infrastructure availability; do not replace primary analysis",
        "planned_runs":len(manifest["jobs"]),"results_present":len(records),
        "eligible_runs":sum(r["eligible"] for r in records),"ineligible_or_missing":exclusions,
        "complete_cost_pair_contrasts":contrasts}
    (output/"protocol_sensitivity.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"results":len(records),"eligible":report["eligible_runs"],"ineligible_or_missing":len(exclusions)}))


if __name__=="__main__":
    main()
