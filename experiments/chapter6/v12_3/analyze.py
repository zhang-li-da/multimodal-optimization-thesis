"""Read completed records only: paired quality, measured cost, and mechanism exposure."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
import csv
import itertools
import json
from pathlib import Path
import statistics

import numpy as np

from chapter6_demo.discovery import behavior_distance
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import DurableCalls
from chapter6_demo.v12_1_controller import V121SearchState
from chapter6_demo.v12_2.common import digest, environment, file_sha, read_json, save_json, utcnow
from chapter6_demo.v12_2.data import split_path
from chapter6_demo.v12_2.runner import branch_exposure, plain, restore
from chapter6_demo.v12_3.study import TERMINAL, verify


def mean(values):
    values = [v for v in values if v is not None]
    return statistics.mean(values) if values else None


def write_csv(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def percentile_interval(values, spec):
    if len(values) < 2:
        return None
    x = np.asarray(values)
    rng = np.random.default_rng(spec["bootstrap_seed"])
    sampled = x[rng.integers(0, len(x), (spec["bootstrap_replicates"], len(x)))].mean(axis=1)
    return [float(v) for v in np.quantile(sampled, [.025, .975])]


def verify_exposure(checkpoint):
    """Pool and ranking diagnostics must match executed decisions, not just logs."""
    config = checkpoint["config"]
    state = V121SearchState("tsp", config["controller"], config["search_seed"])
    for node in checkpoint["seeds"]:
        state.observe(copy.deepcopy(node))
    for step, record in enumerate(checkpoint["records"]):
        assert plain(state.branch_pool) == record["pool_before"]
        decision = state.choose(step)
        assert plain(branch_exposure(state, decision)) == record["exposure"]
        state.observe(copy.deepcopy(record["node"]))
        assert plain(state.branch_pool) == record["pool_after"]


def paired_rows(rows, blocks):
    """Missing jobs remain visible; gaps are fractions, differences percentage points."""
    pairs = []
    for block in blocks:
        fixed = next(r for r in rows if r["block"] == block and r["controller"] == "niche_fixed_dev")
        relation = next(r for r in rows if r["block"] == block and r["controller"] == "relational_branch")
        complete = fixed["test_gap"] is not None and relation["test_gap"] is not None
        pairs.append({"block": block, "fixed_status": fixed["status"], "relation_status": relation["status"],
            "complete_pair": complete, "fixed_gap": fixed["test_gap"], "relation_gap": relation["test_gap"],
            "delta_pp": 100*(relation["test_gap"]-fixed["test_gap"]) if complete else None,
            "fixed_tokens": fixed["tokens"], "relation_tokens": relation["tokens"]})
    return pairs


def raw_calls(directory, job_id):
    rows = []
    for folder in sorted((directory / "calls").glob("*")):
        request = read_json(folder / "request.json")
        state = read_json(folder / "state.json") if (folder / "state.json").exists() else {}
        response = read_json(folder / "response.json") if (folder / "response.json").exists() else {}
        raw = read_json(folder / "raw_response.json") if (folder / "raw_response.json").exists() else {}
        if raw:
            assert raw["request_sha256"] == digest(request)
            assert raw["envelope_sha256"] == digest(raw["envelope"])
        if response:
            assert response["request_sha256"] == digest(request)
        body = json.loads(base64.b64decode(raw["envelope"]["body_base64"])) if raw else {}
        choice = (body.get("choices") or [{}])[0]
        text = response.get("text", "")
        rows.append({"job_id": job_id, "step": request["step"], "stage": request["stage"],
            "request_state": state.get("status"), "requested_model": request["model"],
            "returned_model": response.get("returned_model"), "max_tokens": request["max_tokens"],
            "input_tokens": response.get("input_tokens"), "output_tokens": response.get("output_tokens"),
            "usage_complete": response.get("usage_complete"), "seconds": response.get("seconds"),
            "finish_reason": choice.get("finish_reason"), "output_at_limit": response.get("output_tokens") == request["max_tokens"],
            "unclosed_thinking_block": "<think>" in text and "</think>" not in text,
            "response_received_utc": raw.get("received_utc"), "request_id": response.get("request_id"),
            "raw_response_sha256": file_sha(folder / "raw_response.json") if raw else None})
    return rows


def selection_metrics(record, job):
    exposure, decision, event = record["exposure"], record["decision"], record["event"]
    entries = exposure["entries"]
    fifo = entries[0]["node_id"] if entries else None
    q = [e["family_priority"] for e in entries]
    gain = [.25 * e["gain_term"] for e in entries]
    q_span = max(q) - min(q) if q else 0.0
    gain_span = max(gain) - min(gain) if gain else 0.0
    branch = exposure["branch_slot"]
    full_fifo = bool(branch and exposure["full_rank_parent_id"] != fifo)
    full_gain = bool(exposure["full_vs_gain_choice_differs"])
    actual = decision["parent"]["id"] if decision["parent"] else None
    return {"job_id": job["job_id"], "controller": job["controller"], "block": job["data_block"],
        "step": decision["audit"]["decision_step"], "node_id": record["node"]["id"], "actual_parent_id": actual,
        "branch_slot": branch, "available_count": len(entries), "distinct_tags": exposure["distinct_available_tags"],
        "multi_branch_slot": exposure["multi_branch_slot"], "family_priority_varies": branch and exposure["family_priority_varies"],
        "fifo_parent_id": fifo, "full_parent_id": exposure["full_rank_parent_id"],
        "gain_only_parent_id": exposure["gain_only_parent_id"], "full_vs_fifo_differs": full_fifo,
        "full_vs_gain_differs": full_gain, "actual_differs_fifo": branch and actual != fifo,
        "actual_differs_gain": branch and actual != exposure["gain_only_parent_id"],
        "family_fallback_count": sum(e["family_fallback"] for e in entries),
        "family_observations_min": min((e["family_observations"] for e in entries), default=None),
        "q_min": min(q, default=None), "q_max": max(q, default=None), "q_span": q_span,
        "weighted_gain_min": min(gain, default=None), "weighted_gain_max": max(gain, default=None), "weighted_gain_span": gain_span,
        "q_span_greater_than_gain_span": branch and q_span > gain_span,
        "valid_child": event["valid"], "parent_improved": event["parent_improved"], "global_improved": event["improved"],
        "admitted": event["branch_admitted"], "classification": event["branch_classification"],
        "parent_gain": event["parent_improvement_margin"], "loss": event["loss"] if event["valid"] else None,
        "attempt_depth": event["branch_attempt_depth"], "success_depth": event["branch_success_depth"],
        "different_choice_followed_by_parent_gain": bool(branch and full_gain and actual == exposure["full_rank_parent_id"] and event["parent_improved"]),
        "claim": "unexecuted alternative rankings do not predict alternative offspring"}


def analyze(study, output, preflight=None):
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Use a new analysis directory; preserve previous reports.")
    # A read-only audit may run on a different machine. Live execution keeps
    # its strict environment gate in study.py; record the audit environment.
    manifest = verify(study)
    if manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("Audit requires the separately frozen study.")
    for block in manifest["protocol"]["blocks"]:
        for role in ("search", "test"):
            split_path(study, manifest, block, role)
    protocol = manifest["protocol"]
    rows, slots, calls, bindings, tag_pairs = [], [], [], [], []
    test_times, end_times = [], []
    for job in manifest["jobs"]:
        directory = study / "runs" / job["job_id"]
        status = read_json(directory / "status.json") if (directory / "status.json").exists() else {"status": "not_started"}
        if status["status"] not in TERMINAL and not (study / "dispatch" / "halt.json").exists():
            raise ValueError("Analysis requires terminal searches or the protocol-defined infrastructure halt.")
        cp = read_json(directory / "checkpoint.json") if (directory / "checkpoint.json").exists() else None
        search = read_json(directory / "search_result.json") if (directory / "search_result.json").exists() else None
        test = read_json(study / "tests" / (job["job_id"] + ".json")) if (study / "tests" / (job["job_id"] + ".json")).exists() else None
        config = read_json(directory / "config.json") if (directory / "config.json").exists() else None
        usage = DurableCalls(directory, config, None).usage() if config else {"calls": [], "call_attempts": 0, "known_tokens": 0, "total_tokens": None, "usage_complete": False}
        records = cp["records"] if cp else []
        local_slots = [selection_metrics(r, job) for r in records]; slots.extend(local_slots)
        local_calls = raw_calls(directory, job["job_id"]) if config else []; calls.extend(local_calls)
        if cp:
            state = restore(cp)
            verify_exposure(cp)
            assert config == cp["config"]
            assert all(config[key] == value for key, value in job.items())
            assert config["binding"] == {"manifest_sha256": manifest["manifest_sha256"]}
            assert config["source"] == manifest["source"] and config["environment"] == manifest["environment"]
            assert config["execution_mode"] == "live"
            for r in records:
                assert r["node"]["generation_mode"] == "live"
            valid = [r["node"] for r in records if r["node"]["evaluation"]["valid"]]
            for a, b in itertools.combinations(valid, 2):
                tag_pairs.append({"job_id": job["job_id"], "a": a["id"], "b": b["id"],
                    "same_allocated_tag": a["allocated_tag"] == b["allocated_tag"],
                    "probe_distance": behavior_distance(a["evaluation"]["behavior"], b["evaluation"]["behavior"]),
                    "exact_same_probe": a["evaluation"]["behavior"] == b["evaluation"]["behavior"],
                    "same_validation_vector": a["evaluation"]["per_instance_loss"] == b["evaluation"]["per_instance_loss"],
                    "semantic_truth_available": False})
        if search:
            if not test:
                raise ValueError("Run the separate test stage for every completed search before analysis.")
            assert len(records) == job["steps"]
            assert search["checkpoint_sha256"] == file_sha(directory / "checkpoint.json")
            assert search["selection_frozen_sha256"] == file_sha(directory / "selection_frozen.json")
            assert search["config"] == config
            if test:
                assert test["binding"]["readout_sha256"] == search["selection_frozen_sha256"]
                assert test["config"] == config and test["selected_on"] == "validation"
                test_times.append(test["evaluated_utc"])
        dispatch_path = study / "dispatch" / (job["job_id"] + ".json")
        if dispatch_path.exists():
            end_times.append(read_json(dispatch_path)["finished_utc"])
        events = [r["event"] for r in records]
        dev = [e for e in events if e["branch_parent_development"]]
        seed_loss = test["seed_validation_selected_test_gap"] if test else None
        best_loss = test["primary_test_gap"] if test else None
        row = {"job_id": job["job_id"], "model": job["model"], "controller": job["controller"], "block": job["data_block"],
            "status": status["status"], "planned_proposals": job["steps"], "completed_proposals": len(records),
            "valid_generated": sum(e["valid"] for e in events), "parse_failures": sum(r["node"].get("proposal_failure") is not None for r in records),
            "program_failures": sum(not e["valid"] for e in events),
            "execution_failures_after_parsing": sum(not r["event"]["valid"] and r["node"].get("proposal_failure") is None for r in records),
            "model_calls": usage["call_attempts"], "tokens": usage["total_tokens"],
            "known_tokens": usage["known_tokens"], "usage_complete": usage["usage_complete"],
            "elapsed_seconds": cp["elapsed_seconds"] if cp else None,
            "api_seconds": sum(r.get("seconds") or 0 for r in local_calls),
            "execution_cpu_seconds_including_reference_setup": sum(n["evaluation"]["cpu_seconds"] for n in cp["seeds"] + [r["node"] for r in records]) if cp else None,
            "test_gap": best_loss, "seed_test_gap": seed_loss,
            "test_improvement_over_seed_pp": 100 * (seed_loss-best_loss) if test else None,
            "independent_test_completed": test is not None,
            "test_valid": test["primary_test_valid"] if test else None,
            "best_validation_gap": search["summary"]["best_validation_loss"] if search else None,
            "best_id": test["best_id"] if test else None,
            "branch_admissions": sum(e["branch_admitted"] for e in events), "branch_evaluations": len(dev),
            "valid_branch_children": sum(e["valid"] for e in dev), "branch_parent_improvements": sum(e["parent_improved"] for e in dev),
            "branch_global_improvements": sum(e["improved"] for e in dev),
            "successful_extensions": sum(e["branch_admitted"] for e in dev),
            "max_attempt_depth": max((e["branch_attempt_depth"] for e in dev), default=0),
            "max_success_depth": max((e["branch_success_depth"] or 0 for e in events), default=0),
            "multi_branch_slots": sum(s["multi_branch_slot"] for s in local_slots),
            "family_differentiated_slots": sum(s["family_priority_varies"] for s in local_slots),
            "full_vs_fifo_differences": sum(s["full_vs_fifo_differs"] for s in local_slots),
            "full_vs_gain_differences": sum(s["full_vs_gain_differs"] for s in local_slots),
            "actual_differs_gain": sum(s["actual_differs_gain"] for s in local_slots),
            "length_limited_responses": sum(r["finish_reason"] == "length" or r["output_at_limit"] for r in local_calls),
            "unclosed_thinking_responses": sum(r["unclosed_thinking_block"] for r in local_calls)}
        rows.append(row)
        bindings.append({"job_id": job["job_id"], "restored": cp is not None,
                         "checkpoint_sha256": file_sha(directory / "checkpoint.json") if cp else None,
                         "search_sha256": file_sha(directory / "search_result.json") if search else None,
                         "test_sha256": file_sha(study / "tests" / (job["job_id"] + ".json")) if test else None})
    assert not test_times or not end_times or min(test_times) >= max(end_times)
    complete_rows = [r for r in rows if r["test_gap"] is not None]
    for row in complete_rows:
        assert row["status"] == "search_complete_test_not_run"
    pairs = paired_rows(rows, protocol["blocks"])
    cells = []
    for method in protocol["controllers"]:
        members = [r for r in rows if r["controller"] == method]
        these_slots = [s for s in slots if s["controller"] == method and s["branch_slot"]]
        cell = {"model": "MiniMax-M3", "controller": method, "planned_runs": len(members),
            "complete_runs": sum(r["test_gap"] is not None for r in members), "mean_test_gap": mean([r["test_gap"] for r in members]),
            "mean_seed_test_gap": mean([r["seed_test_gap"] for r in members]),
            "mean_tokens": mean([r["tokens"] for r in members]) if all(r["usage_complete"] for r in members) else None,
            "mean_elapsed_seconds": mean([r["elapsed_seconds"] for r in members]),
            "runs_with_B": sum(r["branch_admissions"] > 0 for r in members)}
        for key in ("completed_proposals", "valid_generated", "parse_failures", "program_failures", "execution_failures_after_parsing", "model_calls", "known_tokens",
                    "branch_admissions", "branch_evaluations", "valid_branch_children", "branch_parent_improvements",
                    "branch_global_improvements", "successful_extensions", "multi_branch_slots", "family_differentiated_slots",
                    "full_vs_fifo_differences", "full_vs_gain_differences", "actual_differs_gain", "length_limited_responses", "unclosed_thinking_responses"):
            cell[key] = sum(r[key] for r in members)
        opportunities = sum(s["available_count"] for s in these_slots)
        cell.update(fallback_entries=sum(s["family_fallback_count"] for s in these_slots), available_entries=opportunities,
                    fallback_fraction=sum(s["family_fallback_count"] for s in these_slots)/opportunities if opportunities else None,
                    q_span_dominates=sum(s["q_span_greater_than_gain_span"] for s in these_slots),
                    q_max=max((s["q_max"] for s in these_slots), default=None),
                    weighted_gain_max=max((s["weighted_gain_max"] for s in these_slots), default=None),
                    max_success_depth=max(r["max_success_depth"] for r in members))
        gate = protocol["mechanism_observability"]
        cell["observability_diagnostic_passed"] = (
            cell["branch_evaluations"] >= gate["per_model_controller_min_branch_evaluations"] and
            cell["valid_branch_children"] >= gate["min_valid_branch_children"] and
            cell["multi_branch_slots"] >= gate["min_multi_branch_slots"])
        cell["proposal_failure_fraction"] = cell["program_failures"] / cell["completed_proposals"] if cell["completed_proposals"] else None
        cell["runs_selecting_seed"] = sum(r["best_id"] in (0, 1, 2) for r in members)
        cells.append(cell)
    differences = [r["delta_pp"] for r in pairs if r["complete_pair"]]
    tolerance = protocol["statistics"]["direction_counts_tolerance_pp"]
    fixed_tokens, relation_tokens = cells[0]["mean_tokens"], cells[1]["mean_tokens"]
    token_increase = relation_tokens/fixed_tokens - 1 if fixed_tokens and relation_tokens is not None else None
    summary = {"study_id": protocol["study_id"], "analysis_utc": utcnow(), "manifest_sha256": manifest["manifest_sha256"],
        "analysis_environment": environment(), "matches_frozen_execution_environment": environment() == manifest["environment"],
        "source_commit": manifest["source_commit"], "search_runtime_commit": protocol["runtime_commit"],
        "metric_notes": {"program_failures": "All invalid proposals, including parser failures; execution_failures_after_parsing excludes parser failures.",
                         "full_vs_gain_differences": "Counterfactual ranking on the recorded state, not a gain-only search experiment.",
                         "tag_behavior_pairs": "Allocated intent tags and finite execution signatures; neither is semantic ground truth."},
        "models": protocol["models"], "planned_search_runs": len(rows), "completed_search_runs": sum(r["test_gap"] is not None for r in rows),
        "search_model_calls": sum(r["model_calls"] for r in rows), "search_known_tokens": sum(r["known_tokens"] for r in rows),
        "search_total_tokens": sum(r["tokens"] for r in rows) if all(r["tokens"] is not None for r in rows) else None,
        "returned_models": sorted({c["returned_model"] for c in calls if c["returned_model"]}),
        "engineering_preflight": read_json(Path(preflight)/"summary.json") if preflight else None,
        "complete_pairs": len(differences), "missing_pairs": len(pairs)-len(differences),
        "mean_delta_pp": mean(differences), "median_delta_pp": statistics.median(differences) if differences else None,
        "descriptive_95pct_interval_pp": percentile_interval(differences, protocol["statistics"]),
        "R_better_pairs": sum(d < -tolerance for d in differences), "ties": sum(abs(d) <= tolerance for d in differences),
        "R_worse_pairs": sum(d > tolerance for d in differences), "mean_token_increase_fraction": token_increase,
        "predefined_practical_delta_pp": protocol["minimum_practical_gain_percentage_points"],
        "predefined_max_token_increase_fraction": protocol["maximum_acceptable_token_increase_fraction"],
        "all_tests_after_all_searches": not test_times or not end_times or min(test_times) >= max(end_times),
        "tests_expected": sum(r["status"] == "search_complete_test_not_run" for r in rows),
        "tests_observed": len(test_times),
        "cells": cells, "failure_classifications": dict(Counter(s["classification"] for s in slots)),
        "claim": "single-model exploratory screen, paired by data block; no confirmatory superiority or family-only causal claim"}
    output.mkdir(parents=True)
    save_json(output / "summary.json", summary, immutable=True)
    save_json(output / "verification.json", {"source_bindings_and_replay": bindings,
        "all_tests_after_all_searches": summary["all_tests_after_all_searches"],
        "last_search_finished_utc": max(end_times) if end_times else None,
        "first_test_evaluated_utc": min(test_times) if test_times else None,
        "unique_proposal_decisions_and_pool_exposures_verified": len(slots),
        "new_model_calls_by_analysis": 0, "program_evaluation_reruns": 0}, immutable=True)
    for name, items in (("runs", rows), ("pairs", pairs), ("cells", cells), ("mechanism_slots", slots), ("calls", calls), ("tag_behavior_pairs", tag_pairs)):
        write_csv(output / (name + ".csv"), items)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    args = parser.parse_args()
    with offline_only():
        report = analyze(args.study, args.output, args.preflight)
    print(json.dumps({k: v for k, v in report.items() if k not in ("cells", "engineering_preflight")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
