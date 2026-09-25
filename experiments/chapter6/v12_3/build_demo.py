"""Build a standalone, offline replay from archived facts and synthetic controls."""
from __future__ import annotations

import argparse
import copy
import difflib
import json
from pathlib import Path
import statistics

from chapter6_demo.v12_controller import V12SearchState
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_1.test_mechanism import finish, two_branches
from chapter6_demo.v12_2.archives import R2, archived_runs
from chapter6_demo.v12_2.common import file_sha, read_json, save_json
from chapter6_demo.v12_2.runner import branch_exposure, plain, restore

HERE = Path(__file__).resolve().parent


def node_view(node):
    ev = node["evaluation"]
    return {"id": node["id"], "name": node["name"], "code": node["code"],
            "parent_id": node.get("parent_id"), "tag": node.get("allocated_tag"),
            "reported_tags": node.get("reported_tags", node.get("tags", [])),
            "valid": ev["valid"], "validation_gap": ev["loss"] if ev["valid"] else None,
            "source": node["source"], "intent": node.get("intent"),
            "failure": node.get("proposal_failure")}


def nodes_view(nodes):
    indexed = {n["id"]: n for n in nodes}
    values = []
    for node in nodes:
        value = node_view(node)
        parent = indexed.get(node.get("parent_id"))
        value["parent_diff"] = "".join(difflib.unified_diff(
            parent["code"].splitlines(keepends=True), node["code"].splitlines(keepends=True),
            fromfile=f"parent_p{parent['id']}", tofile=f"candidate_p{node['id']}")) if parent else None
        values.append(value)
    return values


def short_decision(selection):
    return {"target": selection["target"], "action": selection["action"],
            "parent_id": selection["parent"]["id"] if selection["parent"] else None,
            "reference_id": selection["reference"]["id"] if selection["reference"] else None,
            "development": selection["branch_development_scheduled"]}


def frame(state, step, *, selection=None, event=None, before=None, depth=None):
    return plain({"step": step, "node_ids": [n["id"] for n in state.nodes],
                  "A": [n["id"] for n in state.A], "W": [n["id"] for n in state.W],
                  "B_before": before or [], "B_after": state.branch_pool,
                  "memory_count": len(state.M), "best_validation_gap": state.best,
                  "decision": short_decision(selection) if selection else None,
                  "event": event, "corrected_success_depth": depth})


def historical():
    manifest = read_json(R2 / "manifest.json")
    runs = []
    for job, result, sha in archived_runs():
        state = V12SearchState("tsp", job["method"], job["block"])
        frames, depths, step = [], {}, 0
        eligible_default = False
        for node, event in zip(result["nodes"], result["events"], strict=True):
            if node["source"] != "live_llm":
                state.observe(copy.deepcopy(node))
                continue
            if not frames:
                frames.append(frame(state, -1))
            before = copy.deepcopy(state.branch_pool)
            selection = state.choose(step)
            chosen = short_decision(selection)
            assert (chosen["parent_id"], chosen["target"], chosen["action"], chosen["reference_id"]) == (
                node["parent_id"], node["allocated_tag"], node["action"], node["reference_id"])
            assert selection["audit"] == node["allocation"]
            if event.get("branch_admitted"):
                depths[node["id"]] = depths.get(node["parent_id"], 0) + 1
            if selection["branch_development_scheduled"] and node["parent_id"] in depths:
                eligible_default = True
            state.observe(copy.deepcopy(node))
            assert state.events[-1] == event
            frames.append(frame(state, step, selection=selection, event=event,
                                before=before, depth=depths.get(node["id"])))
            step += 1
        runs.append({"id": job["job_id"], "model": job["model"], "controller": job["method"],
                     "block": job["block"], "kind": "historical-r2", "source_sha256": sha,
                     "source_commit": manifest["source_commit"], "frames": frames,
                     "nodes": nodes_view(result["nodes"]),
                     "test_gap": result["summary"]["validation_selected_test_loss"],
                     "tokens": result["summary"]["tokens_used"], "status": "archived_real_search",
                     "eligible_default": eligible_default})
    default = next((i for i, run in enumerate(runs) if run["eligible_default"]), 0)
    cells = [{"controller": method, "runs": len(group),
              "test_gap": statistics.mean(r["test_gap"] for r in group)}
             for method in ("niche", "niche_fixed_dev", "relational_branch")
             if (group := [r for r in runs if r["controller"] == method])]
    return {"runs": runs, "default_index": default, "cells": cells,
            "selection_rule": "First run in original manifest order with an admitted branch later selected for continuation; all 18 runs remain selectable.",
            "claim": "Historical v1.2 r2 replay; ordinary scheduling and W were confounded. Whole-batch averages are not estimates for the current controller."}


def synthetic():
    cases = []
    for bad_tag in ("local_distance", "return_aware", "hybrid"):
        policies = []
        for method in ("niche_fixed_dev", "relational_branch"):
            state = two_branches(method, bad_tag=bad_tag)
            before = copy.deepcopy(state.branch_pool)
            choice = state.choose(1)
            exposure = branch_exposure(state, choice)
            finish(state, choice, 6, valid=False)
            policies.append({"controller": method, "selected_parent": choice["parent"]["id"],
                             "exposure": plain(exposure), "scores": choice["audit"]["branch_scores"],
                             "statistics": choice["audit"]["family_statistics"],
                             "ordinary_decision": choice["audit"]["ordinary_decision"],
                             "pool_before": before, "pool_after_invalid_attempt": plain(state.branch_pool),
                             "observed_invalid_event": plain(state.events[-1])})
        assert policies[0]["ordinary_decision"] == policies[1]["ordinary_decision"]
        cases.append({"bad_tag": bad_tag, "policies": policies,
                      "claim": "Synthetic controlled history; generated by the actual V121 controller. The only followup is a prescribed invalid outcome; no future quality gain is claimed."})
    return {"cases": cases, "model_calls": 0}


def current(study, analysis):
    study = Path(study)
    manifest = read_json(study / "manifest.json")
    summary = read_json(Path(analysis) / "summary.json")
    runs = []
    for job in manifest["jobs"]:
        directory = study / "runs" / job["job_id"]
        cp_path = directory / "checkpoint.json"
        status = read_json(directory / "status.json") if (directory / "status.json").exists() else {"status": "not_started"}
        if not cp_path.exists():
            runs.append({"id": job["job_id"], "model": job["model"], "controller": job["controller"],
                         "block": job["data_block"], "kind": "v123", "frames": [], "nodes": [],
                         "status": status["status"], "test_gap": None, "tokens": None})
            continue
        cp = read_json(cp_path)
        restore(cp)
        from chapter6_demo.v12_1_controller import V121SearchState
        state = V121SearchState("tsp", job["controller"], job["search_seed"])
        for node in cp["seeds"]:
            state.observe(copy.deepcopy(node))
        frames = [frame(state, -1)]
        for step, record in enumerate(cp["records"]):
            selected = state.choose(step)
            state.observe(copy.deepcopy(record["node"]))
            f = frame(state, step, selection=selected, event=record["event"],
                      before=record["pool_before"], depth=record["event"]["branch_success_depth"])
            f["exposure"] = record["exposure"]
            f["tokens_after"] = record["tokens_after"]
            frames.append(f)
        test_path = study / "tests" / (job["job_id"] + ".json")
        test = read_json(test_path) if test_path.exists() else None
        search = read_json(directory / "search_result.json") if (directory / "search_result.json").exists() else None
        runs.append({"id": job["job_id"], "model": job["model"], "controller": job["controller"],
                     "block": job["data_block"], "kind": "v123", "status": status["status"],
                     "source_commit": manifest["source_commit"], "source_sha256": file_sha(cp_path),
                     "frames": frames, "nodes": nodes_view(cp["seeds"] + [r["node"] for r in cp["records"]]),
                     "test_gap": test["primary_test_gap"] if test else None,
                     "tokens": search["usage"]["total_tokens"] if search else None})
    default = next((i for i, run in enumerate(runs) if any(
        f["event"] and f["event"]["branch_parent_development"] for f in run["frames"])), 0)
    return {"runs": runs, "default_index": default, "summary": summary,
            "selection_rule": "First frozen-manifest run with an admitted branch actually developed; otherwise first planned run.",
            "claim": "v1.2.3 MiniMax M3, 8 paired blocks × 2 policies, 8 proposals each; exploratory, equal proposal budget. Test runs only after all searches finish."}


def build(output, study=None, analysis=None):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a new output directory; archived demos are immutable.")
    data = {"historical": historical(), "synthetic": synthetic(),
            "current": current(study, analysis) if study else None,
            "network_requests": 0, "demo_kind": "offline_record_replay_and_synthetic_control"}
    output.mkdir(parents=True)
    save_json(output / "demo_data.json", data, immutable=True)
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = (HERE / "demo_template.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(template.replace("__DEMO_DATA__", payload), encoding="utf-8", newline="\n")
    evidence = {"historical_runs": len(data["historical"]["runs"]),
                "historical_default": data["historical"]["runs"][data["historical"]["default_index"]]["id"],
                "historical_default_selection_rule": data["historical"]["selection_rule"],
                "current_runs": len(data["current"]["runs"]) if data["current"] else 0,
                "synthetic_cases": len(data["synthetic"]["cases"]),
                "synthetic_selected_parents": [[p["selected_parent"] for p in c["policies"]] for c in data["synthetic"]["cases"]],
                "new_model_calls": 0, "program_evaluations": 0,
                "files": {name: file_sha(output / name) for name in ("index.html", "demo_data.json")}}
    save_json(output / "provenance.json", evidence, immutable=True)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--study", type=Path)
    parser.add_argument("--analysis", type=Path)
    args = parser.parse_args()
    if bool(args.study) != bool(args.analysis):
        parser.error("--study and --analysis are a pair")
    with offline_only():
        result = build(args.output, args.study, args.analysis)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
