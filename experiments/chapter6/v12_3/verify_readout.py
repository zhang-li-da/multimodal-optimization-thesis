"""Re-execute each frozen primary program on archived search/test coordinates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_1.verify_numeric import ATOL, RTOL, compare_evaluation
from chapter6_demo.v12_2.common import environment, file_sha, read_json, save_json
from chapter6_demo.v12_2.data import evaluate_search, evaluate_test, split_path
from chapter6_demo.v12_2.runner import restore
from chapter6_demo.v12_3.study import verify


def verify_selected(study, output):
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Keep prior verification evidence; choose a new output.")
    manifest = verify(study)
    if manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("Verification requires a frozen study.")
    rows, missing = [], []
    for job in manifest["jobs"]:
        directory = study / "runs" / job["job_id"]
        test_path = study / "tests" / (job["job_id"] + ".json")
        if not test_path.exists():
            missing.append(job["job_id"])
            continue
        cp, test = read_json(directory / "checkpoint.json"), read_json(test_path)
        state = restore(cp)
        best = min((n for n in state.nodes if n["evaluation"]["valid"]),
                   key=lambda n: (n["evaluation"]["loss"], n["id"]))
        assert best["id"] == test["best_id"]
        for split, evaluator in (("search", evaluate_search), ("test", evaluate_test)):
            snapshot_path = split_path(study, manifest, job["data_block"], split)
            actual = evaluator(best["code"], read_json(snapshot_path))
            expected = best["evaluation"] if split == "search" else test["evaluations"][str(best["id"]) ]
            comparison = compare_evaluation(actual, expected)
            rows.append({"job_id": job["job_id"], "node_id": best["id"], "split": split,
                         "coordinates_sha256": file_sha(snapshot_path),
                         "program_identity": actual["program_identity"], **comparison})
    passed = all(r["within_numeric_contract"] for r in rows)
    report = {"passed": passed, "environment": environment(), "manifest_sha256": manifest["manifest_sha256"],
              "matches_frozen_execution_environment": environment() == manifest["environment"],
              "new_model_calls": 0, "scope": "Selected programs on the current Windows/Python environment; not a claim of newly executed cross-platform verification.",
              "absolute_tolerance": ATOL, "relative_tolerance": RTOL,
              "discrete_outputs_and_program_identity": "exact equality required",
              "ignored_timing_fields": ["wall_seconds", "cpu_seconds"],
              "program_evaluations": len(rows), "strict_matches": sum(r["strict_equal"] for r in rows),
              "within_contract": sum(r["within_numeric_contract"] for r in rows),
              "jobs_without_test_readout": missing, "rows": rows}
    save_json(output, report, immutable=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with offline_only():
        report = verify_selected(args.study, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
