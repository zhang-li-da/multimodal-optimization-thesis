"""Offline, post-hoc audit of published r2 ZIPs; never generates candidates."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import socket
from unittest.mock import patch
from zipfile import ZipFile

from chapter6_demo.benchmarks import V12_TSP_PROFILE, instances, split_fingerprint
from chapter6_demo.discovery import planner_prompt
from chapter6_demo.providers import ModelClient
from chapter6_demo.v12_controller import V12SearchState, v12_source_fingerprint
from chapter6_demo.v12_1_controller import V121SearchState

REPO = Path(__file__).resolve().parents[3]
PACKAGE = REPO / "experiments/chapter6/v12/results/screening-20260924-r2"
OUTPUT = Path(__file__).resolve().parent / "results/mechanism-only-20260924"


@contextmanager
def offline_only():
    def forbidden(*args, **kwargs):
        raise RuntimeError("This audit is offline: model/network calls are forbidden.")
    with patch.object(ModelClient, "from_opencode", forbidden), \
         patch.object(ModelClient, "complete", forbidden), \
         patch.object(socket, "create_connection", forbidden), \
         patch.object(socket.socket, "connect", forbidden):
        yield


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def archived_runs(package=PACKAGE):
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for job in manifest["jobs"]:
        archive = "alibaba" if job["provider"].startswith("alibaba") else "minimax"
        with ZipFile(package / f"{archive}-raw-runs.zip") as stream:
            name = f"{job['job_id']}/result.json"
            data = stream.read(name)
        yield job, json.loads(data), hashlib.sha256(data).hexdigest()


def chosen(selection):
    return (selection["target"], selection["action"],
            selection["parent"]["id"] if selection["parent"] else None,
            selection["reference"]["id"] if selection["reference"] else None)


def audit(package=PACKAGE, output=OUTPUT):
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert v12_source_fingerprint() == manifest["source_fingerprint_sha256"]
    rows, decisions, counterfactual = [], [], []
    for job, result, digest in archived_runs(package):
        state = V12SearchState("tsp", job["method"], job["block"])
        alternatives = [V121SearchState("tsp", method, job["block"])
                        for method in ("niche_fixed_dev", "relational_branch")]
        depths = {}
        successful_max = attempted_max = 0
        row = {"job_id": job["job_id"], "model": job["model"], "controller": job["method"],
               "block": job["block"], "result_sha256": digest,
               "test_gap": result["summary"]["validation_selected_test_loss"],
               "admissions": 0, "followups": 0, "valid_children": 0, "parent_improvements": 0,
               "global_improvements": 0, "multi_branch_slots": 0, "restarts": 0,
               "restarts_to_untried_tags": 0, "w_only_references": 0}
        step = 0
        for node, event in zip(result["nodes"], result["events"], strict=True):
            live = node["source"] == "live_llm"
            if live:
                available = [b["node_id"] for b in state.branch_pool if b["remaining"] > 0]
                A, W = {n["id"] for n in state.A}, {n["id"] for n in state.W}
                selection = state.choose(step)
                assert chosen(selection) == (node["allocated_tag"], node["action"],
                                             node["parent_id"], node["reference_id"])
                assert selection["audit"] == node["allocation"]
                records = [e for e in state.M if e.get("allocated_tag") == node["allocated_tag"]
                           or e.get("allocated_tag") is None and node["allocated_tag"] in e["tags"]]
                restart = node["action"] == "restart"
                ref_w = node["reference_id"] in W - A
                develop = selection["branch_development_scheduled"]
                row["restarts"] += restart
                row["restarts_to_untried_tags"] += restart and not records
                row["w_only_references"] += ref_w
                row["multi_branch_slots"] += develop and len(available) >= 2
                row["admissions"] += bool(event.get("branch_admitted"))
                row["followups"] += develop
                row["valid_children"] += develop and event["valid"]
                row["parent_improvements"] += develop and event["parent_improved"]
                row["global_improvements"] += develop and event["improved"]
                attempt = depths.get(node["parent_id"], 0) + 1 if develop else None
                if attempt is not None:
                    attempted_max = max(attempted_max, attempt)
                if event.get("branch_admitted"):
                    depths[node["id"]] = depths.get(node["parent_id"], 0) + 1
                    successful_max = max(successful_max, depths[node["id"]])
                decisions.append({"job_id": job["job_id"], "step": step,
                    "available_branch_ids": available, "selected": chosen(selection),
                    "branch_development": develop, "w_only_reference": ref_w,
                    "restart_to_untried_tag": restart and not records,
                    "attempt_depth": attempt, "success_depth": depths.get(node["id"])})

                # Advance each policy copy with its own selected allocation.
                # The recorded candidate evaluation is deliberately reused as
                # a same-history probe; no counterfactual model response is
                # generated. This makes branch budgets and future availability
                # evolve under the queried policy instead of silently leaving
                # every branch uncharged.
                selected = []
                policy_copies = []
                for alternative in alternatives:
                    policy_copy = copy.deepcopy(alternative)
                    choice = policy_copy.choose(step)
                    selected.append(choice)
                    policy_copies.append(policy_copy)
                left, right = selected
                ordinary_equal = left["audit"]["ordinary_decision"] == right["audit"]["ordinary_decision"]
                nondev = not left["branch_development_scheduled"]
                full_prompt_equal = planner_prompt("tsp", left, step) == planner_prompt("tsp", right, step)
                assert ordinary_equal
                assert not nondev or full_prompt_equal
                counterfactual.append({"job_id": job["job_id"], "step": step,
                    "ordinary_equal": ordinary_equal, "nondevelopment": nondev,
                    "full_prompt_equal": full_prompt_equal,
                    "available_count": len(left["audit"]["available_branch_ids"]),
                    "fixed_parent": chosen(left)[2], "relation_parent": chosen(right)[2],
                    "branch_choice_changed": chosen(left)[2] != chosen(right)[2]})
                for policy_copy, choice in zip(policy_copies, selected):
                    observed = copy.deepcopy(node)
                    observed.update({
                        "allocated_tag": choice["target"],
                        "action": choice["action"],
                        "parent_id": choice["parent"]["id"] if choice["parent"] else None,
                        "reference_id": choice["reference"]["id"] if choice["reference"] else None,
                        "allocation": choice["audit"],
                    })
                    policy_copy.observe(observed)
                alternatives = policy_copies
                step += 1
            state.observe(node)
            if not live:
                for alternative in alternatives:
                    alternative.observe(copy.deepcopy(node))
            assert state.events[-1] == event
        assert state.summary() == {key: result["summary"][key] for key in state.summary()}
        row.update(max_attempt_depth=attempted_max, max_success_depth=successful_max)
        rows.append(row)

    cells = []
    groups = defaultdict(list)
    for row in rows:
        groups[row["model"], row["controller"]].append(row)
    sum_fields = ("admissions", "followups", "valid_children", "parent_improvements", "global_improvements",
                  "multi_branch_slots", "restarts", "restarts_to_untried_tags", "w_only_references")
    for (model, controller), records in sorted(groups.items()):
        cell = {"model": model, "controller": controller, "runs": len(records),
                "runs_without_admission": sum(r["admissions"] == 0 for r in records),
                "mean_test_gap": sum(r["test_gap"] for r in records) / len(records)}
        cell.update({key: sum(r[key] for r in records) for key in sum_fields})
        cell.update(max_attempt_depth=max(r["max_attempt_depth"] for r in records),
                    max_success_depth=max(r["max_success_depth"] for r in records))
        # A proposed exposure gate, not an outcome-selected criterion and not
        # a retrospective replacement for the original pooled r2 gate.
        cell["proposed_exposure_gate_diagnostic_only"] = (controller != "niche" and
            cell["followups"] >= 4 and cell["valid_children"] >= 1 and cell["multi_branch_slots"] >= 2)
        cells.append(cell)
    report = {"analysis_type": "post-hoc; no new model calls; no counterfactual quality estimate",
              "archived_source_commit": manifest["source_commit"], "source_fingerprint": v12_source_fingerprint(),
              "archived_runs_replayed": len(rows), "archived_decisions_replayed": len(decisions),
              "new_model_calls": 0, "cells": cells, "runs": rows,
              "matched_history_probe": {"queries": len(counterfactual),
                  "ordinary_equal": sum(c["ordinary_equal"] for c in counterfactual),
                  "nondevelopment_queries": sum(c["nondevelopment"] for c in counterfactual),
                  "nondevelopment_prompts_equal": sum(c["nondevelopment"] and c["full_prompt_equal"] for c in counterfactual),
                  "branch_choice_differences": sum(c["branch_choice_changed"] for c in counterfactual)}}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "r2_audit.json", report)
    write_json(output / "r2_decisions.json", decisions)
    write_json(output / "matched_history_decisions.json", counterfactual)
    write_csv(output / "r2_cells.csv", cells)
    write_csv(output / "r2_runs.csv", rows)
    return report


def snapshot_instances(package=PACKAGE, output=OUTPUT):
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    snapshot = {"provenance": "Post-hoc reconstruction matching every original r2 split fingerprint; not saved during original search.",
                "profile": V12_TSP_PROFILE, "blocks": {}}
    with patch.dict(os.environ, {"CHAPTER6_BENCHMARK_PROFILE": V12_TSP_PROFILE}):
        for block, expected in manifest["splits"].items():
            with patch.dict(os.environ, {"CHAPTER6_DATA_BLOCK": block}):
                snapshot["blocks"][block] = {}
                for split, record in expected.items():
                    assert split_fingerprint("tsp", split) == record["sha256"]
                    snapshot["blocks"][block][split] = {"sha256": record["sha256"],
                                                         "instances": instances("tsp", split)}
    write_json(output / "r2_instances.json", snapshot)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=PACKAGE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    with offline_only():
        result = audit(args.package, args.output)
        snapshot_instances(args.package, args.output)
    print(json.dumps({k: result[k] for k in ("archived_runs_replayed", "archived_decisions_replayed",
                                            "new_model_calls", "matched_history_probe")}))


if __name__ == "__main__":
    main()
