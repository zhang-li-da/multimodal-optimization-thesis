"""Offline r2 evaluation replay using saved coordinates and explicit tolerances."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
from unittest.mock import patch

from chapter6_demo import benchmarks
from chapter6_demo.v12_controller import V12SearchState, v12_source_fingerprint
from .audit_r2 import OUTPUT, PACKAGE, archived_runs, chosen, offline_only, write_json

ATOL = 1e-12
RTOL = 1e-10
NUMERIC = {"loss", "per_instance_loss", "per_instance_value", "family_loss", "trajectory_values"}
IGNORED = {"wall_seconds", "cpu_seconds"}


def numeric_differences(actual, expected, path=""):
    """Record every differing scalar; only finite numeric leaves get tolerance."""
    if isinstance(actual, dict) and isinstance(expected, dict) and actual.keys() == expected.keys():
        return [d for key in actual for d in numeric_differences(actual[key], expected[key], path + "/" + str(key))]
    if isinstance(actual, (tuple, list)) and isinstance(expected, (tuple, list)) and len(actual) == len(expected):
        return [d for i, (a, e) in enumerate(zip(actual, expected)) for d in numeric_differences(a, e, path + "/" + str(i))]
    numbers = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (actual, expected))
    if numbers:
        finite = math.isfinite(actual) and math.isfinite(expected)
        if finite and actual == expected:
            return []
        return [{"path": path, "actual": actual if math.isfinite(actual) else str(actual),
                 "expected": expected if math.isfinite(expected) else str(expected),
                 "absolute_error": abs(actual - expected) if finite else None,
                 "within_tolerance": finite and math.isclose(actual, expected, abs_tol=ATOL, rel_tol=RTOL)}]
    if actual == expected:
        return []
    return [{"path": path, "actual": actual, "expected": expected, "within_tolerance": False}]


def compare_evaluation(actual, expected):
    exact_mismatches, differences = [], []
    for key in sorted((actual.keys() | expected.keys()) - IGNORED):
        if key not in actual or key not in expected:
            exact_mismatches.append(key + ": missing field")
        elif key in NUMERIC:
            differences.extend(numeric_differences(actual[key], expected[key], "/" + key))
        elif actual[key] != expected[key]:
            exact_mismatches.append(key)
    return {"exact_mismatches": exact_mismatches, "numeric_differences": differences,
            "strict_equal": not exact_mismatches and not differences,
            "within_numeric_contract": not exact_mismatches and all(d["within_tolerance"] for d in differences)}


def verify(package, snapshot_path, output):
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert v12_source_fingerprint() == manifest["source_fingerprint_sha256"]
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for block, splits in snapshot["blocks"].items():
        for split, data in splits.items():
            digest = hashlib.sha256(json.dumps(data["instances"], sort_keys=True).encode()).hexdigest()
            assert digest == manifest["splits"][block][split]["sha256"] == data["sha256"]
    rows, decision_errors = [], []
    jobs = sorted(archived_runs(package), key=lambda item: (item[0]["block"], item[0]["job_id"]))
    for job, result, _ in jobs:
        saved_instances = snapshot["blocks"][str(job["block"]) ]
        def load_instances(task, split):
            if task != "tsp":
                raise ValueError("Only frozen TSP data are supported.")
            return saved_instances[split]["instances"]
        state = V12SearchState("tsp", job["method"], job["block"])
        step = 0
        with patch.object(benchmarks, "instances", load_instances):
            for node, event in zip(result["nodes"], result["events"], strict=True):
                if node["source"] == "live_llm":
                    selection = state.choose(step)
                    if chosen(selection) != (node["allocated_tag"], node["action"], node["parent_id"], node["reference_id"]):
                        decision_errors.append({"job_id": job["job_id"], "node": node["id"], "field": "decision"})
                    if selection["audit"] != node["allocation"]:
                        decision_errors.append({"job_id": job["job_id"], "node": node["id"], "field": "allocation"})
                    step += 1
                evaluation = benchmarks.evaluate(node["code"], "tsp")
                rows.append({"job_id": job["job_id"], "node_id": node["id"], "split": "validation",
                             **compare_evaluation(evaluation, node["evaluation"])})
                replayed = copy.deepcopy(node)
                replayed["evaluation"] = evaluation
                state.observe(replayed)
                for field in ("valid", "parent_improved", "improved", "terminal_collision", "useful_gain",
                              "nearest_node", "local_credit_eligible", "branch_admitted", "branch_classification",
                              "branch_parent_id", "branch_depth"):
                    if state.events[-1].get(field) != event.get(field):
                        decision_errors.append({"job_id": job["job_id"], "node": node["id"], "field": field})
            by_id = {str(n["id"]): n for n in result["nodes"]}
            for node_id, expected in result["test"].items():
                actual = benchmarks.evaluate(by_id[node_id]["code"], "tsp", split="test", with_probes=False)
                rows.append({"job_id": job["job_id"], "node_id": int(node_id), "split": "test",
                             **compare_evaluation(actual, expected)})
    report = {"scope": "Local evaluation replay, not evidence of successful verification on an untested OS.",
              "platform": platform.platform(), "python": platform.python_version(), "model_calls": 0,
              "source_fingerprint": v12_source_fingerprint(),
              "instance_snapshot_sha256": hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
              "tolerances": {"absolute": ATOL, "relative": RTOL, "numeric_fields": sorted(NUMERIC)},
              "ignored_fields": sorted(IGNORED), "program_evaluations": len(rows),
              "validation_programs": sum(r["split"] == "validation" for r in rows),
              "test_programs": sum(r["split"] == "test" for r in rows),
              "strict_equal": sum(r["strict_equal"] for r in rows),
              "within_numeric_contract": sum(r["within_numeric_contract"] for r in rows),
              "decision_errors": decision_errors, "evaluations": rows,
              "passed": len(rows) == 330 and not decision_errors and all(r["within_numeric_contract"] for r in rows)}
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=PACKAGE)
    parser.add_argument("--instances", type=Path, default=OUTPUT / "r2_instances.json")
    parser.add_argument("--output", type=Path, required=True,
                        help="Use a new file for each platform; do not overwrite the published record.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; choose a new verification file.")
    with offline_only():
        result = verify(args.package, args.instances, args.output)
    print(json.dumps({key: result[key] for key in ("program_evaluations", "strict_equal",
                                                 "within_numeric_contract", "passed", "model_calls")}))
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
