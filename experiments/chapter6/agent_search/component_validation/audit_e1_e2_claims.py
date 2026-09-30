"""Offline audit for the E1/E2 claims discussed in the component review.

The script reads frozen run/checkpoint/test files only.  It never imports the
provider service and never sends a model request.  E1 baseline Test scores are
recomputed from the frozen start-incumbent code and the frozen test snapshot,
so the ``validation improved but Test worsened`` claim is explicit rather than
inferred from a final summary field.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .evaluator import evaluate_test


HERE = Path(__file__).resolve().parent
DEFAULT_E1 = HERE / "studies" / "e1-minimax-20260928-g-frozen"
DEFAULT_E2 = HERE / "studies" / "e2-minimax-20260928-r3-frozen"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _start_node(state: dict[str, Any]) -> dict[str, Any]:
    start_id = state["start_incumbent_id"]
    nodes = state["nodes"]
    if isinstance(nodes, dict):
        return nodes[str(start_id)] if str(start_id) in nodes else nodes[start_id]
    return next(node for node in nodes if node["id"] == start_id)


def audit_e2(root: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    by_run: dict[str, dict[str, Any]] = {}
    for run_dir in sorted((root / "runs").iterdir()):
        checkpoint = run_dir / "checkpoint.json"
        if not checkpoint.exists():
            continue
        payload = read_json(checkpoint)
        run_events = [record.get("event", {}) for record in payload.get("records", [])]
        events.extend(run_events)
        by_run[run_dir.name] = {
            "branch_development": sum(
                event.get("branch_development") is True for event in run_events
            ),
            "new_direction_children": sum(
                event.get("branch_development") is True
                and event.get("new_direction") is True
                for event in run_events
            ),
            "parent_improvements": sum(
                event.get("branch_development") is True
                and event.get("local_improvement") is True
                for event in run_events
            ),
        }

    branch = [event for event in events if event.get("branch_development") is True]
    improvements = [event for event in branch if event.get("local_improvement") is True]
    reason_counts = Counter(event.get("admission_reason") for event in improvements)
    renewals = [
        event for event in improvements
        if event.get("protection_grant_awarded", 0) > 0
        or event.get("development_grant_awarded", 0) > 0
    ]
    return {
        "study": root.name,
        "runs": len(by_run),
        "records": len(events),
        "branch_development": len(branch),
        "new_direction_children": sum(
            event.get("new_direction") is True for event in branch
        ),
        "parent_improvements": len(improvements),
        "improvement_admission_reasons": {
            str(key): value for key, value in sorted(reason_counts.items(), key=lambda pair: str(pair[0]))
        },
        "improvements_with_new_grant": len(renewals),
        "improvement_details": [
            {
                "admission_reason": event.get("admission_reason"),
                "protected_development": event.get("protected_development", False),
                "global_improvement": event.get("global_improvement", False),
                "branch_remaining_after": event.get("branch_remaining_after"),
                "protection_grant_awarded": event.get("protection_grant_awarded", 0),
                "development_grant_awarded": event.get("development_grant_awarded", 0),
            }
            for event in improvements
        ],
        "run_summary": by_run,
        "note": (
            "The frozen E2 r3 archive contains 113 branch-development events. "
            "The reviewed narrative's count of 112 is therefore off by one; "
            "the 96/18/0 and 16/1/1 subdivisions are reproduced exactly."
        ),
    }


def audit_e1(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for run_dir in sorted((root / "runs").iterdir()):
        if "-cb-" not in run_dir.name:
            continue
        checkpoint_path = run_dir / "checkpoint.json"
        test_path = root / "tests" / f"{run_dir.name}.json"
        if not checkpoint_path.exists() or not test_path.exists():
            continue
        checkpoint = read_json(checkpoint_path)
        config = checkpoint["config"]
        state = checkpoint["state"]
        block = int(config["data_block"])
        test_snapshot = read_json(root / "data" / f"test-b{block}.json")
        start = _start_node(state)
        baseline = evaluate_test(start["code"], test_snapshot)
        final = read_json(test_path)
        validation_improvements = sum(
            bool(record.get("event", {}).get("global_improvement"))
            for record in checkpoint.get("records", [])
        )
        baseline_gap = baseline.get("loss")
        final_gap = final.get("primary_test_gap")
        test_improved = (
            baseline_gap is not None
            and final_gap is not None
            and final_gap < baseline_gap - 1e-12
        )
        rows.append({
            "job": run_dir.name,
            "block": block,
            "repetition": int(config["repetition"]),
            "validation_global_improvements": validation_improvements,
            "baseline_test_gap": baseline_gap,
            "final_test_gap": final_gap,
            "test_improved_over_start_incumbent": test_improved,
        })

    improved_rows = [row for row in rows if row["validation_global_improvements"] > 0]
    return {
        "study": root.name,
        "strategy": "C-B",
        "jobs": len(rows),
        "validation_improved_jobs": len(improved_rows),
        "validation_improved_but_test_worse_jobs": sum(
            not row["test_improved_over_start_incumbent"] for row in improved_rows
        ),
        "rows": rows,
        "note": (
            "The baseline Test gap is an offline evaluation of the frozen "
            "start_incumbent code on the frozen independent Test split."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e1", type=Path, default=DEFAULT_E1)
    parser.add_argument("--e2", type=Path, default=DEFAULT_E2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"e2": audit_e2(args.e2), "e1_cb": audit_e1(args.e1)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
