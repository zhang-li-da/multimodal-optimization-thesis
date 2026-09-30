"""Create a reproducible descriptive report for the frozen short-horizon study."""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json, utcnow

from .evaluator import evaluate_test


def _mean(values):
    return sum(values) / len(values) if values else None


def _sd(values):
    if len(values) < 2:
        return None
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _median(values):
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _round(value):
    return round(value, 10) if isinstance(value, (int, float)) else value


def _summary(values):
    return {"n": len(values), "mean": _round(_mean(values)),
            "median": _round(_median(values)), "sd": _round(_sd(values)),
            "min": _round(min(values)) if values else None,
            "max": _round(max(values)) if values else None}


def _job_identity(job):
    checkpoint = job["checkpoint_id"]
    block = int(job["data_block"])
    phase = checkpoint.rsplit("-", 1)[1]
    return block, phase, job["strategy"], int(job["repetition"])


def analyze(study: Path, output: Path) -> dict:
    study = Path(study).resolve()
    output = Path(output).resolve()
    manifest = read_json(study / "manifest.json")
    data = {(item["block"], item["role"]): item for item in manifest["data"]}
    checkpoints = {item["checkpoint_id"]: item for item in manifest["checkpoints"]}
    rows = []
    statuses = defaultdict(int)
    for job in manifest["jobs"]:
        run = study / "runs" / job["job_id"]
        status = read_json(run / "status.json")
        statuses[status["status"]] += 1
        checkpoint = read_json(study / checkpoints[job["checkpoint_id"]]["path"])
        baseline_node = next((node for node in checkpoint["nodes"]
                              if node["id"] == checkpoint["incumbent"]["id"]), None)
        test_path = study / "tests" / f"{job['job_id']}.json"
        summary = status.get("summary") or {}
        row = {
            "job_id": job["job_id"], "block": int(job["data_block"]),
            "phase": checkpoint["config"]["phase"], "strategy": job["strategy"],
            "repetition": int(job["repetition"]), "status": status["status"],
            "completed_proposals": summary.get("completed_proposals", 0),
            "known_tokens": summary.get("known_tokens"),
            "request_count": summary.get("request_count"),
            "global_improvements": summary.get("global_improvements"),
            "parent_improvements": summary.get("parent_improvements"),
            "project_progress": summary.get("project_progress"),
            "exploration_followups": summary.get("exploration_followups"),
            "test_file": test_path.relative_to(study).as_posix() if test_path.exists() else None,
            "test_gap": None, "baseline_test_gap": None, "test_improvement": None,
            "validation_loss": None,
        }
        if test_path.exists() and status["status"] == "continuation_complete":
            test = read_json(test_path)
            row["test_gap"] = test.get("primary_test_gap")
            row["validation_loss"] = read_json(run / "selection_frozen.json").get("validation_loss")
            test_snapshot = read_json(study / data[(job["data_block"], "test")]["path"])
            if baseline_node and isinstance(baseline_node.get("code"), str):
                baseline = evaluate_test(baseline_node["code"], test_snapshot)
                row["baseline_test_gap"] = baseline["loss"] if baseline.get("valid") else 1.0
                if row["test_gap"] is not None and row["baseline_test_gap"] is not None:
                    row["test_improvement"] = row["baseline_test_gap"] - row["test_gap"]
        rows.append(row)

    complete = [row for row in rows if row["status"] == "continuation_complete"]
    observed = [row for row in complete if row["test_gap"] is not None]
    by_strategy = {}
    for strategy in ("C-I", "C-B", "C-E"):
        selected = [row for row in observed if row["strategy"] == strategy]
        by_strategy[strategy] = {
            "jobs": len([row for row in rows if row["strategy"] == strategy]),
            "complete_jobs": len([row for row in complete if row["strategy"] == strategy]),
            "test_jobs": len(selected),
            "test_gap": _summary([row["test_gap"] for row in selected]),
            "improvement_over_checkpoint": _summary([row["test_improvement"] for row in selected]),
            "known_tokens": _summary([row["known_tokens"] for row in selected if isinstance(row["known_tokens"], (int, float))]),
            "completed_proposals": _summary([row["completed_proposals"] for row in selected]),
            "global_improvements": _summary([row["global_improvements"] for row in selected if isinstance(row["global_improvements"], (int, float))]),
            "parent_improvements": _summary([row["parent_improvements"] for row in selected if isinstance(row["parent_improvements"], (int, float))]),
            "project_progress": _summary([row["project_progress"] for row in selected if isinstance(row["project_progress"], (int, float))]),
            "exploration_followups": _summary([row["exploration_followups"] for row in selected if isinstance(row["exploration_followups"], (int, float))]),
        }

    paired = {}
    for strategy in ("C-B", "C-E"):
        differences = []
        for row in observed:
            if row["strategy"] != strategy:
                continue
            key = (row["block"], row["phase"], row["repetition"])
            incumbent = next((candidate for candidate in observed
                              if (candidate["block"], candidate["phase"], candidate["repetition"]) == key
                              and candidate["strategy"] == "C-I"), None)
            if incumbent:
                differences.append({"block": row["block"], "phase": row["phase"],
                                    "repetition": row["repetition"],
                                    "strategy_minus_CI_test_gap": _round(row["test_gap"] - incumbent["test_gap"]),
                                    "strategy_minus_CI_improvement": _round(row["test_improvement"] - incumbent["test_improvement"])})
        paired[strategy] = {
            "task_pairs": len(differences),
            "test_gap_difference": _summary([item["strategy_minus_CI_test_gap"] for item in differences]),
            "improvement_difference": _summary([item["strategy_minus_CI_improvement"] for item in differences]),
            "pairs": differences,
        }

    block_phase = defaultdict(lambda: defaultdict(list))
    for row in observed:
        block_phase[(row["block"], row["phase"])][row["strategy"]].append(row["test_improvement"])
    block_rows = []
    for (block, phase), values in sorted(block_phase.items()):
        item = {"block": block, "phase": phase}
        for strategy in ("C-I", "C-B", "C-E"):
            item[strategy] = _round(_mean(values.get(strategy, [])))
        if item["C-I"] is not None:
            item["C-B_minus_C-I"] = _round(item["C-B"] - item["C-I"]) if item["C-B"] is not None else None
            item["C-E_minus_C-I"] = _round(item["C-E"] - item["C-I"]) if item["C-E"] is not None else None
        block_rows.append(item)

    report = {
        "schema": "chapter6-short-horizon-analysis-v1",
        "study_id": manifest["study_id"],
        "study_manifest_sha256": manifest["manifest_sha256"],
        "generated_utc": utcnow(),
        "status_counts": dict(sorted(statuses.items())),
        "planned_jobs": len(manifest["jobs"]), "completed_jobs": len(complete),
        "test_jobs": len(observed), "test_jobs_missing": len(manifest["jobs"]) - len(observed),
        "unit_of_inference": "data block; early/late checkpoints are paired within block; repetitions are within-checkpoint repeats",
        "selection": "best validation node after four proposals; test was read only after all search jobs were terminal",
        "strategies": by_strategy, "paired_against_CI": paired,
        "block_phase_improvements": block_rows,
        "rows": rows,
        "limitations": [
            "This is a diagnostic opportunity-cost study, not a confirmation of universal superiority.",
            "One C-E job is infrastructure_incomplete after a sent_unknown request and has no test endpoint.",
            "The 95 test jobs are not 95 independent experiments; block-level pairing is the primary interpretation.",
            "No post-hoc parameter or checkpoint changes were made after continuation results.",
        ],
    }
    report["report_sha256"] = digest({key: value for key, value in report.items() if key != "report_sha256"})
    output.parent.mkdir(parents=True, exist_ok=True)
    save_json(output, report, immutable=False)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.study, args.output)
    print(json.dumps({"status": "complete", "test_jobs": report["test_jobs"],
                      "report_sha256": report["report_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
