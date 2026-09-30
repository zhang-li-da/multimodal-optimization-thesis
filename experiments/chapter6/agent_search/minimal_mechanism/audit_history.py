"""Read-only audit of the frozen Chapter 6 short-horizon evidence."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from experiments.chapter6.agent_search.component_validation.evaluator import evaluate_test

CV = ROOT / "experiments/chapter6/agent_search/component_validation"
RESULTS = CV / "results/short-horizon-20260929"
ZIP_PATH = RESULTS / "raw-study.zip"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def zip_json(archive: zipfile.ZipFile, name: str):
    return json.loads(archive.read(name).decode("utf-8"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(payload)


def mean(values):
    return statistics.fmean(values) if values else None


def sd(values):
    return statistics.stdev(values) if len(values) > 1 else None


def t_interval(values, confidence=0.95):
    if len(values) < 2:
        return {"n": len(values), "mean": mean(values), "lower": None, "upper": None}
    from scipy.stats import t
    avg = mean(values)
    half = float(t.ppf((1 + confidence) / 2, len(values) - 1)) * sd(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": avg, "lower": avg - half, "upper": avg + half}


def compact_stats(values):
    return {"n": len(values), "mean": mean(values), "sd": sd(values),
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None}


def audit_e2_events(root: Path):
    events = []
    runs = 0
    for run_dir in sorted((root / "runs").iterdir()):
        checkpoint_path = run_dir / "checkpoint.json"
        if not checkpoint_path.exists():
            continue
        checkpoint = read_json(checkpoint_path)
        events.extend(record.get("event", {}) for record in checkpoint.get("records", []))
        runs += 1
    branch = [e for e in events if e.get("branch_development") is True]
    improved = [e for e in branch if e.get("local_improvement") is True]
    reasons = Counter(e.get("admission_reason") for e in improved)
    return {
        "runs": runs,
        "records": len(events),
        "branch_development_events": len(branch),
        "new_direction_children": sum(e.get("new_direction") is True for e in branch),
        "parent_improvement_events": len(improved),
        "improvements_with_new_grant": sum(
            e.get("protection_grant_awarded", 0) > 0 or e.get("development_grant_awarded", 0) > 0
            for e in improved),
        "admission_reasons": {str(k): v for k, v in sorted(reasons.items(), key=lambda item: str(item[0]))},
    }


def audit_e1_cb(root: Path):
    rows = []
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
        start_id = state["start_incumbent_id"]
        nodes = state["nodes"]
        if isinstance(nodes, dict):
            start = nodes.get(str(start_id), nodes.get(start_id))
        else:
            start = next(n for n in nodes if n["id"] == start_id)
        snapshot = read_json(root / "data" / f"test-b{config['data_block']}.json")
        baseline = evaluate_test(start["code"], snapshot)
        final = read_json(test_path)
        baseline_gap = baseline.get("loss")
        final_gap = final.get("primary_test_gap")
        global_improvements = sum(bool(record.get("event", {}).get("global_improvement"))
                                  for record in checkpoint.get("records", []))
        rows.append({
            "job_id": run_dir.name,
            "validation_global_improvement_events": global_improvements,
            "baseline_test_gap": baseline_gap,
            "final_test_gap": final_gap,
            "test_not_better_than_start": baseline_gap is None or final_gap is None or final_gap >= baseline_gap - 1e-12,
        })
    improved = [row for row in rows if row["validation_global_improvement_events"] > 0]
    return {
        "C_B_jobs_with_test": len(rows),
        "validation_improved_jobs": len(improved),
        "validation_improved_but_test_not_better": sum(row["test_not_better_than_start"] for row in improved),
        "rows": rows,
    }


def archive_audit(archive: zipfile.ZipFile, analysis: dict):
    index = read_json(RESULTS / "ARCHIVE_INDEX.json")
    audit = read_json(RESULTS / "ARCHIVE_AUDIT.json")
    members = set(archive.namelist())
    index_paths = {row["path"] for row in index}
    index_mismatches = []
    for row in index:
        try:
            payload = archive.read(row["path"])
        except KeyError:
            index_mismatches.append({"path": row["path"], "issue": "missing_from_zip"})
            continue
        if len(payload) != row["bytes"] or sha256(payload) != row["sha256"]:
            index_mismatches.append({"path": row["path"], "issue": "size_or_sha256_mismatch"})
    unindexed = sorted(members - index_paths)
    audit_mismatches = []
    for row in audit["files"]:
        path = "study/" + row["path"]
        try:
            payload = archive.read(path)
        except KeyError:
            audit_mismatches.append({"path": path, "issue": "missing_from_zip"})
            continue
        if len(payload) != row["bytes"] or sha256(payload) != row["sha256"]:
            audit_mismatches.append({"path": path, "issue": "size_or_sha256_mismatch"})

    manifest = zip_json(archive, "study/manifest.json")
    frozen_protocol = read_json(ROOT / manifest["protocol_path"])
    manifest_sha = manifest.get("manifest_sha256")
    manifest_payload = dict(manifest)
    manifest_payload.pop("manifest_sha256", None)
    manifest_hash_recomputed = canonical_digest(manifest_payload)
    data_files = [x for x in index if x["path"].startswith("study/")]
    study_file_paths = {x["path"] for x in audit["files"]}
    source_checks = {}
    for group in ("source", "tooling_source"):
        declared = manifest.get(group, {}).get("files", {})
        checked = []
        for path, expected in declared.items():
            source_path = ROOT / path
            if not source_path.exists():
                checked.append({"path": path, "matches": False, "issue": "missing_local_source"})
                continue
            normalized = source_path.read_bytes().replace(b"\r\n", b"\n")
            actual = sha256(normalized)
            checked.append({"path": path, "expected_sha256": expected,
                            "actual_sha256": actual, "matches": actual == expected})
        source_checks[group] = {
            "declared_file_count": len(declared),
            "checked_file_count": len(checked),
            "matching_file_count": sum(row["matches"] for row in checked),
            "mismatches": [row for row in checked if not row["matches"]],
        }
    return {
        "raw_zip": {
            "path": ZIP_PATH.relative_to(ROOT).as_posix(),
            "bytes": ZIP_PATH.stat().st_size,
            "sha256": sha256(ZIP_PATH.read_bytes()),
            "member_count": len(members),
            "directory_members": sum(name.endswith("/") for name in members),
        },
        "archive_index": {
            "record_count": len(index),
            "indexed_study_payload_count": len(data_files),
            "index_matches_zip_members": not unindexed and not index_mismatches,
            "unindexed_members": unindexed,
            "mismatches": index_mismatches,
        },
        "archive_audit": {
            "declared_study_file_count": audit["file_count"],
            "records": len(audit["files"]),
            "matches_indexed_study_paths": len(study_file_paths) == len(data_files),
            "matches_zip_hashes_and_sizes": not audit_mismatches,
            "mismatches": audit_mismatches,
        },
        "frozen_manifest": {
            "study_id": manifest["study_id"],
            "declared_sha256": manifest_sha,
            "recomputed_canonical_sha256": manifest_hash_recomputed,
            "canonical_hash_matches": manifest_sha == manifest_hash_recomputed,
            "planned_checkpoints": manifest["planned_checkpoints"],
            "planned_jobs": manifest["planned_jobs"],
            "planned_proposals": manifest["planned_proposals"],
            "planned_model_requests": manifest["planned_model_requests"],
            "job_count": len(manifest["jobs"]),
            "checkpoint_count": len(manifest["checkpoints"]),
            "data_record_count": len(manifest["data"]),
            "source_commit": manifest.get("source_commit"),
            "protocol_sha256": manifest.get("protocol_sha256"),
            "protocol_path": manifest.get("protocol_path"),
            "protocol_declared_status": frozen_protocol.get("status"),
            "manifest_freeze_status": manifest.get("status"),
            "data_overlap_audit": manifest.get("overlap_check"),
        },
        "source_file_integrity": source_checks,
        "reported_analysis_manifest_sha256": analysis["study_manifest_sha256"],
        "analysis_manifest_hash_matches": analysis["study_manifest_sha256"] == manifest_sha,
    }


def read_short_horizon(archive: zipfile.ZipFile, analysis: dict):
    manifest = zip_json(archive, "study/manifest.json")
    rows = {}
    statuses = Counter()
    baseline_test_cache = {}
    for job in manifest["jobs"]:
        job_id = job["job_id"]
        dispatch = zip_json(archive, f"study/dispatch/{job_id}.json")
        status = dispatch["status"]
        statuses[status] += 1
        test_path = f"study/tests/{job_id}.json"
        test = zip_json(archive, test_path) if test_path in archive.namelist() else None
        checkpoint_path = f"study/checkpoints/{job['checkpoint_id']}.json"
        checkpoint = zip_json(archive, checkpoint_path)
        incumbent = next(n for n in checkpoint["nodes"] if n["id"] == checkpoint["incumbent"]["id"])
        baseline_test = None
        if test is not None:
            checkpoint_id = job["checkpoint_id"]
            if checkpoint_id not in baseline_test_cache:
                test_snapshot = zip_json(archive, f"study/data/test-b{job['data_block']}.json")
                baseline_eval = evaluate_test(incumbent["code"], test_snapshot)
                baseline_test_cache[checkpoint_id] = baseline_eval["loss"] if baseline_eval.get("valid") else 1.0
            baseline_test = baseline_test_cache[checkpoint_id]
        summary = dispatch.get("summary", {})
        rows[job_id] = {
            "job_id": job_id, "block": int(job["data_block"]),
            "phase": checkpoint["config"]["phase"],
            "strategy": job["strategy"], "repetition": int(job["repetition"]),
            "status": status, "test_gap": test.get("primary_test_gap") if test else None,
            "baseline_test_gap": baseline_test,
            "test_improvement": (baseline_test - test["primary_test_gap"])
                if test and baseline_test is not None else None,
            "checkpoint_incumbent_validation_loss": incumbent["evaluation"]["loss"],
            "test_evaluation_matches_primary": bool(test and test.get("evaluation", {}).get("loss")
                == test.get("primary_test_gap")),
            "known_tokens": summary.get("known_tokens"),
            "request_count": summary.get("request_count"),
            "global_improvements": summary.get("global_improvements"),
            "parent_improvements": summary.get("parent_improvements"),
            "project_progress": summary.get("project_progress"),
            "exploration_followups": summary.get("exploration_followups"),
            "checkpoint": checkpoint,
        }

    pair_rows = []
    missing_pairs = []
    for strategy in ("C-B", "C-E"):
        diffs_by_block = defaultdict(list)
        for row in rows.values():
            if row["strategy"] != strategy:
                continue
            key = (row["block"], row["phase"], row["repetition"])
            ci_id = f"b{key[0]}-{key[1]}-ci-r{key[2]}"
            ci = rows.get(ci_id)
            if row["test_gap"] is None or ci is None or ci["test_gap"] is None:
                missing_pairs.append({"strategy": strategy, "job_id": row["job_id"],
                                      "paired_incumbent_job_id": ci_id,
                                      "reason": row["status"] if row["test_gap"] is None else "missing_CI"})
                continue
            diff_pp = (row["test_gap"] - ci["test_gap"]) * 100
            diffs_by_block[row["block"]].append(diff_pp)
            pair_rows.append({"strategy": strategy, "block": row["block"],
                              "phase": row["phase"], "repetition": row["repetition"],
                              "job_id": row["job_id"], "incumbent_job_id": ci_id,
                              "difference_pp": diff_pp})

        block_means = {block: mean(values) for block, values in sorted(diffs_by_block.items())}
        complete_blocks = [block for block, values in sorted(diffs_by_block.items()) if len(values) == 4]
        contrast = {
            "task_pairs": len([x for x in pair_rows if x["strategy"] == strategy]),
            "task_weighted_difference_pp": mean([x["difference_pp"] for x in pair_rows if x["strategy"] == strategy]),
            "block_equal_difference_pp": t_interval(list(block_means.values())),
            "block_differences_pp": block_means,
            "complete_blocks_only": {
                "blocks": complete_blocks,
                "difference_pp": t_interval([block_means[b] for b in complete_blocks]),
            },
            "paired_job_count_by_block": {str(k): len(v) for k, v in sorted(diffs_by_block.items())},
        }
        if strategy == "C-B":
            c_b = contrast
        else:
            c_e = contrast

    # Recompute all-proposal C-E project and global progress from candidate records.
    exploration_rows = []
    ce_run_trajectories = {}
    for job_id, row in rows.items():
        if row["strategy"] != "C-E":
            continue
        run_prefix = f"study/runs/{job_id}"
        checkpoint = row["checkpoint"]
        incumbent_loss = next(n["evaluation"]["loss"] for n in checkpoint["nodes"]
                              if n["id"] == checkpoint["incumbent"]["id"])
        start_name = f"{run_prefix}/slots/000/candidate.json"
        first = zip_json(archive, start_name) if start_name in archive.namelist() else None
        start_loss = first.get("evaluation", {}).get("loss") if first else None
        exploration_rows.append({"job_id": job_id, "block": row["block"],
                                 "phase": row["phase"], "repetition": row["repetition"],
                                 "status": row["status"], "start_loss": start_loss,
                                 "incumbent_loss": incumbent_loss,
                                 "start_minus_incumbent_pp": (start_loss - incumbent_loss) * 100
                                     if start_loss is not None else None,
                                 "valid_start": bool(first and first.get("evaluation", {}).get("valid"))})
        best_project = None
        best_global = incumbent_loss
        project_progress_count = 0
        global_improvement_count = 0
        steps = []
        for slot in range(4):
            name = f"{run_prefix}/slots/{slot:03}/candidate.json"
            if name not in archive.namelist():
                continue
            candidate = zip_json(archive, name)
            evaluation = candidate.get("evaluation", {})
            loss = evaluation.get("loss")
            valid = evaluation.get("valid") is True and isinstance(loss, (int, float))
            allocation = candidate.get("allocation", {})
            local_gain = bool(valid and best_project is not None and loss < best_project - 1e-4)
            global_gain = bool(valid and loss < best_global - 1e-4)
            if valid and (best_project is None or local_gain):
                best_project = loss
            if valid and global_gain:
                best_global = loss
            project_progress_count += int(local_gain)
            global_improvement_count += int(global_gain)
            steps.append({"step": slot, "candidate_id": candidate.get("id"),
                          "action": candidate.get("action"), "parent_id": candidate.get("parent_id"),
                          "investment_id": allocation.get("investment_id"),
                          "project_best_id_before": allocation.get("project_best_id"),
                          "validation_loss": loss, "valid": valid,
                          "project_progress_recomputed": local_gain,
                          "global_improvement_recomputed": global_gain,
                          "program_hash": evaluation.get("program_hash"),
                          "behavior_cell": candidate.get("tags")})
        ce_run_trajectories[job_id] = {
            "recomputed_project_progress": project_progress_count,
            "reported_project_progress": row["project_progress"],
            "recomputed_global_improvements": global_improvement_count,
            "reported_global_improvements": row["global_improvements"],
            "steps": steps,
        }

    start_gaps = [r["start_minus_incumbent_pp"] for r in exploration_rows if r["start_minus_incumbent_pp"] is not None]
    completed_ce = [r for r in rows.values() if r["strategy"] == "C-E" and r["test_gap"] is not None]
    positive_ce = [r for r in completed_ce if r["test_improvement"] > 1e-12]
    all_rows_by_strategy = defaultdict(list)
    for row in rows.values():
        if row["test_gap"] is not None:
            all_rows_by_strategy[row["strategy"]].append(row)

    current_report_comparison = {}
    for strategy, report_key in (("C-I", "C-I"), ("C-B", "C-B"), ("C-E", "C-E")):
        actual = [r["test_gap"] for r in all_rows_by_strategy[strategy]]
        reported = analysis["strategies"][report_key]["test_gap"]["mean"]
        current_report_comparison[strategy] = {
            "recomputed_task_mean_gap": mean(actual),
            "reported_task_mean_gap": reported,
            "absolute_difference": mean(actual) - reported if actual else None,
            "task_count": len(actual),
        }
    report_rows = {row["job_id"]: row for row in analysis["rows"]}
    baseline_differences = []
    for row in rows.values():
        if row["baseline_test_gap"] is None:
            continue
        reported = report_rows[row["job_id"]].get("baseline_test_gap")
        if reported is not None:
            baseline_differences.append(abs(row["baseline_test_gap"] - reported))
    baseline_recomputation = {
        "recomputed_jobs": len(baseline_differences),
        "reported_baselines_matched": sum(x < 1e-12 for x in baseline_differences),
        "maximum_absolute_difference": max(baseline_differences) if baseline_differences else None,
        "all_final_test_records_match_reported_evaluation": all(r["test_evaluation_matches_primary"] for r in rows.values() if r["test_gap"] is not None),
    }

    cost_by_strategy = {}
    for strategy in ("C-I", "C-B", "C-E"):
        group = [r for r in rows.values() if r["strategy"] == strategy]
        known = [r["known_tokens"] for r in group if isinstance(r["known_tokens"], (int, float))]
        requests = [r["request_count"] for r in group if isinstance(r["request_count"], (int, float))]
        tested = [r for r in group if r["test_gap"] is not None]
        cost_by_strategy[strategy] = {
            "planned_jobs": len(group), "tested_jobs": len(tested),
            "known_token_total_including_partial": sum(known),
            "known_tokens_mean_per_tested_job": mean([r["known_tokens"] for r in tested if r["known_tokens"] is not None]),
            "request_count_total_known": sum(requests),
            "request_count_mean_per_tested_job": mean([r["request_count"] for r in tested if r["request_count"] is not None]),
            "unknown_cost_jobs": [r["job_id"] for r in group if r["status"] != "continuation_complete"],
        }

    return {
        "source_protocol": "protocol.short_horizon.draft.json; archived manifest binds final protocol digest",
        "status_counts_recomputed": dict(statuses),
        "planned_jobs": len(manifest["jobs"]),
        "jobs_with_test": sum(r["test_gap"] is not None for r in rows.values()),
        "sent_unknown_jobs": [r["job_id"] for r in rows.values() if r["status"] != "continuation_complete"],
        "task_level_gap_means_vs_analysis": current_report_comparison,
        "baseline_test_recomputation": baseline_recomputation,
        "cost_by_strategy": cost_by_strategy,
        "pairing": {"C-B_minus_C-I": c_b, "C-E_minus_C-I": c_e,
                    "missing_test_pairs": missing_pairs},
        "C_E_starting_quality": {
            "count": len(exploration_rows),
            "valid_count": sum(r["valid_start"] for r in exploration_rows),
            "all_valid_starts_worse_than_incumbent": all(x > 0 for x in start_gaps),
            "start_minus_incumbent_validation_gap_pp": compact_stats(start_gaps),
            "by_block": {str(block): compact_stats([r["start_minus_incumbent_pp"] for r in exploration_rows
                                                     if r["block"] == block and r["start_minus_incumbent_pp"] is not None])
                         for block in sorted({r["block"] for r in exploration_rows})},
        },
        "C_E_progress_translation": {
            "completed_test_jobs": len(completed_ce),
            "jobs_with_local_project_progress": sum((r["project_progress"] or 0) > 0 for r in completed_ce),
            "local_project_progress_events_reported": sum(r["project_progress"] or 0 for r in completed_ce),
            "local_project_progress_events_recomputed": sum(t["recomputed_project_progress"] for t in ce_run_trajectories.values()),
            "global_improvement_events_reported": sum(r["global_improvements"] or 0 for r in completed_ce),
            "global_improvement_events_recomputed": sum(t["recomputed_global_improvements"] for t in ce_run_trajectories.values()),
            "jobs_with_global_improvement": sum((r["global_improvements"] or 0) > 0 for r in completed_ce),
            "test_positive_jobs": len(positive_ce),
            "test_positive_blocks": sorted({r["block"] for r in positive_ce}),
            "test_improvement_pp": compact_stats([r["test_improvement"] * 100 for r in completed_ce]),
            "positive_test_rows": [{"job_id": r["job_id"], "block": r["block"], "phase": r["phase"],
                                    "repetition": r["repetition"],
                                    "baseline_test_gap": r["baseline_test_gap"],
                                    "final_test_gap": r["test_gap"],
                                    "improvement_pp": r["test_improvement"] * 100}
                                   for r in positive_ce],
            "run_recomputation": ce_run_trajectories,
        },
        "rows": [{k: v for k, v in r.items() if k != "checkpoint"} for r in rows.values()],
        "exploration_starts": exploration_rows,
    }


def history_summary():
    e2 = read_json(CV / "results/e2-minimax-20260928-r3-analysis/E2_R3_ANALYSIS.json")
    offline = read_json(CV / "results/offline-audit-e1-e2-20260929.json")
    s3_path = ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/results/s3-minimax-strategy-20260927/S3_ANALYSIS.json"
    s3 = read_json(s3_path)
    s3_protocol_path = ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/protocol.final.json"
    s3_protocol = read_json(s3_protocol_path)
    e1_manifest_path = CV / "studies/e1-minimax-20260928-g-frozen/manifest.json"
    e1_manifest = read_json(e1_manifest_path)
    e1_raw_audit = audit_e1_cb(CV / "studies/e1-minimax-20260928-g-frozen")
    e2_raw_audit = audit_e2_events(CV / "studies/e2-minimax-20260928-r3-frozen")
    def compact_failed_batch(path):
        analysis = read_json(path)
        return {
            "study_id": analysis["study_id"],
            "planned_jobs": analysis["jobs_planned"],
            "tested_jobs": analysis["jobs_tested"],
            "all_jobs_infrastructure_failed": analysis["all_jobs_infrastructure_failed"],
            "group_outcomes": {name: {
                "status_counts": group["status_counts"],
                "completed_proposals": group["completed_proposals"],
                "provider_failures": group["provider_failures"],
                "known_tokens": group["known_tokens"],
            } for name, group in analysis["group"].items()},
            "interpretation": analysis["interpretation"],
        }
    e1_status = Counter()
    for job in e1_manifest["jobs"]:
        run_status = CV / "studies/e1-minimax-20260928-g-frozen/runs" / job["job_id"] / "status.json"
        if run_status.exists():
            e1_status[read_json(run_status).get("status", "unknown")] += 1
        else:
            e1_status["missing_status"] += 1
    contrasts = s3["contrasts"]
    return {
        "E1": {
            "protocol": "same-state continuation: C-I, C-B, C-E; blocks 44-47; 4 proposals/job; two source arms × checkpoints × repetitions",
            "study_id": e1_manifest["study_id"],
            "planned_jobs": len(e1_manifest["jobs"]),
            "observed_run_status_counts": dict(e1_status),
            "C_B_validation_to_test_audit": {
                **{k: v for k, v in e1_raw_audit.items() if k != "rows"},
                "matches_prior_offline_audit": (
                    e1_raw_audit["C_B_jobs_with_test"] == offline["e1_cb"]["jobs"]
                    and e1_raw_audit["validation_improved_jobs"] == offline["e1_cb"]["validation_improved_jobs"]
                    and e1_raw_audit["validation_improved_but_test_not_better"] == offline["e1_cb"]["validation_improved_but_test_worse_jobs"]
                ),
                "rows": e1_raw_audit["rows"],
                "unit_note": "descriptive jobs nested in 4 blocks and two source arms; not independent samples",
            },
            "evidence_boundary": "pilot; cannot identify a general branch-development benefit; validation progress did not reliably transfer to Test",
            "manifest_sha256": e1_manifest.get("manifest_sha256"),
        },
        "E2": {
            "v1": compact_failed_batch(CV / "results/e2-v1/ANALYSIS.json"),
            "r2": compact_failed_batch(CV / "results/e2-r2/ANALYSIS.json"),
            "r3": {
                "planned_jobs": e2["jobs_planned"], "tested_jobs": e2["jobs_tested"],
                "blocks": 4, "repetitions_per_group_block": 2,
                "P11_minus_P00_pp": e2["pairwise_test_gap_contrasts"]["P11_minus_P00"]["difference_pp"],
                "P_S_scheduling_priority_pp": {k: (v * 100 if k != "n_blocks" else v) for k, v in e2["factor_effects"]["P_S_scheduling_priority"]["difference"].items()},
                "P_E_eviction_protection_pp": {k: (v * 100 if k != "n_blocks" else v) for k, v in e2["factor_effects"]["P_E_eviction_protection"]["difference"].items()},
                "interpretation_limits": e2["interpretation_limits"],
                "manifest_sha256": e2["manifest_sha256"],
            },
            "offline_mechanism_audit": {
                **e2_raw_audit,
                "matches_prior_offline_audit": (
                    e2_raw_audit["runs"] == offline["e2"]["runs"]
                    and e2_raw_audit["records"] == offline["e2"]["records"]
                    and e2_raw_audit["branch_development_events"] == offline["e2"]["branch_development"]
                    and e2_raw_audit["parent_improvement_events"] == offline["e2"]["parent_improvements"]
                ),
                "known_narrative_discrepancy": "prior narrative said 112 branch-development events; frozen run records count 113",
            },
            "evidence_boundary": "r3 is a 4-block component screen; r1 and r2 stopped on provider failures before usable quality estimates",
        },
        "S3": {
            "study_id": s3["study_id"], "protocol_sha256": s3["protocol_sha256"],
            "blocks": s3_protocol["blocks"], "arms": [x["arm_id"] for x in s3_protocol["arms"]],
            "planned_jobs": s3["jobs_planned"], "tested_jobs": s3["jobs_tested"],
            "groups": {name: {"mean_test_gap": data["mean_test_gap"],
                               "mean_tokens": data["mean_tokens"],
                               "protected_local_improvements": data["protected_local_improvements"],
                               "protected_global_improvements": data["protected_global_improvements"],
                               "protected_slots_scheduled": data["protected_slots_scheduled"]}
                       for name, data in s3["group"].items()},
            "FB_P_minus_FB_U_pp": contrasts["FB_P_minus_FB_U"]["paired_difference_pp"],
            "TS_P_minus_FB_P_pp": contrasts["TS_P_minus_FB_P"]["paired_difference_pp"],
            "AD_P_minus_FB_P_pp": contrasts["AD_P_minus_FB_P"]["paired_difference_pp"],
            "evidence_boundary": "one model, one seed, eight TSP14 blocks; screening evidence, not confirmation or cross-task validation",
        },
        "protocol_and_source_hashes": {
            "e1_g_manifest": sha256(e1_manifest_path.read_bytes()),
            "s3_r3_protocol": sha256(s3_protocol_path.read_bytes()),
            "s3_r3_analysis": sha256(s3_path.read_bytes()),
            "e2_r3_protocol": sha256((CV / "protocol.r3.final.json").read_bytes()),
            "e2_r3_analysis": sha256((CV / "results/e2-minimax-20260928-r3-analysis/E2_R3_ANALYSIS.json").read_bytes()),
        },
    }


def build_demo(archive: zipfile.ZipFile, short: dict, archive_sha: str):
    job_id = "b59-early-ce-r0"
    trajectory = short["C_E_progress_translation"]["run_recomputation"][job_id]
    row = next(x for x in short["rows"] if x["job_id"] == job_id)
    start_row = next(x for x in short["exploration_starts"] if x["job_id"] == job_id)
    baseline_test = row["baseline_test_gap"]
    final_test = row["test_gap"]
    return {
        "schema": "chapter6-real-trajectory-demo-v1",
        "record_type": "real_archived_trajectory",
        "synthetic": False,
        "source": {
            "git_commit": "6d491ac6391ccc2c7a00baa5e695fbe66e66f16a",
            "study_id": "chapter6-component-validation-short-horizon-20260929",
            "raw_archive": "experiments/chapter6/agent_search/component_validation/results/short-horizon-20260929/raw-study.zip",
            "raw_archive_sha256": archive_sha,
            "job_id": job_id,
            "checkpoint": "study/checkpoints/b59-early.json",
            "run_prefix": f"study/runs/{job_id}",
            "test_record": f"study/tests/{job_id}.json",
        },
        "data_boundary": {
            "search_selection_split": "validation",
            "test_readout": "after all study jobs were terminal; test did not select the final candidate",
            "test_is_not_a_new_confirmation_observation": True,
        },
        "initial_comparison": {
            "incumbent_validation_loss": row["checkpoint_incumbent_validation_loss"],
            "exploration_start_validation_loss": start_row["start_loss"],
            "exploration_start_minus_incumbent_percentage_points": start_row["start_minus_incumbent_pp"],
        },
        "trajectory": trajectory["steps"],
        "accounting": {
            "reported_project_progress": trajectory["reported_project_progress"],
            "recomputed_project_progress": trajectory["recomputed_project_progress"],
            "reported_global_improvements": trajectory["reported_global_improvements"],
            "recomputed_global_improvements": trajectory["recomputed_global_improvements"],
            "known_tokens": row["known_tokens"], "request_count": row["request_count"],
        },
        "final_validation_selection_id": zip_json(archive, f"study/runs/{job_id}/selection_frozen.json")["best_id"],
        "test_outcome": {
            "incumbent_test_gap": baseline_test,
            "selected_test_gap": final_test,
            "improvement_percentage_points": (baseline_test - final_test) * 100,
            "interpretation": "One realized trajectory in the previously used screening archive; descriptive only, not a method confirmation.",
        },
    }


def generate(output_dir: Path):
    analysis = read_json(RESULTS / "ANALYSIS.json")
    with zipfile.ZipFile(ZIP_PATH) as archive:
        archive_result = archive_audit(archive, analysis)
        short = read_short_horizon(archive, analysis)
        demo = build_demo(archive, short, archive_result["raw_zip"]["sha256"])
    history = history_summary()
    code_paths = [
        CV / "protocol.short_horizon.draft.json",
        CV / "analyze_short_horizon.py",
        CV / "study_short_horizon.py",
        CV / "controller.py",
        CV / "evaluator.py",
        Path(__file__),
    ]
    source_files = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()) for p in code_paths}
    result = {
        "schema": "chapter6-history-audit-v1",
        "baseline_commit": "6d491ac6391ccc2c7a00baa5e695fbe66e66f16a",
        "working_branch_at_audit": "research/chapter6-minimal-mechanism-20260930",
        "scope": "read-only evidence reanalysis; no model calls; no historical archive changes; no counterfactual performance claims",
        "source_files_sha256": source_files,
        "archive_integrity": archive_result,
        "short_horizon": short,
        "historical_studies": history,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "HISTORY_AUDIT.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output_dir / "demo_real.json").write_text(
        json.dumps(demo, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    report = render_report(result)
    (output_dir / "HISTORY_AUDIT_ZH.md").write_text(report, encoding="utf-8")
    return result


def render_report(result):
    s = result["short_horizon"]
    integ = result["archive_integrity"]
    starts = s["C_E_starting_quality"]["start_minus_incumbent_validation_gap_pp"]
    lines = [
        "# 第六章历史证据独立复核",
        "",
        "本审计从冻结原始归档重算短程研究的任务级 Test gap、同检查点配对差、区块等权区间、探索起点验证差及进步事件。它不调用模型、不修改历史归档，也不把历史后代当作新方法的反事实表现。基准提交为 `6d491ac6391ccc2c7a00baa5e695fbe66e66f16a`。",
        "",
        "## 短程研究",
        "",
        f"96 个任务中，{s['jobs_with_test']} 个有 Test 结果；sent_unknown 任务保留为缺失：{', '.join(s['sent_unknown_jobs'])}。该任务不记为零收益，也不补跑。任务级差异与 `ANALYSIS.json` 对照如下：",
        "",
        "| 策略 | 重算 Test gap 均值 | 已报均值 | 差异 | n |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, vals in s["task_level_gap_means_vs_analysis"].items():
        lines.append(f"| {name} | {vals['recomputed_task_mean_gap']*100:.4f}% | {vals['reported_task_mean_gap']*100:.4f}% | {vals['absolute_difference']*100:+.6f} 个百分点 | {vals['task_count']} |")
    baseline_audit = s["baseline_test_recomputation"]
    lines.extend(["", f"另将冻结 incumbent 程序在对应 Test 快照上重新评价：{baseline_audit['reported_baselines_matched']}/{baseline_audit['recomputed_jobs']} 个基线与原分析在 1e-12 内一致；归档最终 Test 记录的 loss 与 primary_test_gap 一致={baseline_audit['all_final_test_records_match_reported_evaluation']}。", ""])
    lines.extend(["", "主要比较先在同一 block-phase-repetition 内配对，再在 block 内对观测到的配对取平均，最终以 8 个数据块等权；缺失值不插补。区间为区块差值的双侧 95% t 区间；C-E 的完整区块敏感性分析排除有缺失配对的 block 56。负值有利于分支/探索策略。", ""])
    lines.append("| 比较 | 任务配对均值 | 区块等权均值及 95% CI | 完整区块均值及 95% CI | block n |")
    lines.append("|---|---:|---:|---:|---:|")
    for label, key in (("C-B − C-I", "C-B_minus_C-I"), ("C-E − C-I", "C-E_minus_C-I")):
        d = s["pairing"][key]
        block = d["block_equal_difference_pp"]
        full = d["complete_blocks_only"]["difference_pp"]
        lines.append(f"| {label} | {d['task_weighted_difference_pp']:+.4f} pp | {block['mean']:+.4f} [{block['lower']:+.4f}, {block['upper']:+.4f}] | {full['mean']:+.4f} [{full['lower']:+.4f}, {full['upper']:+.4f}] | {block['n']} / {full['n']} |")
    lines.extend(["", f"C-E 缺失配对：{json.dumps(s['pairing']['missing_test_pairs'], ensure_ascii=False)}。项目级缺失块差异和每个配对都保存在 JSON。区间跨零意味着精度不足以辨别方向，不表示两策略等效。", "", "实际成本按已知用量列示；未知请求的成本没有估成零。", "", "| 策略 | 已知 token 总量 | 平均 token/有 Test 任务 | 已知请求总量 | 有 Test 任务 |", "|---|---:|---:|---:|---:|"])
    for name, cost in s["cost_by_strategy"].items():
        lines.append(f"| {name} | {cost['known_token_total_including_partial']:,} | {cost['known_tokens_mean_per_tested_job']:.1f} | {cost['request_count_total_known']:,} | {cost['tested_jobs']} |")
    lines.extend(["", "## 探索起点与收益转化", "", f"32 个 C-E 首次探索候选均可复核；其 validation loss 相对同检查点 incumbent 的平均差为 **{starts['mean']:.4f} 个百分点**（SD {starts['sd']:.4f}，范围 {starts['min']:.4f} 至 {starts['max']:.4f}），全部起点较差。这个诊断与“无父代探索起点质量不足”一致，但不是 G 有信息生成的比较。", ""])
    trans = s["C_E_progress_translation"]
    lines.append(f"31 个完整 C-E Test 任务记录 {trans['local_project_progress_events_reported']} 次项目进步，其中按冻结 incumbent 口径重算得到 {trans['global_improvement_events_recomputed']} 次全局进步（项目事件转为全局事件为 {100*trans['global_improvement_events_recomputed']/trans['local_project_progress_events_reported']:.1f}%）；{trans['test_positive_jobs']} 个任务的最终 Test gap 低于冻结 incumbent 的 Test gap，且全部来自 block 59。局部项目进步、全局 validation 进步和 Test 改善是不同终点；这些事件不能当成独立样本或机制因果证明。")
    lines.extend(["", "## 历史协议与负结果", ""])
    h = result["historical_studies"]
    e1 = h["E1"]
    lines.append(f"E1 同状态试验计划 {e1['planned_jobs']} 个任务；C-B 的离线复核显示 {e1['C_B_validation_to_test_audit']['validation_improved_jobs']} 个任务出现 validation 全局进步，其中 {e1['C_B_validation_to_test_audit']['validation_improved_but_test_not_better']} 个最终 Test 没有优于起点 incumbent。此试点只有 4 个数据块，不能支持普遍分支收益。")
    e2 = h["E2"]
    e2r3 = e2["r3"]
    p11 = e2r3["P11_minus_P00_pp"]
    ps = e2r3["P_S_scheduling_priority_pp"]
    pe = e2r3["P_E_eviction_protection_pp"]
    lines.append(f"E2 v1/r2 共两次基础设施失败批次（各 32 任务，无可估质量效应）；r3 在 4 个 block 上完成 32 任务。联合机制 P11−P00 为 {p11['mean']:+.4f} pp，95% CI [{p11['lower']:+.4f}, {p11['upper']:+.4f}]；排程优先级效应为 {ps['mean']:+.4f} pp，95% CI [{ps['lower']:+.4f}, {ps['upper']:+.4f}]；保护效应为 {pe['mean']:+.4f} pp，95% CI [{pe['lower']:+.4f}, {pe['upper']:+.4f}]。这些小样本筛查没有形成稳定的实用增量证据。离线轨迹审计发现 18 次分支局部进步均没有取得新额度；原摘要的分支开发次数 112 应更正为 113。")
    s3 = h["S3"]
    fb = s3["FB_P_minus_FB_U_pp"]
    lines.append(f"S3 r3 为 8 个 TSP14 block、6 个策略、48 个完整测试任务的筛查。固定保护 FB_P−FB_U 的平均 Test gap 差为 {fb['mean']:+.4f} pp，95% CI [{fb['lower']:+.4f}, {fb['upper']:+.4f}]，没有达到 0.3 pp 的筛查收益门槛；受保护方向发生局部和全局进步，不能单独说明其机会成本值得。更早的 S3 r1/r2 停止或线程隔离失败批次不用于质量结论。")
    source_checks = integ["source_file_integrity"]
    protocol = integ["frozen_manifest"]
    lines.extend(["", "## 归档复核", "", f"原始 ZIP 为 {integ['raw_zip']['member_count']} 个成员，SHA-256 `{integ['raw_zip']['sha256']}`。归档索引记录 {integ['archive_index']['record_count']} 个文件，逐项字节数与 SHA-256 { '全部匹配' if integ['archive_index']['index_matches_zip_members'] else '存在不匹配' }；冻结 study 清单记录 {integ['archive_audit']['records']} 个文件，逐项校验 { '全部匹配' if integ['archive_audit']['matches_zip_hashes_and_sizes'] else '存在不匹配' }。manifest canonical SHA-256 为 `{integ['frozen_manifest']['recomputed_canonical_sha256']}`，与内部值和 ANALYSIS 引用 { '相符' if integ['frozen_manifest']['canonical_hash_matches'] and integ['analysis_manifest_hash_matches'] else '不符' }。冻结 manifest 声明的实验实现源有 {source_checks['tooling_source']['matching_file_count']}/{source_checks['tooling_source']['declared_file_count']} 个本地文件摘要匹配；原始任务输入源有 {source_checks['source']['matching_file_count']}/{source_checks['source']['declared_file_count']} 个摘要匹配。运行来源提交是 `{protocol['source_commit']}`，而审计基准为 `6d491ac`；文件级摘要仍可逐项验证。另一个文档状态差异是 manifest 指向的 `{protocol['protocol_path']}` 文件自标 `DRAFT_OFFLINE_ONLY`，但 manifest 为 `FROZEN_PENDING_EXECUTION` 并绑定其 SHA-256 `{protocol['protocol_sha256']}`；报告保留该差异供复核。数据重叠检查报告 {protocol['data_overlap_audit']['old_instances_checked']} 个旧实例和 {protocol['data_overlap_audit']['new_instances']} 个新实例间无 ID 或精确坐标重叠，但未检验几何等价。", "", "真实轨迹演示 `demo_real.json` 取自 block 59 的 b59-early-ce-r0。它是历史记录的展示，不是合成机制证明，也不是新的确认样本。", "", "复现命令：`python experiments/chapter6/agent_search/minimal_mechanism/audit_history.py --output-dir experiments/chapter6/agent_search/minimal_mechanism`。"])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = generate(args.output_dir.resolve())
    print(json.dumps({"status": "complete", "output_dir": str(args.output_dir.resolve()),
                      "archive_integrity": result["archive_integrity"]["archive_index"]["index_matches_zip_members"]
                      and result["archive_integrity"]["archive_audit"]["matches_zip_hashes_and_sizes"],
                      "block_equal_contrasts_pp": {
                          key: val["block_equal_difference_pp"]["mean"]
                          for key, val in result["short_horizon"]["pairing"].items()
                          if key != "missing_test_pairs"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
