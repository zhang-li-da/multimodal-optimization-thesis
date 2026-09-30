"""Offline readout for E2, including explicit infrastructure failures."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import zipfile

from chapter6_demo.v12_2.common import digest, read_json, save_json


def _summary(run_dir):
    status_path = run_dir / "status.json"
    result_path = run_dir / "search_result.json"
    status = read_json(status_path) if status_path.exists() else {"status": "missing"}
    result = read_json(result_path) if result_path.exists() else {}
    summary = result.get("summary") or status.get("summary") or {}
    usage = result.get("usage") or status.get("usage") or {}
    return {"status": status.get("status", "missing"), "summary": summary,
            "usage": usage, "test": None}


def read_study(study):
    study = Path(study); manifest = read_json(study / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        run = _summary(study / "runs" / job["job_id"])
        test_path = study / "tests" / f"{job['job_id']}.json"
        run["test"] = read_json(test_path) if test_path.exists() else None
        rows.append({"job": job, **run})
    return manifest, rows


def _mean(values):
    return statistics.fmean(values) if values else None


def analyze(study, output):
    manifest, rows = read_study(study)
    groups = {}
    for row in rows:
        arm = row["job"]["arm_id"]
        groups.setdefault(arm, []).append(row)
    group_report = {}
    for arm, selected in sorted(groups.items()):
        summaries = [r["summary"] for r in selected]
        tested = [r["test"]["primary_test_gap"] for r in selected if r["test"] and r["test"].get("primary_test_gap") is not None]
        group_report[arm] = {
            "planned_jobs": len(selected),
            "status_counts": {s: sum(r["status"] == s for r in selected) for s in sorted({r["status"] for r in selected})},
            "tested_jobs": len(tested), "mean_test_gap": _mean(tested),
            "completed_proposals": sum(s.get("completed_proposals", 0) for s in summaries),
            "valid_proposals": sum(s.get("valid_generated", 0) for s in summaries),
            "request_attempts": sum((r["usage"] or {}).get("call_attempts", 0) for r in selected),
            "known_tokens": sum((r["usage"] or {}).get("known_tokens", 0) for r in selected),
            "provider_failures": sum(r["status"] == "infrastructure_incomplete" for r in selected),
            "branch_entries": sum(s.get("branch_entries_created", 0) for s in summaries),
            "zero_credit_pool_entries": sum(s.get("zero_credit_pool_entries", 0) for s in summaries),
            "evictions": sum(s.get("pool_evictions", 0) for s in summaries),
            "protected_slots": sum(s.get("protected_slots_scheduled", 0) for s in summaries),
        }
    all_failed = all(r["status"] == "infrastructure_incomplete" for r in rows)
    report = {
        "study_id": manifest.get("study_id"), "manifest_sha256": manifest.get("manifest_sha256"),
        "jobs_planned": len(rows), "jobs_tested": sum(bool(r["test"]) for r in rows),
        "all_jobs_infrastructure_failed": all_failed, "group": group_report,
        "model_calls_in_analysis": 0,
        "interpretation": (
            "No quality or component-effect conclusion is estimable because every job terminated on provider failure before a complete search."
            if all_failed else
            "At least one job produced a terminal search; inspect block-paired test gaps and statuses before any quality claim."
        ),
        "failure_policy": "Failed requests and partial histories remain visible; no replacement or automatic retry is included.",
    }
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    save_json(output / "ANALYSIS.json", report, immutable=False)
    lines = ["# 第六章组件验证 E2 运行报告", "", f"实验 `{report['study_id']}` 计划 {report['jobs_planned']} 个任务。", "",
             "本报告把提供方失败与方法效果分开；只有完成真实搜索和独立 test 的任务才有质量数值。", "",
             "| 组 | 计划 | 状态 | 完成提案 | 请求 | 已知 token | Test 数 |", "|---|---:|---|---:|---:|---:|---:|"]
    for arm, row in group_report.items():
        status = ", ".join(f"{k}:{v}" for k, v in row["status_counts"].items())
        gap = "NA" if row["mean_test_gap"] is None else f"{row['mean_test_gap']:.4%}"
        lines.append(f"| {arm} | {row['planned_jobs']} | {status} | {row['completed_proposals']} | {row['request_attempts']} | {row['known_tokens']} | {row['tested_jobs']} ({gap}) |")
    lines += ["", "## 结论", "", f"{report['interpretation']}", "",
              "E0 的离线组件测试与固定槽位 fixture 测试另行记录；本批不能替代 E1/E2 的真实模型搜索。", ""]
    (output / "REPORT_ZH.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def audit(study, output):
    study, output = Path(study), Path(output); files = []
    for path in sorted(study.rglob("*")):
        if path.is_file() and path.name != ".run.lock":
            files.append({"path": path.relative_to(study).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    save_json(output / "ARCHIVE_AUDIT.json", {"study_id": read_json(study / "manifest.json")["study_id"], "file_count": len(files), "files": files, "new_model_calls_in_audit": 0}, immutable=False)


def package(study, output):
    study, output = Path(study), Path(output); archive = output / "raw-study.zip"; index = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(study.rglob("*")):
            if path.is_file() and path.name != ".run.lock":
                rel = f"study/{path.relative_to(study).as_posix()}"; zf.write(path, rel)
                index.append({"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        for name in ("ANALYSIS.json", "REPORT_ZH.md", "TECHNICAL_REPORT.md", "ARCHIVE_AUDIT.json"):
            path = output / name; zf.write(path, name)
            index.append({"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    save_json(output / "ARCHIVE_INDEX.json", index, immutable=False)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--study", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    report = analyze(args.study, args.output); audit(args.study, args.output); package(args.study, args.output)
    print(json.dumps({"study_id": report["study_id"], "jobs": report["jobs_planned"], "tested": report["jobs_tested"], "all_failed": report["all_jobs_infrastructure_failed"]}, ensure_ascii=False))


if __name__ == "__main__": main()
