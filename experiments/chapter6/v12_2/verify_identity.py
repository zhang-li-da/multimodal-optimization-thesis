"""Standard-library-only cross-Python identity verification of 327 archived evaluations."""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from .archives import archived_runs
from .common import read_json, save_json
from .identity import FORMAT, identity, legacy_hashes


def verify(output, reference=None):
    if Path(output).exists():
        raise ValueError("Use a new identity output file.")
    rows = []
    for job, result, _ in archived_runs():
        by_id = {node["id"]: node for node in result["nodes"]}
        evaluations = [(node["id"], "validation", node["evaluation"]) for node in result["nodes"]]
        evaluations += [(int(node_id), "test", evaluation) for node_id, evaluation in result["test"].items()]
        for node_id, split, evaluation in evaluations:
            if "program_hash" not in evaluation:
                continue
            code = by_id[node_id]["code"]
            hashes = legacy_hashes(code)
            rows.append({"job_id": job["job_id"], "node_id": node_id, "split": split,
                         **identity(code), "runtime_legacy_hash": hashes["runtime_default"],
                         "legacy_313_format_match": hashes["cpython_313_show_empty_false_subset"] == evaluation["program_hash"],
                         "runtime_legacy_match": hashes["runtime_default"] == evaluation["program_hash"]})
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "format": FORMAT, "new_model_calls": 0, "new_program_evaluations": 0,
              "valid_archived_evaluations": len(rows),
              "legacy_313_format_matches": sum(row["legacy_313_format_match"] for row in rows),
              "runtime_legacy_matches": sum(row["runtime_legacy_match"] for row in rows),
              "all_structural_hashes_present": all(row["structural_sha256"] for row in rows), "rows": rows}
    if reference:
        old = read_json(reference)
        keys = ("job_id", "node_id", "split", "raw_code_sha256", "structural_format", "structural_sha256")
        matched = len(rows) == len(old["rows"]) and all(
            all(left[key] == right[key] for key in keys) for left, right in zip(rows, old["rows"], strict=True))
        report.update(reference_python=old["python"], raw_and_structural_identity_equal=matched)
    report["passed"] = (len(rows) == 327 and report["legacy_313_format_matches"] == 327
                        and report["all_structural_hashes_present"]
                        and report.get("raw_and_structural_identity_equal", True))
    save_json(output, report, immutable=True)
    return {key: value for key, value in report.items() if key != "rows"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    report = verify(args.output, args.reference)
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
