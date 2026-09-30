"""Explain archived AST hash differences without relaxing the frozen verifier."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from verify_review import portable_archived_runs


def dump_without_empty_optional_fields(node):
    """3.13-style default dump for the repository's validated AST subset.

    This is a diagnostic serializer, not a general AST canonicalization API.
    Python 3.13 defaults show_empty=False; see official ast.dump documentation
    and https://github.com/python/cpython/blob/v3.13.5/Lib/ast.py.
    No source code, program evaluation or archived hash is overwritten.
    """
    if isinstance(node, ast.AST):
        fields = []
        for name, value in ast.iter_fields(node):
            if value is None or value == []:
                continue
            fields.append(f"{name}={dump_without_empty_optional_fields(value)}")
        return type(node).__name__ + "(" + ", ".join(fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(dump_without_empty_optional_fields(x) for x in node) + "]"
    return repr(node)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--numeric-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output file.")
    rows = []
    for job, result, _ in portable_archived_runs():
        by_id = {n["id"]: n for n in result["nodes"]}
        evaluations = [(n["id"], "validation", n["evaluation"]) for n in result["nodes"]]
        evaluations += [(int(node_id), "test", evaluation) for node_id, evaluation in result["test"].items()]
        for node_id, split, evaluation in evaluations:
            if "program_hash" not in evaluation:
                continue
            tree = ast.parse(by_id[node_id]["code"])
            actual = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()[:16]
            compatible = hashlib.sha256(dump_without_empty_optional_fields(tree).encode()).hexdigest()[:16]
            rows.append({"job_id": job["job_id"], "node_id": node_id, "split": split,
                         "archived_hash": evaluation["program_hash"], "runtime_hash": actual,
                         "diagnostic_313_style_hash": compatible,
                         "diagnostic_matches_archive": compatible == evaluation["program_hash"]})
    numeric = json.loads(args.numeric_report.read_text())
    report = {"purpose": "Explanation only; does not change original failed strict-verifier result",
              "python_ast_reference": "https://docs.python.org/3.13/library/ast.html#ast.dump",
              "ast_source_reference": "https://github.com/python/cpython/blob/v3.13.5/Lib/ast.py",
              "hash_evaluations": len(rows),
              "runtime_hash_mismatches": sum(r["runtime_hash"] != r["archived_hash"] for r in rows),
              "diagnostic_hash_matches": sum(r["diagnostic_matches_archive"] for r in rows),
              "non_hash_discrete_mismatches": sum(len(set(r["exact_mismatches"]) - {"program_hash"}) for r in numeric["evaluations"]),
              "numeric_differing_leaves": sum(len(r["numeric_differences"]) for r in numeric["evaluations"]),
              "decision_errors": numeric["decision_errors"], "rows": rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
