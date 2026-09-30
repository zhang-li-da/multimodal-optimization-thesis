"""Offline block-level analysis for the phase-B diagnostic study."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.common import digest, file_sha


STRATEGIES = ("I", "B", "E0", "EG")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mean_ci(values: list[float]) -> dict:
    if not values:
        return {"n_blocks": 0, "mean": None, "lower": None, "upper": None}
    average = statistics.fmean(values)
    if len(values) < 2:
        return {"n_blocks": 1, "mean": average, "lower": None, "upper": None}
    from scipy.stats import t
    half = float(t.ppf(.975, len(values) - 1)) * statistics.stdev(values) / math.sqrt(len(values))
    return {"n_blocks": len(values), "mean": average,
            "lower": average - half, "upper": average + half}


def _job_status(study: Path, job_id: str, *, run_root: str = "runs") -> dict:
    run_dir = study / run_root / job_id
    for name in ("terminal_status.json", "status.json"):
        path = run_dir / name
        if path.exists():
            return read_json(path)
    return {"status": "missing", "reason": "no_terminal_status"}


def _recorded_call_count(run_dir: Path) -> int:
    calls = Path(run_dir) / "calls"
    return sum(1 for path in calls.iterdir() if path.is_dir()
               and ((path / "request.json").exists() or (path / "state.json").exists())) if calls.exists() else 0


def _durable_call_summary(run_dir: Path) -> dict:
    calls_dir = Path(run_dir) / "calls"
    if not calls_dir.exists():
        return {"has_call_logs": False, "request_count": None, "known_tokens": None,
                "usage_complete": None, "unknown_cost": False, "call_status_counts": {}}
    status_counts = defaultdict(int)
    attempted = 0
    known_tokens = 0
    all_usage_complete = True
    any_unknown_cost = False
    has_call_logs = False
    for folder in sorted(path for path in calls_dir.iterdir() if path.is_dir()):
        request_path = folder / "request.json"
        state_path = folder / "state.json"
        response_path = folder / "response.json"
        raw_path = folder / "raw_response.json"
        if not any(path.exists() for path in (request_path, state_path, response_path, raw_path)):
            continue
        has_call_logs = True
        request = read_json(request_path) if request_path.exists() else None
        state = read_json(state_path) if state_path.exists() else {}
        state_status = state.get("status")
        request_sha = digest(request) if request is not None else None
        if (request_sha is not None and state.get("request_sha256") is not None
                and state["request_sha256"] != request_sha):
            raise ValueError(f"Durable call request fingerprint differs: {folder.name}")

        response = None
        recovered_status = state_status
        if response_path.exists():
            response = read_json(response_path)
            if (request_sha is not None and response.get("request_sha256") != request_sha):
                raise ValueError(f"Durable call response fingerprint differs: {folder.name}")
            recovered_status = "response_persisted"
        elif raw_path.exists():
            raw = read_json(raw_path)
            if (request_sha is not None and raw.get("request_sha256") != request_sha
                    or raw.get("envelope_sha256") != digest(raw.get("envelope"))):
                raise ValueError(f"Durable raw call response fingerprint differs: {folder.name}")
            response = decode_response(raw["envelope"])
            recovered_status = "response_recovered_from_raw"

        dispatched = state_status not in (None, "prepared") or response is not None
        if not dispatched:
            status_counts[state_status or "request_only"] += 1
            continue
        attempted += 1
        status_counts[recovered_status or "unknown_state"] += 1
        input_tokens = response.get("input_tokens") if response else None
        output_tokens = response.get("output_tokens") if response else None
        valid_input = type(input_tokens) is int and input_tokens >= 0
        valid_output = type(output_tokens) is int and output_tokens >= 0
        if valid_input:
            known_tokens += input_tokens
        if valid_output:
            known_tokens += output_tokens
        usage_complete = bool(response and response.get("usage_complete") is True
                              and valid_input and valid_output)
        if not usage_complete:
            all_usage_complete = False
            any_unknown_cost = True
    return {"has_call_logs": has_call_logs,
            "request_count": attempted if has_call_logs else None,
            "known_tokens": known_tokens if attempted else None,
            "usage_complete": all_usage_complete if attempted else None,
            "unknown_cost": any_unknown_cost,
            "call_status_counts": dict(sorted(status_counts.items()))}


def _cost_fields(run_dir: Path, summary: dict, terminal: dict) -> dict:
    calls = _durable_call_summary(run_dir)
    if calls["request_count"]:
        known_tokens = calls["known_tokens"]
        usage_complete = calls["usage_complete"]
        request_count = calls["request_count"]
    else:
        known_tokens = summary.get("known_tokens")
        usage_complete = summary.get("usage_complete")
        request_count = (summary.get("request_count")
                         if summary.get("request_count") is not None
                         else (len(terminal["attempted_calls"])
                               if isinstance(terminal.get("attempted_calls"), list)
                               else (calls["request_count"] if calls["has_call_logs"]
                                     else _recorded_call_count(run_dir))))
    return {"known_tokens": known_tokens, "usage_complete": usage_complete,
            "request_count": request_count,
            "call_status_counts": calls["call_status_counts"],
            "unknown_cost": (bool(terminal.get("unknown_cost", False))
                             or calls["unknown_cost"]
                             or bool(request_count and usage_complete is False))}


def _prefix_cost(study: Path, candidate: dict) -> dict:
    if candidate.get("kind") != "continuation_prefix":
        return {"prefix_known_tokens": None, "prefix_token_usage_complete": None,
                "prefix_requests": None, "prefix_wall_seconds": None}
    checkpoint = study / "runs" / candidate["job_id"] / "checkpoint.json"
    if not checkpoint.exists():
        return {"prefix_known_tokens": None, "prefix_token_usage_complete": False,
                "prefix_requests": None, "prefix_wall_seconds": None}
    records = read_json(checkpoint).get("records", [])[:int(candidate["prefix_proposals"])]
    token_rows = [record.get("costs", {}).get("tokens_added") for record in records]
    request_rows = [record.get("costs", {}).get("requests_added", 0) for record in records]
    tokens_complete = (len(records) == int(candidate["prefix_proposals"])
                       and all(isinstance(value, (int, float)) for value in token_rows))
    return {"prefix_known_tokens": sum(value for value in token_rows if isinstance(value, (int, float))),
            "prefix_token_usage_complete": tokens_complete,
            "prefix_requests": sum(int(value or 0) for value in request_rows),
            "prefix_wall_seconds": records[-1].get("elapsed_after") if records else None}


def _test_rows(study: Path, candidate_manifest: dict) -> tuple[list[dict], list[dict]]:
    rows, missing = [], []
    for candidate in candidate_manifest.get("candidates", []):
        result_path = study / "test" / f"{candidate['candidate_id']}.json"
        if not result_path.exists():
            missing.append({"candidate_id": candidate["candidate_id"], "reason": "test_result_missing"})
            continue
        result = read_json(result_path)
        binding = result.get("binding", {})
        if (binding.get("candidate_manifest_sha256") != candidate_manifest.get("test_candidate_manifest_sha256")
                or binding.get("candidate_id") != candidate["candidate_id"]
                or binding.get("candidate_code_sha256") != candidate.get("code_sha256")
                or binding.get("test_data_sha256") != candidate.get("test_data_sha256")):
            raise ValueError(f"Test result binding differs for {candidate['candidate_id']}")
        source_path = (study / candidate["source_path"]).resolve()
        if not source_path.is_relative_to(study):
            raise ValueError("Test candidate source escapes the study directory")
        source = read_json(source_path)
        if candidate.get("kind") == "continuation_prefix":
            source_code = source[str(candidate["prefix_proposals"])].get("code")
        else:
            ref_key = "incumbent" if candidate.get("reference_action") == "I" else "branch"
            ref = source.get(ref_key)
            source_node = next((node for node in source.get("nodes", [])
                                if ref and node.get("id") == ref.get("id")), None)
            source_code = source_node.get("code") if source_node else None
        if (not isinstance(source_code, str)
                or hashlib.sha256(source_code.encode()).hexdigest() != candidate.get("code_sha256")):
            raise ValueError(f"Test candidate source changed for {candidate['candidate_id']}")
        evaluation = result.get("evaluation", {})
        loss = evaluation.get("loss") if evaluation.get("valid") else None
        row = {**candidate, "test_status": "valid" if loss is not None else "invalid",
               "test_gap_percent": float(loss) * 100 if loss is not None else None,
               "test_instance_evaluations": evaluation.get("instance_evaluations"),
               "test_wall_seconds": evaluation.get("wall_seconds"),
               "test_data_snapshot_sha256": evaluation.get("data_snapshot_sha256")}
        row.update(_prefix_cost(study, candidate))
        rows.append(row)
        if loss is None:
            missing.append({"candidate_id": candidate["candidate_id"],
                            "reason": evaluation.get("failure_type") or "invalid_test_program"})
    return rows, missing


def _block_means(rows: list[dict], value_key: str, predicate) -> dict[int, float]:
    by_block = defaultdict(list)
    for row in rows:
        value = row.get(value_key)
        if value is not None and predicate(row):
            by_block[int(row["data_block"])].append(float(value))
    return {block: statistics.fmean(values) for block, values in by_block.items()}


def _contrast(rows: list[dict], jobs: list[dict], left: str, right: str, prefix: int,
              *, branch_available_checkpoints: set[str] | None = None,
              conditional_branch: bool = False, fallback_unavailable: bool = False) -> dict:
    values = {}
    for row in rows:
        if (row.get("kind") == "continuation_prefix"
                and row.get("prefix_proposals") == prefix
                and row.get("test_gap_percent") is not None):
            key = (int(row["data_block"]), row["checkpoint_id"], int(row["repetition"]))
            values[(key[0], key[1], key[2], row["strategy"])] = float(row["test_gap_percent"])

    available = branch_available_checkpoints or set()
    planned = set()
    all_blocks = sorted({int(job["data_block"]) for job in jobs
                         if job["strategy"] in {left, right}})
    for job in jobs:
        if job["strategy"] not in {left, right}:
            continue
        key = (int(job["data_block"]), job["checkpoint_id"], int(job["repetition"]))
        if conditional_branch and key[1] not in available:
            continue
        planned.add(key)

    paired_by_block = defaultdict(list)
    paired_counts, expected_counts = defaultdict(int), defaultdict(int)
    missing_pairs = []
    for block, _, _ in planned:
        expected_counts[block] += 1
    for block, checkpoint, repetition in sorted(planned):
        key = (block, checkpoint, repetition)
        left_value = values.get((*key, left))
        right_value = values.get((*key, right))
        if fallback_unavailable and right == "B" and checkpoint not in available:
            # The all-state B policy routes unavailable B slots to I; this is a
            # declared fallback estimate, not an observed B continuation.
            right_value = values.get((*key, "I"))
        if left_value is not None and right_value is not None:
            paired_by_block[block].append(right_value - left_value)
            paired_counts[block] += 1
        else:
            missing_pairs.append({"block": block, "checkpoint_id": checkpoint,
                                  "repetition": repetition,
                                  "missing_left": left_value is None,
                                  "missing_right": right_value is None})
    block_differences = {block: statistics.fmean(diffs)
                         for block, diffs in paired_by_block.items() if diffs}
    complete_blocks = [block for block in all_blocks if block in block_differences
                       and paired_counts[block] == expected_counts[block]]
    complete_ci = mean_ci([block_differences[block] for block in complete_blocks])
    return {"contrast_label": f"{right}-{left}",
            "difference_unit": "percentage_points; positive means right is worse",
            "prefix_proposals": prefix,
            "conditional_on_eligible_branch": conditional_branch,
            "unavailable_branch_fallback": "I" if fallback_unavailable else None,
            "pairing_basis": "same block/checkpoint/repetition assignment; no shared model RNG claimed",
            "block_equal_difference_pp": mean_ci(list(block_differences.values())),
            "complete_block_sensitivity_pp": complete_ci,
            "complete_blocks": complete_blocks,
            "block_differences_pp": {str(k): v for k, v in sorted(block_differences.items())},
            "missing_pairs": missing_pairs,
            "expected_pairs_by_block": {str(k): v for k, v in sorted(expected_counts.items())},
            "observed_pairs_by_block": {str(k): v for k, v in sorted(paired_counts.items())},
            "n_blocks_planned": len(all_blocks), "n_blocks_observed": len(block_differences)}


def analyze(study: Path) -> dict:
    study = Path(study).resolve()
    manifest = read_json(study / "manifest.json")
    jobs = manifest.get("jobs", [])
    terminal_rows, run_rows, start_rows, event_rows = [], [], [], []
    for job in jobs:
        job_id = job["job_id"]
        terminal = _job_status(study, job_id)
        run_dir = study / "runs" / job_id
        result_path, checkpoint_path = run_dir / "search_result.json", run_dir / "checkpoint.json"
        result = read_json(result_path) if result_path.exists() else {}
        summary = result.get("summary", {})
        costs = _cost_fields(run_dir, summary, terminal)
        terminal = {**terminal, "unknown_cost": costs["unknown_cost"],
                    "missing_outcome": terminal.get("missing_outcome", not result_path.exists())}
        terminal_rows.append({"job_id": job_id, "block": job["data_block"],
                              "checkpoint_id": job["checkpoint_id"],
                              "strategy": job["strategy"], "repetition": job["repetition"],
                              **terminal})
        run_rows.append({"job_id": job_id, "block": job["data_block"],
                         "checkpoint_id": job["checkpoint_id"], "strategy": job["strategy"],
                         "repetition": job["repetition"], "status": terminal.get("status"),
                         "completed_proposals": summary.get("completed_proposals"),
                         **costs,
                         "wall_seconds": summary.get("wall_seconds"),
                         "global_improvements": summary.get("global_improvements"),
                         "project_progress": summary.get("project_progress"),
                         "exploration_admissions": summary.get("exploration_admissions"),
                         "missing_outcome": terminal["missing_outcome"]})
        if not checkpoint_path.exists():
            continue
        saved = read_json(checkpoint_path)
        checkpoint_state = saved.get("state", {})
        if job["strategy"] in {"E0", "EG"}:
            first = next((record for record in saved.get("records", [])), None)
            if first:
                node, event = first["node"], first.get("event", {})
                incumbent_node = next((n for n in checkpoint_state.get("nodes", [])
                                       if n.get("id") == checkpoint_state.get("start_incumbent_id")), {})
                start_rows.append({"job_id": job_id, "block": job["data_block"],
                    "checkpoint_id": job["checkpoint_id"], "strategy": job["strategy"],
                    "repetition": job["repetition"],
                    "valid": bool(node.get("evaluation", {}).get("valid")),
                    "validation_loss": node.get("evaluation", {}).get("loss"),
                    "incumbent_validation_loss": checkpoint_state.get("start_incumbent_loss"),
                    "validation_gap_to_incumbent": (
                        node.get("evaluation", {}).get("loss") - checkpoint_state.get("start_incumbent_loss")
                        if node.get("evaluation", {}).get("valid") and checkpoint_state.get("start_incumbent_loss") is not None
                        else None),
                    "within_quality_tolerance": (
                        node.get("evaluation", {}).get("loss") - checkpoint_state.get("start_incumbent_loss") <= .035
                        if node.get("evaluation", {}).get("valid") and checkpoint_state.get("start_incumbent_loss") is not None
                        else False),
                    "structure_changed_from_parent": bool(node.get("structure_fingerprint")
                        and node.get("structure_fingerprint") != incumbent_node.get("structure_fingerprint")),
                    "behavior_changed_from_incumbent": (
                        node.get("behavior_cell_id") != incumbent_node.get("behavior_cell_id")),
                    "behavior_cell_id": node.get("behavior_cell_id"),
                    "hypothesis_valid": event.get("strategy_hypothesis_valid"),
                    "test_not_used_for_start_selection": True})
        for event in checkpoint_state.get("events", []):
            event_rows.append({"job_id": job_id, "block": job["data_block"],
                               "checkpoint_id": job["checkpoint_id"],
                               "strategy": job["strategy"], **event})

    test_gate_path = study / "test_gate.json"
    candidate_manifest_path = study / "test_candidate_manifest.json"
    gate = read_json(test_gate_path) if test_gate_path.exists() else None
    test_rows, missing_test = [], []
    if gate and gate.get("released"):
        if not candidate_manifest_path.exists():
            raise ValueError("released Test gate is missing its frozen candidate manifest")
        candidate_manifest = read_json(candidate_manifest_path)
        test_data_path = study / "test_data_manifest.json"
        if not test_data_path.exists():
            raise ValueError("released Test gate is missing its frozen Test data manifest")
        test_data_manifest = read_json(test_data_path)
        if (digest({key: value for key, value in candidate_manifest.items()
                    if key != "test_candidate_manifest_sha256"})
                != candidate_manifest.get("test_candidate_manifest_sha256")
                or gate.get("test_candidate_manifest_sha256")
                != candidate_manifest.get("test_candidate_manifest_sha256")
                or digest({key: value for key, value in test_data_manifest.items()
                           if key != "manifest_sha256"}) != test_data_manifest.get("manifest_sha256")
                or gate.get("test_data_manifest_sha256") != test_data_manifest.get("manifest_sha256")
                or candidate_manifest.get("test_data_manifest_sha256") != test_data_manifest.get("manifest_sha256")):
            raise ValueError("test candidate/data manifest digest or gate binding differs")
        for data_row in test_data_manifest.get("data", []):
            test_path = (study / data_row["path"]).resolve()
            if not test_path.is_relative_to(study) or file_sha(test_path) != data_row["sha256"]:
                raise ValueError(f"Test data snapshot changed: {data_row['path']}")
        test_rows, missing_test = _test_rows(study, candidate_manifest)
    else:
        candidate_manifest = None

    prefix_run_rows, actual_search_evals, shared_seed_evals = [], 0, 0
    for job in manifest.get("prefix_jobs", []):
        run_dir = study / "prefix_runs" / job["job_id"]
        result_path, checkpoint_path = run_dir / "search_result.json", run_dir / "checkpoint.json"
        result = read_json(result_path) if result_path.exists() else {}
        saved = read_json(checkpoint_path) if checkpoint_path.exists() else {}
        terminal = _job_status(study, job["job_id"], run_root="prefix_runs")
        summary = result.get("summary", {})
        costs = _cost_fields(run_dir, summary, terminal)
        records, seeds = saved.get("records", []), saved.get("seeds", [])
        actual_search_evals += sum(int(row.get("node", {}).get("evaluation", {}).get(
            "instance_evaluations", 0) or 0) for row in records)
        shared_seed_evals += sum(int(node.get("evaluation", {}).get("instance_evaluations", 0) or 0)
                                 for node in seeds)
        prefix_run_rows.append({"job_id": job["job_id"], "block": job["data_block"],
            "status": terminal.get("status"), "reason": terminal.get("reason"),
            "completed_proposals": result.get("summary", {}).get("completed_proposals", len(records)),
            **costs,
            "missing_outcome": terminal.get("missing_outcome", not result_path.exists()),
            "wall_seconds": result.get("summary", {}).get("wall_seconds"),
            "proposal_evaluator_instances": sum(int(row.get("node", {}).get("evaluation", {}).get(
                "instance_evaluations", 0) or 0) for row in records),
            "shared_seed_evaluator_instances": sum(int(node.get("evaluation", {}).get(
                "instance_evaluations", 0) or 0) for node in seeds)})
    for row in run_rows:
        run_dir = study / "runs" / row["job_id"]
        checkpoint_path = run_dir / "checkpoint.json"
        if checkpoint_path.exists():
            saved = read_json(checkpoint_path)
            actual_search_evals += sum(int(item.get("node", {}).get("evaluation", {}).get(
                "instance_evaluations", 0) or 0) for item in saved.get("records", []))

    test_eval_instances = sum(int(row.get("test_instance_evaluations") or 0) for row in test_rows)
    known_token_rows = [row.get("known_tokens") for row in prefix_run_rows + run_rows
                        if isinstance(row.get("known_tokens"), (int, float))]
    request_rows = [row.get("request_count") for row in prefix_run_rows + run_rows
                    if isinstance(row.get("request_count"), (int, float))]
    task_counts = defaultdict(int)
    for row in terminal_rows:
        task_counts[row["status"]] += 1
    unknown_cost_jobs = sorted({row["job_id"] for row in prefix_run_rows + terminal_rows
                                if row.get("unknown_cost")})
    missing_prefix_jobs = [row for row in prefix_run_rows
                           if row.get("status") != "search_complete_test_not_run"]
    resource_summary = {"planned": manifest.get("protocol", {}).get("stages", {}).get("B", {}),
        "public_prefix_jobs": len(prefix_run_rows), "continuation_jobs": len(run_rows),
        "public_prefix_terminal_status_counts": dict(sorted(
            (status, sum(row.get("status") == status for row in prefix_run_rows))
            for status in {row.get("status") for row in prefix_run_rows})),
        "completed_proposals": sum(int(row.get("completed_proposals") or 0)
                                    for row in prefix_run_rows + run_rows),
        "observed_known_token_sum_lower_bound": sum(known_token_rows),
        "all_observed_token_usage_complete": (not unknown_cost_jobs and all(
            row.get("usage_complete") is True
            for row in prefix_run_rows + run_rows if row.get("request_count"))),
        "observed_request_attempts": sum(request_rows),
        "actual_search_evaluator_instances": actual_search_evals + shared_seed_evals,
        "public_prefix_proposal_evaluator_instances": sum(
            row["proposal_evaluator_instances"] for row in prefix_run_rows),
        "shared_seed_evaluator_instances": shared_seed_evals,
        "continuation_proposal_evaluator_instances": actual_search_evals - sum(
            row["proposal_evaluator_instances"] for row in prefix_run_rows),
        "test_evaluator_instances": test_eval_instances,
        "test_candidate_results_present": len(test_rows),
        "unknown_cost_job_count": len(unknown_cost_jobs)}
    quality_budget_rows = [{key: row.get(key) for key in (
        "candidate_id", "data_block", "strategy", "prefix_proposals", "test_gap_percent",
        "prefix_known_tokens", "prefix_token_usage_complete", "prefix_requests",
        "prefix_wall_seconds", "test_instance_evaluations")} for row in test_rows
        if row.get("kind") == "continuation_prefix"]

    block_quality = {}
    if candidate_manifest is not None:
        for strategy in STRATEGIES:
            for prefix in (4, 8):
                block_quality[f"{strategy}-p{prefix}"] = {
                    str(block): value for block, value in sorted(_block_means(
                        test_rows, "test_gap_percent", lambda row, s=strategy, p=prefix:
                        row.get("kind") == "continuation_prefix" and row.get("strategy") == s
                        and row.get("prefix_proposals") == p).items())}

    contrasts = {}
    if candidate_manifest is not None:
        branch_available = {row["checkpoint_id"] for row in candidate_manifest.get("candidates", [])
                             if row.get("kind") == "checkpoint_start_reference"
                             and row.get("reference_action") == "B"}
        for prefix in (4, 8):
            contrasts[f"EG_minus_E0_p{prefix}"] = _contrast(test_rows, jobs, "E0", "EG", prefix)
            contrasts[f"E0_minus_I_p{prefix}"] = _contrast(test_rows, jobs, "I", "E0", prefix)
            contrasts[f"EG_minus_I_p{prefix}"] = _contrast(test_rows, jobs, "I", "EG", prefix)
            contrasts[f"B_conditional_minus_I_p{prefix}"] = _contrast(
                test_rows, jobs, "I", "B", prefix,
                branch_available_checkpoints=branch_available, conditional_branch=True)
            contrasts[f"B_all_states_fallback_I_minus_I_p{prefix}"] = _contrast(
                test_rows, jobs, "I", "B", prefix,
                branch_available_checkpoints=branch_available, fallback_unavailable=True)

    starts = {}
    for strategy in ("E0", "EG"):
        values_by_block = defaultdict(list)
        for row in start_rows:
            if row["strategy"] == strategy and row.get("validation_gap_to_incumbent") is not None:
                values_by_block[row["block"]].append(row["validation_gap_to_incumbent"] * 100)
        starts[strategy] = {"validation_gap_to_incumbent_pp": mean_ci(
            [statistics.fmean(values) for values in values_by_block.values()]),
            "valid_starts": sum(row["strategy"] == strategy and row["valid"] for row in start_rows),
            "near_incumbent_starts": sum(row["strategy"] == strategy and row["within_quality_tolerance"] for row in start_rows),
            "start_records": [row for row in start_rows if row["strategy"] == strategy]}

    missing_jobs = [row for row in terminal_rows if row["status"] != "continuation_complete"]
    conversion = {}
    for strategy in ("E0", "EG"):
        events = [row for row in event_rows if row.get("strategy") == strategy]
        progress_events = [row for row in events if row.get("project_progress")]
        progress = len(progress_events)
        global_improvement = sum(bool(row.get("global_improvement")) for row in progress_events)
        conversion[strategy] = {"proposal_events": len(events),
            "project_progress_events": progress, "global_improvement_events": global_improvement,
            "project_progress_to_global_improvement_fraction": (
                global_improvement / progress if progress else None),
            "interpretation": "event count is descriptive and not an independent sample"}

    return {"schema": "chapter6-phase-b-analysis-v1", "study_id": manifest.get("study_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "status": "analyzed_after_test_gate" if gate and gate.get("released") else "search_status_only_test_not_released",
        "inference_unit": "data block; repetitions and checkpoints aggregated within block",
        "model_randomness_note": "repetition labels do not imply shared service randomness",
        "planned_job_count": len(jobs), "terminal_status_counts": dict(sorted(task_counts.items())),
        "missing_or_noncomplete_jobs": missing_jobs, "unknown_cost_job_ids": unknown_cost_jobs,
        "missing_or_noncomplete_prefix_jobs": missing_prefix_jobs,
        "search_cost_rows": run_rows, "validation_start_diagnostics": starts,
        "project_to_global_conversion": conversion,
        "test_gate": gate, "test_candidate_count": candidate_manifest.get("candidate_count") if candidate_manifest else None,
        "test_outcome_rows": test_rows, "missing_test_outcomes": missing_test,
        "test_block_quality_percent": block_quality, "test_contrasts": contrasts,
        "quality_budget_rows": quality_budget_rows,
        "public_prefix_cost_rows": prefix_run_rows, "resource_summary": resource_summary,
        "limitations": ["Stage B is a development diagnostic, not confirmation.",
            "Test contrasts are block-level descriptive estimates and do not select a method.",
            "Unavailable B branches remain missing in conditional analysis; fallback-to-I is separately labeled.",
            "No test result is manufactured for sent_unknown, invalid, or absent outcomes."]}


def write_report(result: dict, output: Path) -> None:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANALYSIS.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    lines = ["# 阶段 B 区块级诊断报告", "",
             f"状态：`{result['status']}`。", "",
             f"计划续开发任务 {result['planned_job_count']} 个；终态计数：`{json.dumps(result['terminal_status_counts'], ensure_ascii=False, sort_keys=True)}`。",
             "重复和检查点先在区块内汇总；区间的独立单位是数据区块。重复编号不代表模型服务共享随机种子。", ""]
    if result["status"] != "analyzed_after_test_gate":
        lines += ["最终 Test 尚未整体放行，本报告不含 Test 质量结果。", ""]
    else:
        lines += ["Test gap 以适配器 `loss × 100` 报告；正的右减左差表示右侧更差。", "",
                  "| 比较 | proposal 前缀 | 区块等权差 (pp) | 95% CI | 区块数 |",
                  "|---|---:|---:|---:|---:|"]
        for name, contrast in result["test_contrasts"].items():
            ci = contrast["block_equal_difference_pp"]
            interval = (f"[{ci['lower']:.4f}, {ci['upper']:.4f}]"
                        if ci["lower"] is not None else "未估")
            lines.append(f"| {name} | {contrast['prefix_proposals']} | "
                         f"{ci['mean']:.4f} | {interval} | {ci['n_blocks']} |")
        lines.append("")
    lines += ["## 缺失与成本", "",
              f"非完整任务：{len(result['missing_or_noncomplete_jobs'])}；未知成本任务：{len(result['unknown_cost_job_ids'])}；" 
              f"缺失/无效 Test 结果：{len(result['missing_test_outcomes'])}。缺失值不填零。", "",
              "完整任务、失败任务、sent_unknown 和不可用分支均保存在 `ANALYSIS.json`。此阶段用于诊断起点、期限与机会成本，不构成确认性方法结论。", ""]
    (output / "REPORT_ZH.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    write_report(analyze(args.study), args.output_dir)


if __name__ == "__main__":
    main()
