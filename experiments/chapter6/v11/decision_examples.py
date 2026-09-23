"""Export deterministic illustrative examples without selecting winners by test loss."""
import argparse
import json
from pathlib import Path

from chapter6_demo.discovery import planner_prompt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args=parser.parse_args()
    root=Path(args.root)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    chosen={}
    for job in sorted(manifest["jobs"],key=lambda j:j["job_id"]):
        path=root/"runs"/job["job_id"]/"result.json"
        if not path.exists():continue
        result=json.loads(path.read_text(encoding="utf-8"))
        by_id={node["id"]:node for node in result["nodes"]}
        event_by_id={event["node_id"]:event for event in result["events"]}
        for node in result["nodes"]:
            if node["source"]!="live_llm":continue
            event=event_by_id[node["id"]]
            examples=[]
            if event["improved"] and event["terminal_collision"]:examples.append("global_gain_with_collision")
            if event["competitive_local_development"] and not event["improved"] and event["terminal_collision"]:
                examples.append("parent_or_neighbor_gain_without_global_record")
                if event["quality_protection"] and event["useful_gain"]:
                    examples.append("protected_local_gain")
            if event["unproductive_collision"]:examples.append("unproductive_collision")
            if not event["valid"]:examples.append("invalid_program")
            for key in examples:
                key=job["task"]+"/"+key
                if key in chosen:continue
                parent=by_id.get(node["parent_id"])
                chosen[key]={"job_id":job["job_id"],"node_id":node["id"],
                    "selection_rule":"lexicographically first job, earliest qualifying node; no test-loss selection",
                    "event":event,"node":node,"parent":parent,
                    "test_result_if_archived":result["test"].get(str(node["id"]))}
    output=root/"analysis"
    (output/"decision_examples.json").write_text(json.dumps(chosen,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# Decision examples","",
        "Examples are selected by sorted job ID and earliest qualifying node. They illustrate mechanism events and are not selected by held-out performance.",
        "Full parent/candidate code, validation evidence and archived test evaluation (if available) are in `decision_examples.json`.",""]
    for name,example in sorted(chosen.items()):
        event=example["event"]
        lines.extend(["## "+name,"",f"Run `{example['job_id']}`, node {example['node_id']}.","",
          f"Global gain: {event['global_improvement_margin']}; parent gain: {event['parent_improvement_margin']}; neighbor gain: {event['neighborhood_improvement_margin']}.",
          f"Collision: {event['terminal_collision']}; local credit eligible: {event['local_credit_eligible']}; useful gain recorded: {event['useful_gain']}.","",
          "```python",example["node"]["code"],"```",""])
    (output/"DECISION_EXAMPLES.md").write_text("\n".join(lines).rstrip()+"\n",encoding="utf-8")
    print(json.dumps({"example_categories":len(chosen),"source":manifest["source_commit"]}))


if __name__=="__main__":
    main()
