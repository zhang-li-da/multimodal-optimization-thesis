"""Offline E2 r3 readout.

This module only reads a frozen study.  It computes block-level factorial
effects for scheduling priority (P-S) and eviction protection (P-E), together
with decision and opportunity-redemption diagnostics.  It never calls a
provider and keeps incomplete jobs visible.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import statistics

from chapter6_demo.v12_2.common import read_json, save_json


ARMS = ("P00", "P10", "P01", "P11")
MECHANISM_FIELDS = (
    "completed_proposals", "valid_generated", "new_direction_candidates",
    "competitive_new_direction_candidates", "branch_entries_created",
    "zero_credit_pool_entries", "pool_evictions", "protection_grants_awarded",
    "protected_slots_scheduled", "branch_slots_scheduled",
    "ordinary_branch_slots_scheduled", "protected_slots_with_complete_output",
    "protected_slots_with_valid_program", "protected_local_improvements",
    "protected_direction_improvements", "protected_global_improvements",
    "protected_slots_on_lagging_parent", "multi_branch_protection_slots",
    "protected_best_ancestor",
)


def _mean(values):
    values = [float(value) for value in values if isinstance(value, (int, float))]
    return statistics.fmean(values) if values else None


def _bootstrap(values, seed, reps=20000):
    values = [float(value) for value in values if value is not None]
    if not values:
        return {"mean": None, "lower": None, "upper": None, "n_blocks": 0}
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        means.append(statistics.fmean(values[rng.randrange(len(values))]
                                     for _ in values))
    means.sort()
    lo = means[max(0, int(reps * .025))]
    hi = means[min(len(means) - 1, int(reps * .975))]
    return {"mean": statistics.fmean(values), "lower": lo, "upper": hi,
            "n_blocks": len(values)}


def _status(run_dir):
    path = Path(run_dir) / "status.json"
    return read_json(path) if path.exists() else {"status": "not_started"}


def _events(run_dir):
    checkpoint = Path(run_dir) / "checkpoint.json"
    if not checkpoint.exists():
        return []
    return (read_json(checkpoint).get("state") or {}).get("events") or []


def _decisions(run_dir):
    result = []
    slots = Path(run_dir) / "slots"
    if not slots.exists():
        return result
    for directory in sorted(slots.iterdir(), key=lambda item: int(item.name)):
        path = directory / "decision.json"
        if not path.exists():
            continue
        raw = read_json(path)
        result.append(raw.get("decision", raw))
    return result


def _fallback_summary(run_dir, status):
    events = _events(run_dir)
    usage = status.get("usage") or {}
    summary = {
        "completed_proposals": len(events),
        "valid_generated": sum(bool(e.get("valid")) for e in events),
        "model_request_attempts": usage.get("call_attempts", 0),
        "known_tokens": usage.get("known_tokens", 0),
        "total_tokens": usage.get("total_tokens"),
        "usage_complete": usage.get("usage_complete", False),
    }
    for name, predicate in {
        "new_direction_candidates": lambda e: e.get("new_direction"),
        "competitive_new_direction_candidates": lambda e: e.get("new_direction") and e.get("competitive"),
        "branch_entries_created": lambda e: e.get("branch_entry_created"),
        "pool_evictions": lambda e: e.get("pool_evicted_direction_id") is not None,
        "protected_slots_scheduled": lambda e: e.get("protected_development"),
        "protected_slots_with_valid_program": lambda e: e.get("protected_development") and e.get("valid"),
        "protected_local_improvements": lambda e: e.get("protected_development") and e.get("local_improvement"),
        "protected_global_improvements": lambda e: e.get("protected_development") and e.get("global_improvement"),
        "protected_slots_on_lagging_parent": lambda e: e.get("protected_development") and e.get("protected_parent_was_behind_global"),
        "multi_branch_protection_slots": lambda e: e.get("multi_branch_protection_slot"),
        "zero_credit_pool_entries": lambda e: e.get("zero_credit_pool_entry"),
    }.items():
        summary[name] = sum(bool(predicate(e)) for e in events)
    return summary


def _row(study, job):
    run_dir = Path(study) / "runs" / job["job_id"]
    status = _status(run_dir)
    result = read_json(run_dir / "search_result.json") if (run_dir / "search_result.json").exists() else {}
    summary = result.get("summary") or status.get("summary") or _fallback_summary(run_dir, status)
    test_path = Path(study) / "tests" / f"{job['job_id']}.json"
    test = read_json(test_path) if test_path.exists() else {}
    return {
        "job": job,
        "status": status.get("status", "missing"),
        "summary": summary,
        "test": test,
        "test_gap": test.get("primary_test_gap"),
        "decisions": _decisions(run_dir),
    }


def _decision_metrics(rows):
    actions = Counter()
    slots = Counter()
    chosen_directions = []
    available_choice_counts = []
    priority_choices = 0
    multiple_choices = 0
    multi_branch_choices = 0
    protected_choices = 0
    lagging_choices = 0
    evicted_steps = 0
    for row in rows:
        for decision in row["decisions"]:
            allocation = decision.get("allocation") or {}
            evidence = decision.get("evidence") or {}
            actions[decision.get("action", "unknown")] += 1
            slots[allocation.get("slot_type", evidence.get("slot_type", "unknown"))] += 1
            selected = allocation.get("direction_id")
            if selected is not None:
                chosen_directions.append(selected)
            available = evidence.get("available_direction_ids") or []
            available_choice_counts.append(len(available))
            if len(available) >= 2:
                multiple_choices += 1
            if evidence.get("multi_branch_protection_slot"):
                multi_branch_choices += 1
            if allocation.get("protected"):
                protected_choices += 1
            priority = evidence.get("priority_direction_ids") or []
            if selected is not None and priority and selected == priority[0]:
                priority_choices += 1
            parent = decision.get("parent") or {}
            if parent.get("loss") is not None:
                # The exact lagging-parent flag is recorded in event summaries;
                # this counter remains a diagnostic for available parent loss.
                lagging_choices += int(bool(allocation.get("protected")))
            # Eviction is an observation-side event and is counted from the
            # run summary; decision records do not contain that field.
    switches = sum(a != b for a, b in zip(chosen_directions, chosen_directions[1:]))
    return {
        "decision_count": sum(actions.values()),
        "action_counts": dict(sorted(actions.items())),
        "slot_type_counts": dict(sorted(slots.items())),
        "selected_direction_count": len(chosen_directions),
        "unique_selected_directions": len(set(chosen_directions)),
        "direction_switches": switches,
        "mean_available_direction_count": _mean(available_choice_counts),
        "steps_with_multiple_investment_choices": multiple_choices,
        "steps_with_multiple_protected_choices": multi_branch_choices,
        "protected_decisions": protected_choices,
        "priority_selected_decisions": priority_choices,
        "lagging_protected_decisions_diagnostic": lagging_choices,
        "eviction_steps_from_decisions": evicted_steps,
    }


def _sum_field(rows, field):
    return sum((row["summary"].get(field, 0) or 0) for row in rows)


def _group_report(rows):
    tested = [row for row in rows if row["test_gap"] is not None]
    report = {
        "planned_jobs": len(rows),
        "tested_jobs": len(tested),
        "completed_searches": sum(row["status"] == "search_complete_test_not_run" for row in rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "mean_test_gap": _mean([row["test_gap"] for row in tested]),
        "median_test_gap": statistics.median([row["test_gap"] for row in tested]) if tested else None,
        "mean_known_tokens": _mean([row["summary"].get("known_tokens") for row in rows]),
        "mean_total_tokens": _mean([row["summary"].get("total_tokens") for row in rows]),
        "usage_complete_jobs": sum(bool(row["summary"].get("usage_complete")) for row in rows),
        "total_requests": _sum_field(rows, "model_request_attempts"),
        "decision_metrics": _decision_metrics(rows),
    }
    for field in MECHANISM_FIELDS:
        report[field] = _sum_field(rows, field)
    proposals = report["completed_proposals"]
    for field in ("valid_generated", "branch_entries_created", "protected_slots_scheduled",
                  "protected_slots_with_complete_output", "protected_slots_with_valid_program",
                  "protected_local_improvements", "protected_global_improvements"):
        report[f"{field}_rate_per_proposal"] = report[field] / proposals if proposals else None
    return report


def _arm_mean(by_key, block, arm, key):
    rows = by_key.get((block, arm), [])
    values = []
    for row in rows:
        value = row["test_gap"] if key == "test_gap" else row["summary"].get(key)
        if value is not None:
            values.append(float(value))
    return {"value": _mean(values), "n": len(values)}


def _paired_effect(blocks, by_key, on_arms, off_arms, key, seed):
    pairs = []
    for block in blocks:
        on = [_arm_mean(by_key, block, arm, key)["value"] for arm in on_arms]
        off = [_arm_mean(by_key, block, arm, key)["value"] for arm in off_arms]
        on = [value for value in on if value is not None]
        off = [value for value in off if value is not None]
        if on and off:
            pairs.append({"block": block, "on_mean": statistics.fmean(on),
                          "off_mean": statistics.fmean(off),
                          "difference": statistics.fmean(on) - statistics.fmean(off),
                          "on_arms": list(on_arms), "off_arms": list(off_arms)})
    difference = _bootstrap([pair["difference"] for pair in pairs], seed)
    return {"pairs": pairs, "difference": difference,
            "negative_favors_on": key == "test_gap"}


def _interaction(blocks, by_key, key, seed):
    values = []
    for block in blocks:
        means = {arm: _arm_mean(by_key, block, arm, key)["value"] for arm in ARMS}
        if all(value is not None for value in means.values()):
            value = means["P11"] - means["P10"] - means["P01"] + means["P00"]
            values.append({"block": block, "P00": means["P00"], "P10": means["P10"],
                           "P01": means["P01"], "P11": means["P11"], "difference": value})
    return {"pairs": values, "difference": _bootstrap([x["difference"] for x in values], seed),
            "definition": "P11 - P10 - P01 + P00; outcome is test gap when key=test_gap"}


def analyze(study, output):
    study = Path(study)
    manifest = read_json(study / "manifest.json")
    rows = [_row(study, job) for job in manifest["jobs"]]
    blocks = manifest["protocol"]["e2"]["blocks"]
    by_key = {}
    for row in rows:
        by_key.setdefault((row["job"]["data_block"], row["job"]["arm_id"]), []).append(row)
    groups = {arm: _group_report([row for row in rows if row["job"]["arm_id"] == arm]) for arm in ARMS}
    seed = 20260928
    factor_effects = {
        "P_S_scheduling_priority": _paired_effect(blocks, by_key, ("P10", "P11"), ("P00", "P01"), "test_gap", seed),
        "P_E_eviction_protection": _paired_effect(blocks, by_key, ("P01", "P11"), ("P00", "P10"), "test_gap", seed + 1),
        "P_S_token_cost": _paired_effect(blocks, by_key, ("P10", "P11"), ("P00", "P01"), "known_tokens", seed + 2),
        "P_E_token_cost": _paired_effect(blocks, by_key, ("P01", "P11"), ("P00", "P10"), "known_tokens", seed + 3),
        "P_S_by_P_E_interaction": _interaction(blocks, by_key, "test_gap", seed + 4),
    }
    pairwise = {}
    for left, right in (("P10", "P00"), ("P01", "P00"), ("P11", "P00"), ("P11", "P10"), ("P11", "P01")):
        pairs = []
        for block in blocks:
            l, r = _arm_mean(by_key, block, left, "test_gap")["value"], _arm_mean(by_key, block, right, "test_gap")["value"]
            if l is not None and r is not None:
                pairs.append({"block": block, "left": left, "right": right,
                              "difference_pp": 100 * (l - r)})
        pairwise[f"{left}_minus_{right}"] = {
            "pairs": pairs,
            "difference_pp": _bootstrap([p["difference_pp"] for p in pairs], seed + len(pairwise)),
            "definition": f"100 * ({left} test gap - {right} test gap); negative favors {left}",
        }
    block_results = []
    for block in blocks:
        arms = {}
        for arm in ARMS:
            rows_for_arm = by_key.get((block, arm), [])
            arms[arm] = {
                "n_jobs": len(rows_for_arm),
                "tested_jobs": sum(row["test_gap"] is not None for row in rows_for_arm),
                "mean_test_gap": _arm_mean(by_key, block, arm, "test_gap")["value"],
                "mean_known_tokens": _arm_mean(by_key, block, arm, "known_tokens")["value"],
            }
        block_results.append({"block": block, "arms": arms})
    threshold = float(manifest["protocol"].get("decision_rules", {}).get("minimum_practical_gain_percentage_points", 0.3))
    report = {
        "study_id": manifest.get("study_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "jobs_planned": len(rows),
        "jobs_tested": sum(row["test_gap"] is not None for row in rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "groups": groups,
        "factor_effects": factor_effects,
        "pairwise_test_gap_contrasts": pairwise,
        "block_results": block_results,
        "minimum_practical_gain_percentage_points": threshold,
        "independent_unit": "data block; seed is a within-block repeat",
        "model_calls_in_analysis": 0,
        "interpretation_limits": [
            "This is a single-model TSP14 component screen, not universal or cross-task validation.",
            "Test-gap effects resample data blocks; proposals, instances and model requests are not independent replicates.",
            "Missing and infrastructure-incomplete jobs remain visible and are not replaced.",
            "A negative test-gap contrast favors the factor-on condition; the descriptive intervals are not confirmatory p-values.",
        ],
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "E2_R3_ANALYSIS.json", report, immutable=False)
    lines = ["# E2 r3 离线分析", "", f"实验 `{report['study_id']}`：{report['jobs_tested']}/{report['jobs_planned']} 个任务已有独立 test。", "", "| 组 | test 任务 | 平均 Test gap | 已知 token | 分支开发 | 保护槽位 | 淘汰 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, group in groups.items():
        gap = "NA" if group["mean_test_gap"] is None else f"{group['mean_test_gap']:.4%}"
        lines.append(f"| {arm} | {group['tested_jobs']}/{group['planned_jobs']} | {gap} | {group['mean_known_tokens'] or 0:.0f} | {group['branch_entries_created']} | {group['protected_slots_scheduled']} | {group['pool_evictions']} |")
    lines += ["", "## 因子效果", "", "负值表示开启因子后 Test gap 更低；区间按数据块描述性重采样。", "", "| 因子 | 数据块 | 平均差值（百分点） | 区间 |", "|---|---:|---:|---:|"]
    for name in ("P_S_scheduling_priority", "P_E_eviction_protection", "P_S_by_P_E_interaction"):
        effect = factor_effects[name]["difference"]
        mean = "NA" if effect["mean"] is None else f"{100 * effect['mean'] if name != 'P_S_by_P_E_interaction' else 100 * effect['mean']:+.3f}"
        interval = "NA" if effect["lower"] is None else f"[{100 * effect['lower']:+.3f}, {100 * effect['upper']:+.3f}]"
        lines.append(f"| {name} | {effect['n_blocks']} | {mean} | {interval} |")
    lines += ["", "分析只读取归档，没有新增模型调用。完整机制计数、区块配对值和状态保存在 `E2_R3_ANALYSIS.json`。", ""]
    # Rewrite the human-readable table with UTF-8 literals; older generated
    # copies of this module contained mojibake in the report template.
    report_lines = [
        "# E2 r3 离线分析", "",
        f"实验 `{report['study_id']}`：{report['jobs_tested']}/{report['jobs_planned']} 个任务已完成独立 test。", "",
        "| 组别 | test 任务 | 平均 Test gap | 已知 token | 分支开发 | 保护槽位 | 淘汰 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, group in groups.items():
        gap = "NA" if group["mean_test_gap"] is None else f"{group['mean_test_gap']:.4%}"
        report_lines.append(
            f"| {arm} | {group['tested_jobs']}/{group['planned_jobs']} | {gap} | "
            f"{group['mean_known_tokens'] or 0:.0f} | {group['branch_entries_created']} | "
            f"{group['protected_slots_scheduled']} | {group['pool_evictions']} |"
        )
    report_lines += [
        "", "## 因子效应", "",
        "负值表示开启因素后 Test gap 更低；区间按数据块进行描述性重采样。", "",
        "| 因子 | 数据块 | 平均差值（百分点） | 区间 |", "|---|---:|---:|---:|",
    ]
    for name in ("P_S_scheduling_priority", "P_E_eviction_protection", "P_S_by_P_E_interaction"):
        effect = factor_effects[name]["difference"]
        mean = "NA" if effect["mean"] is None else f"{100 * effect['mean']:+.3f}"
        interval = "NA" if effect["lower"] is None else f"[{100 * effect['lower']:+.3f}, {100 * effect['upper']:+.3f}]"
        report_lines.append(f"| {name} | {effect['n_blocks']} | {mean} | {interval} |")
    report_lines += [
        "", "分析只读取归档，没有新增模型调用。完整机制计数、区块配对值和状态保存在 `E2_R3_ANALYSIS.json`。", "",
    ]
    (output / "REPORT_ZH.md").write_text("\n".join(report_lines), encoding="utf-8")
    # Keep the checked-in summary ASCII-only so it remains readable regardless
    # of the shell's code page; the JSON artifact retains all numeric detail.
    ascii_lines = [
        "# E2 r3 Offline Analysis", "",
        f"Study `{report['study_id']}`: {report['jobs_tested']}/{report['jobs_planned']} jobs have independent test results.", "",
        "| Arm | Tested | Mean test gap | Mean known tokens | Branch entries | Protected slots | Evictions |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, group in groups.items():
        gap = "NA" if group["mean_test_gap"] is None else f"{group['mean_test_gap']:.4%}"
        ascii_lines.append(f"| {arm} | {group['tested_jobs']}/{group['planned_jobs']} | {gap} | {group['mean_known_tokens'] or 0:.0f} | {group['branch_entries_created']} | {group['protected_slots_scheduled']} | {group['pool_evictions']} |")
    ascii_lines += ["", "## Factor effects", "", "Negative values favor the factor-on condition; intervals resample data blocks.", "", "| Factor | Blocks | Mean difference (pp) | Interval |", "|---|---:|---:|---:|"]
    for name in ("P_S_scheduling_priority", "P_E_eviction_protection", "P_S_by_P_E_interaction"):
        effect = factor_effects[name]["difference"]
        mean = "NA" if effect["mean"] is None else f"{100 * effect['mean']:+.3f}"
        interval = "NA" if effect["lower"] is None else f"[{100 * effect['lower']:+.3f}, {100 * effect['upper']:+.3f}]"
        ascii_lines.append(f"| {name} | {effect['n_blocks']} | {mean} | {interval} |")
    ascii_lines += ["", "This report reads the archive only and makes no model calls. Full mechanism counts and block pairs are in E2_R3_ANALYSIS.json.", ""]
    (output / "REPORT_ZH.md").write_text("\n".join(ascii_lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.study, args.output)
    print(json.dumps({"jobs": report["jobs_tested"], "planned": report["jobs_planned"],
                      "status_counts": report["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
