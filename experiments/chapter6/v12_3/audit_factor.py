"""Matched-history policy/prompt audit, with no counterfactual offspring claim."""
import argparse
import copy
import json
from pathlib import Path

from chapter6_demo.discovery import planner_prompt
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_1_controller import V121SearchState
from chapter6_demo.v12_2.common import read_json, save_json
from chapter6_demo.v12_2.runner import plain
from chapter6_demo.v12_3.analyze import write_csv


def audit(study, output):
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Do not overwrite an earlier audit.")
    manifest = read_json(study / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        cp = read_json(study / "runs" / job["job_id"] / "checkpoint.json")
        state = V121SearchState("tsp", job["controller"], job["search_seed"])
        for node in cp["seeds"]:
            state.observe(copy.deepcopy(node))
        for step, record in enumerate(cp["records"]):
            choices = []
            for method in ("niche_fixed_dev", "relational_branch"):
                twin = copy.deepcopy(state)
                twin.method = method
                choices.append(plain(twin.choose(step)))
            actual = plain(state.choose(step))
            assert actual == record["decision"]
            left, right = choices
            rows.append({"job_id": job["job_id"], "step": step,
                         "available_B": len(left["audit"]["available_branch_ids"]),
                         "development": actual["branch_development_scheduled"],
                         "ordinary_decision_equal": left["audit"]["ordinary_decision"] == right["audit"]["ordinary_decision"],
                         "full_decision_equal": left == right,
                         "planner_prompt_equal": planner_prompt("tsp", left, step) == planner_prompt("tsp", right, step)})
            state.observe(copy.deepcopy(record["node"]))
    report = {"scope": "Post-hoc policy substitution on every recorded history; no new offspring, model calls, or counterfactual quality estimate.",
              "manifest_sha256": manifest["manifest_sha256"], "decisions": len(rows),
              "ordinary_decision_equal": sum(r["ordinary_decision_equal"] for r in rows),
              "full_decision_equal": sum(r["full_decision_equal"] for r in rows),
              "planner_prompt_equal": sum(r["planner_prompt_equal"] for r in rows),
              "branch_development_slots": sum(r["development"] for r in rows),
              "development_slots_with_multiple_B": sum(r["development"] and r["available_B"] >= 2 for r in rows),
              "new_model_calls": 0, "new_program_evaluations": 0}
    output.mkdir(parents=True)
    save_json(output / "summary.json", report, immutable=True)
    write_csv(output / "matched_history.csv", rows)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with offline_only():
        result = audit(args.study, args.output)
    print(json.dumps(result))
