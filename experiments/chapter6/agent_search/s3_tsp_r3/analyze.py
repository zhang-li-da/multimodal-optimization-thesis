"""Block-paired readout for the frozen S3 TSP strategy screen."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import random
import statistics
import zipfile

from chapter6_demo.v12_2.calls import DurableCalls
from chapter6_demo.v12_2.common import read_json, save_json

from .study import verify


def bootstrap(values, seed, reps=20000, q=.025):
    if not values:
        return {"lower": None, "upper": None, "mean": None, "n_blocks": 0}
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(sample))
    means.sort()
    lo = min(len(means) - 1, max(0, int(q * len(means))))
    hi = min(len(means) - 1, max(0, int((1 - q) * len(means))))
    return {"lower": means[lo], "upper": means[hi],
            "mean": statistics.fmean(values), "n_blocks": len(values)}


def _rows(study, manifest):
    study = Path(study)
    rows = []
    for job in manifest["jobs"]:
        run_dir = study / "runs" / job["job_id"]
        status_path, result_path = run_dir / "status.json", run_dir / "search_result.json"
        test_path = study / "tests" / f"{job['job_id']}.json"
        status = read_json(status_path) if status_path.exists() else {"status": "not_started"}
        result = read_json(result_path) if result_path.exists() else {}
        test = read_json(test_path) if test_path.exists() else {}
        summary = result.get("summary", status.get("summary", {}))
        if not summary:
            summary = _partial_summary(run_dir, status)
        rows.append({"job": job, "status": status.get("status"),
                     "summary": summary, "test": test,
                     "test_gap": test.get("primary_test_gap")})
    return rows


def _partial_summary(run_dir, status):
    checkpoint_path = run_dir / "checkpoint.json"
    checkpoint = read_json(checkpoint_path) if checkpoint_path.exists() else {}
    config = checkpoint.get("config")
    if config is None and (run_dir / "config.json").exists():
        config = read_json(run_dir / "config.json")
    usage = status.get("usage")
    if usage is None and config is not None:
        usage = DurableCalls(run_dir, config, None).usage()
    usage = usage or {"call_attempts": 0, "known_tokens": 0,
                      "total_tokens": None, "usage_complete": False}
    state = checkpoint.get("state", {})
    events = state.get("events", [])
    complete_responses = truncations = 0
    for raw_path in (run_dir / "calls").glob("*/raw_response.json") if (run_dir / "calls").exists() else []:
        try:
            raw = read_json(raw_path)
            body = json.loads(base64.b64decode(raw["envelope"]["body_base64"], validate=True))
            choices = body.get("choices", [])
            finish = choices[0].get("finish_reason") if choices else None
            complete_responses += int(finish == "stop")
            truncations += int(str(finish or "").lower() in {"length", "max_tokens", "token_limit"})
        except (ValueError, KeyError, TypeError):
            continue
    return {
        "completed_proposals": len(events),
        "valid_generated": sum(event.get("valid", False) for event in events),
        "model_request_attempts": usage.get("call_attempts", 0),
        "request_count": usage.get("call_attempts", 0),
        "known_tokens": usage.get("known_tokens", 0),
        "total_tokens": usage.get("total_tokens"),
        "usage_complete": usage.get("usage_complete", False),
        "complete_model_responses": complete_responses,
        "truncated_model_responses": truncations,
        "new_direction_candidates": sum(event.get("new_direction", False) for event in events),
        "competitive_new_direction_candidates": sum(
            event.get("competitive", False) and event.get("new_direction", False) for event in events),
        "branch_entries_created": sum(event.get("branch_entry_created", False) for event in events),
        "pool_evictions": sum(event.get("pool_evicted_direction_id") is not None for event in events),
        "protection_grants_awarded": sum(event.get("protection_grant_awarded", 0) for event in events),
        "protected_slots_scheduled": sum(event.get("protected_development", False) for event in events),
        "multi_branch_protection_slots": sum(event.get("multi_branch_protection_slot", False) for event in events),
        "protected_slots_with_complete_output": sum(
            event.get("protected_development", False)
            and event.get("costs", {}).get("complete_responses", 0) >= 2 for event in events),
        "protected_slots_with_valid_program": sum(
            event.get("protected_development", False) and event.get("valid", False) for event in events),
        "protected_slots_on_lagging_parent": sum(
            event.get("protected_development", False)
            and event.get("protected_parent_was_behind_global", False) for event in events),
        "protected_local_improvements": sum(
            event.get("protected_development", False) and event.get("local_improvement", False) for event in events),
        "protected_global_improvements": sum(
            event.get("protected_development", False) and event.get("global_improvement", False) for event in events),
        "adaptive_units": state.get("adaptive_units", []),
    }


def analyze(study, output):
    manifest = verify(study, frozen=True)
    rows = _rows(study, manifest)
    blocks = manifest["protocol"]["blocks"]
    arms = [arm["arm_id"] for arm in manifest["protocol"]["arms"]]
    by_key = {(row["job"]["data_block"], row["job"]["arm_id"]): row for row in rows}
    group = {}
    for arm in arms:
        selected = [row for row in rows if row["job"]["arm_id"] == arm]
        completed = [row for row in selected if row["test"].get("primary_test_gap") is not None]
        summaries = [row["summary"] for row in selected]
        valid_count = sum(s.get("valid_generated", 0) for s in summaries)
        proposals = sum(s.get("completed_proposals", 0) for s in summaries)
        tests = [row["test"]["primary_test_gap"] for row in completed]
        group[arm] = {
            "planned_jobs": len(selected),
            "completed_searches": sum(row["status"] == "search_complete_test_not_run" for row in selected),
            "tested_jobs": len(completed),
            "status_counts": {status: sum(row["status"] == status for row in selected)
                              for status in sorted({row["status"] for row in selected})},
            "mean_test_gap": statistics.fmean(tests) if tests else None,
            "median_test_gap": statistics.median(tests) if tests else None,
            "mean_improvement_vs_shared_seed_pp": statistics.fmean(
                100 * (row["test"]["seed_test_gap"] - row["test"]["primary_test_gap"])
                for row in completed if row["test"].get("seed_test_gap") is not None) if completed else None,
            "mean_tokens": statistics.fmean(s.get("known_tokens", 0) for s in summaries) if summaries else None,
            "usage_complete_jobs": sum(s.get("usage_complete", False) for s in summaries),
            "total_requests": sum(s.get("request_count", 0) for s in summaries),
            "valid_proposals": valid_count,
            "proposal_slots": proposals,
            "proposal_validity_rate": valid_count / proposals if proposals else None,
            "truncated_model_responses": sum(s.get("truncated_model_responses", 0) for s in summaries),
            "new_direction_candidates": sum(s.get("new_direction_candidates", 0) for s in summaries),
            "competitive_new_direction_candidates": sum(s.get("competitive_new_direction_candidates", 0) for s in summaries),
            "branch_entries_created": sum(s.get("branch_entries_created", 0) for s in summaries),
            "pool_evictions": sum(s.get("pool_evictions", 0) for s in summaries),
            "protection_grants_awarded": sum(s.get("protection_grants_awarded", 0) for s in summaries),
            "protected_slots_scheduled": sum(s.get("protected_slots_scheduled", 0) for s in summaries),
            "multi_branch_protection_slots": sum(s.get("multi_branch_protection_slots", 0) for s in summaries),
            "protected_slots_with_complete_output": sum(s.get("protected_slots_with_complete_output", 0) for s in summaries),
            "protected_slots_with_valid_program": sum(s.get("protected_slots_with_valid_program", 0) for s in summaries),
            "protected_slots_on_lagging_parent": sum(s.get("protected_slots_on_lagging_parent", 0) for s in summaries),
            "protected_local_improvements": sum(s.get("protected_local_improvements", 0) for s in summaries),
            "protected_global_improvements": sum(s.get("protected_global_improvements", 0) for s in summaries),
            "adaptive_units": sum(len(s.get("adaptive_units", [])) for s in summaries),
        }

    contrast_specs = [
        ("FB_P_minus_FB_U", "FB_P", "FB_U"),
        ("TS_P_minus_FB_P", "TS_P", "FB_P"),
        ("AD_P_minus_FB_P", "AD_P", "FB_P"),
        ("SP_minus_FB_P", "SP", "FB_P"),
        ("WR_minus_FB_P", "WR", "FB_P"),
    ]
    contrasts = {}
    primary_contrasts = {"FB_P_minus_FB_U", "TS_P_minus_FB_P", "AD_P_minus_FB_P"}
    practical_gain = manifest["protocol"]["practical_thresholds"]["minimum_practical_gain_percentage_points"]
    max_token_increase = manifest["protocol"]["practical_thresholds"]["maximum_acceptable_token_increase_fraction"]
    for name, left, right in contrast_specs:
        pairs = []
        for block in blocks:
            a, b = by_key.get((block, left)), by_key.get((block, right))
            if (a and b and a["test"].get("primary_test_gap") is not None
                    and b["test"].get("primary_test_gap") is not None):
                pairs.append({"block": block,
                              "left_test_gap": a["test"]["primary_test_gap"],
                              "right_test_gap": b["test"]["primary_test_gap"],
                              "difference_percentage_points": 100 * (
                                  a["test"]["primary_test_gap"] - b["test"]["primary_test_gap"]),
                              "left_tokens": a["summary"].get("total_tokens"),
                              "right_tokens": b["summary"].get("total_tokens")})
        diffs = [pair["difference_percentage_points"] for pair in pairs]
        token_ratios = [pair["left_tokens"] / pair["right_tokens"] - 1
                        for pair in pairs if isinstance(pair["left_tokens"], (int, float))
                        and isinstance(pair["right_tokens"], (int, float)) and pair["right_tokens"] > 0]
        interval = bootstrap(
            diffs, manifest["protocol"]["statistics"]["bootstrap_seed"] + len(contrasts),
            manifest["protocol"]["statistics"]["bootstrap_replicates"], .025)
        screen_signal = (name in primary_contrasts and len(diffs) >= 6
                         and interval["mean"] <= -practical_gain
                         and interval["upper"] < 0
                         and len(token_ratios) == len(diffs)
                         and statistics.fmean(token_ratios) <= max_token_increase)
        contrasts[name] = {
            "definition": f"100 * ({left} test gap - {right} test gap); negative favors {left}",
            "pairs": pairs,
            "paired_difference_pp": interval,
            "mean_token_increase_fraction": statistics.fmean(token_ratios) if token_ratios else None,
            "primary_contrast": name in primary_contrasts,
            "screening_signal_met": bool(screen_signal),
            "wins_left": sum(x < 0 for x in diffs),
            "ties": sum(abs(x) < 1e-12 for x in diffs),
            "losses_left": sum(x > 0 for x in diffs),
        }

    result = {
        "study_id": manifest["study_id"],
        "protocol_sha256": manifest["manifest_sha256"],
        "jobs_planned": len(rows),
        "jobs_tested": sum(row["test"].get("primary_test_gap") is not None for row in rows),
        "status_counts": {status: sum(row["status"] == status for row in rows)
                          for status in sorted({row["status"] for row in rows})},
        "independent_unit": "data block",
        "group": group,
        "contrasts": contrasts,
        "block_results": [
            {"block": block, "arms": {arm: {
                "status": by_key[(block, arm)]["status"],
                "test_gap": by_key[(block, arm)]["test"].get("primary_test_gap"),
                "tokens": by_key[(block, arm)]["summary"].get("total_tokens"),
                "valid_proposals": by_key[(block, arm)]["summary"].get("valid_generated"),
            } for arm in arms if (block, arm) in by_key}}
            for block in blocks
        ],
        "interpretation_limits": [
            "This is a single-model TSP14 strategy screen, not a confirmatory multi-task study.",
            "Confidence intervals resample data blocks; proposal and instance counts are not independent search replicates.",
            "No p-values or post-hoc stopping are used. Missing jobs remain visible and are not replaced.",
            f"A prespecified screening signal requires at least 6 complete blocks, mean gain of at least {practical_gain:.1f} percentage points, a descriptive interval below zero, and no more than {max_token_increase:.0%} mean token increase.",
        ],
        "new_model_calls_in_analysis": 0,
    }
    save_json(Path(output) / "S3_ANALYSIS.json", result, immutable=False)
    return result


def write_report(report, path):
    lines = [
        "# S3 MiniMax M3 TSP 搜索策略实验报告",
        "",
        f"实验 `{report['study_id']}` 计划 {report['jobs_planned']} 个任务，完成 test 评价 {report['jobs_tested']} 个；数据块为独立重采样单位。",
        "",
        "本实验是单模型、TSP14 的固定预算策略筛查。下表的 test gap 越低越好；不报告确认性 p 值。",
        "",
        "| 组别 | 已测试/计划 | 平均 Test gap | 平均已知 tokens | 有效提案/槽位 | 保护槽位 | 落后分支槽位 | 保护局部改进 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for arm, row in report["group"].items():
        gap = "NA" if row["mean_test_gap"] is None else f"{row['mean_test_gap']:.4%}"
        tokens = "NA" if row["mean_tokens"] is None else f"{row['mean_tokens']:.0f}"
        lines.append(f"| {arm} | {row['tested_jobs']}/{row['planned_jobs']} | {gap} | {tokens} | "
                     f"{row['valid_proposals']}/{row['proposal_slots']} | "
                     f"{row['protected_slots_scheduled']} | {row['protected_slots_on_lagging_parent']} | "
                     f"{row['protected_local_improvements']} |")
    lines += ["", "## 配对区块差异", "",
              "负值表示差异定义中的左侧方法 test gap 较低。区间是按完整数据块重采样的 95% 描述性区间。", "",
              "| 对比 | 完整配对数 | 平均差 (百分点) | 95% 区间 | 左侧胜/平/负 |",
              "|---|---:|---:|---:|---:|"]
    for name, contrast in report["contrasts"].items():
        ci = contrast["paired_difference_pp"]
        avg = "NA" if ci["mean"] is None else f"{ci['mean']:+.3f}"
        interval = "NA" if ci["lower"] is None else f"[{ci['lower']:+.3f}, {ci['upper']:+.3f}]"
        lines.append(f"| {name} | {ci['n_blocks']} | {avg} | {interval} | "
                     f"{contrast['wins_left']}/{contrast['ties']}/{contrast['losses_left']} |")
    lines += ["", "## 解释边界", ""]
    lines.extend(f"- {item}" for item in report["interpretation_limits"])
    lines += ["", "每个区块的各组结果、运行状态和成本均保留在 `S3_ANALYSIS.json` 与原始归档中。", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def audit_study(study, output):
    study = Path(study)
    files = []
    for source in sorted(study.rglob("*")):
        if source.is_file() and source.name != ".run.lock":
            files.append({"path": source.relative_to(study).as_posix(),
                          "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                          "bytes": source.stat().st_size})
    audit = {"study_id": read_json(study / "manifest.json")["study_id"],
             "file_count": len(files), "files": files,
             "new_model_calls_in_audit": 0}
    save_json(Path(output) / "S3_AUDIT.json", audit, immutable=False)
    return audit


def package(study, output):
    study, output = Path(study), Path(output)
    archive = output / "raw-study.zip"
    index = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source in sorted(study.rglob("*")):
            if source.is_file() and source.name != ".run.lock":
                rel = f"study/{source.relative_to(study).as_posix()}"
                zf.write(source, rel)
                index.append({"path": rel, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                              "bytes": source.stat().st_size})
        for name in ("S3_ANALYSIS.json", "REPORT_ZH.md", "S3_AUDIT.json"):
            source = output / name
            zf.write(source, name)
            index.append({"path": name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                          "bytes": source.stat().st_size})
    save_json(output / "ARCHIVE_INDEX.json", index, immutable=False)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = analyze(args.study, args.output)
    write_report(report, args.output / "REPORT_ZH.md")
    audit_study(args.study, args.output)
    package(args.study, args.output)
    print(json.dumps({"jobs": report["jobs_tested"], "planned": report["jobs_planned"],
                      "groups": report["group"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
