"""Portable r2 replay: preserve strict results and separately assess AST compatibility."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from chapter6_demo.v12_1 import audit_r2, verify_numeric
from .archives import R2, archived_runs
from .common import environment, file_sha, read_json, save_json, source_record
from .data import HISTORICAL
from .identity import identity, legacy_hashes


def compatibility_report(strict, package=R2):
    evaluated = {(row["job_id"], row["node_id"], row["split"]): row for row in strict["evaluations"]}
    rows = []
    for job, run, result_sha in archived_runs(package):
        by_id = {node["id"]: node for node in run["nodes"]}
        records = [(node["id"], "validation", node["evaluation"]) for node in run["nodes"]]
        records += [(int(node_id), "test", evaluation) for node_id, evaluation in run["test"].items()]
        for node_id, split, archived in records:
            original = evaluated[job["job_id"], node_id, split]
            code = by_id[node_id]["code"]
            hashes = legacy_hashes(code) if "program_hash" in archived else None
            hash_matches = hashes is None or hashes["cpython_313_show_empty_false_subset"] == archived["program_hash"]
            remaining_discrete = [key for key in original["exact_mismatches"] if key != "program_hash"]
            passed = (hash_matches and not remaining_discrete and
                      all(value["within_tolerance"] for value in original["numeric_differences"]))
            rows.append({"job_id": job["job_id"], "node_id": node_id, "split": split,
                         "raw_result_sha256": result_sha, "program_identity": identity(code),
                         "archived_program_hash": archived.get("program_hash"), "hashes": hashes,
                         "legacy_313_format_match": hash_matches,
                         "strict_equal": original["strict_equal"],
                         "other_discrete_mismatches": remaining_discrete,
                         "compatibility_passed": passed})
    return {"scope": "Named AST-format compatibility; does not rewrite the original strict verdict.",
            "environment": environment(), "new_model_calls": 0, "rows": rows,
            "program_evaluations": len(rows), "strict_equal": strict["strict_equal"],
            "original_strict_passed": strict["passed"],
            "hash_evaluations": sum(row["hashes"] is not None for row in rows),
            "legacy_hash_matches": sum(row["hashes"] is not None and row["legacy_313_format_match"] for row in rows),
            "runtime_hash_mismatches": sum(row["hashes"] is not None and row["hashes"]["runtime_default"] != row["archived_program_hash"] for row in rows),
            "numeric_differing_leaves": sum(len(row["numeric_differences"]) for row in strict["evaluations"]),
            "decision_errors": strict["decision_errors"],
            "compatibility_passed": len(rows) == 330 and not strict["decision_errors"] and all(row["compatibility_passed"] for row in rows)}


def replay(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a fresh output directory to preserve prior evidence.")
    output.mkdir(parents=True)
    with audit_r2.offline_only(), patch.object(audit_r2, "archived_runs", archived_runs), \
            patch.object(verify_numeric, "archived_runs", archived_runs):
        audit = audit_r2.audit(output=output / "audit")
        print(json.dumps({"replayed_runs": audit["archived_runs_replayed"],
                          "replayed_decisions": audit["archived_decisions_replayed"]}), flush=True)
        strict = verify_numeric.verify(R2, HISTORICAL, output / "numeric_strict.json")
        report = compatibility_report(strict)
    report.update(source=source_record(), instances_sha256=file_sha(HISTORICAL))
    save_json(output / "numeric_compatibility.json", report, immutable=True)
    return {key: value for key, value in report.items() if key not in ("rows", "source")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = replay(args.output)
    print(json.dumps(report, ensure_ascii=False))
    if not report["compatibility_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
