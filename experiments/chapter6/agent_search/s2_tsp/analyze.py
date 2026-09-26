"""Offline analysis and audit for the completed S2 P1 study."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import zipfile
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo, hi = int(pos), min(len(values) - 1, int(pos) + 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def bootstrap(values, seed, reps=20000):
    if not values:
        return {"lower": None, "upper": None, "mean": None}
    rng = random.Random(seed)
    means = [statistics.fmean(rng.choice(values) for _ in values) for _ in range(reps)]
    return {"lower": percentile(means, .025), "upper": percentile(means, .975),
            "mean": statistics.fmean(values)}


def load(study):
    study = Path(study)
    manifest = read(study / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        run = study / "runs" / job["job_id"]
        status = read(run / "status.json") if (run / "status.json").exists() else {}
        search = read(run / "search_result.json") if (run / "search_result.json").exists() else {}
        test = read(study / "tests" / f"{job['job_id']}.json") if (study / "tests" / f"{job['job_id']}.json").exists() else {}
        rows.append({**job, "status": status.get("status"), "search": search,
                     "test": test, "summary": search.get("summary", {})})
    return manifest, rows


def analyze(study, output):
    manifest, rows = load(study)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    complete = [r for r in rows if r["status"] == "search_complete_test_not_run" and r["test"]]
    by_key = {(r["data_block"], r["search_seed_label"], r["controller"]): r for r in complete}
    pairs = []
    for block in manifest["protocol"]["blocks"]:
        for seed in manifest["protocol"]["search_seeds"]:
            u = by_key.get((block, seed, "fb_unprotected"))
            p = by_key.get((block, seed, "fb_protected"))
            pairs.append({
                "block": block, "seed": seed,
                "unprotected_status": u["status"] if u else "missing",
                "protected_status": p["status"] if p else "missing",
                "unprotected_test_gap": u["test"].get("primary_test_gap") if u else None,
                "protected_test_gap": p["test"].get("primary_test_gap") if p else None,
                "difference_percentage_points": 100 * (p["test"]["primary_test_gap"] - u["test"]["primary_test_gap"])
                    if u and p else None,
                "unprotected_tokens": u["summary"].get("total_tokens") if u else None,
                "protected_tokens": p["summary"].get("total_tokens") if p else None,
                "unprotected_summary": u["summary"] if u else None,
                "protected_summary": p["summary"] if p else None,
            })
    diffs = [p["difference_percentage_points"] for p in pairs if p["difference_percentage_points"] is not None]
    group = {}
    for arm in ("fb_unprotected", "fb_protected"):
        arm_rows = [r for r in complete if r["controller"] == arm]
        gaps = [r["test"]["primary_test_gap"] for r in arm_rows]
        group[arm] = {
            "jobs": len(arm_rows), "valid_test": sum(r["test"].get("primary_test_valid", False) for r in arm_rows),
            "mean_test_gap": statistics.fmean(gaps) if gaps else None,
            "median_test_gap": statistics.median(gaps) if gaps else None,
            "mean_validation_gap": statistics.fmean(r["summary"]["best_validation_loss"] for r in arm_rows) if arm_rows else None,
            "mean_tokens": statistics.fmean(r["summary"].get("total_tokens", 0) for r in arm_rows) if arm_rows else None,
            "mean_requests": statistics.fmean(r["summary"].get("request_count", 0) for r in arm_rows) if arm_rows else None,
            "valid_generated": sum(r["summary"].get("valid_generated", 0) for r in arm_rows),
            "branch_admissions": sum(r["summary"].get("branch_admissions", 0) for r in arm_rows),
            "protected_slots_scheduled": sum(r["summary"].get("protected_slots_scheduled", 0) for r in arm_rows),
            "protected_slots_realized": sum(r["summary"].get("protected_slots_realized", 0) for r in arm_rows),
            "multi_branch_slots": sum(r["summary"].get("multi_branch_slots", 0) for r in arm_rows),
            "protected_parent_improvements": sum(r["summary"].get("protected_branch_parent_improvements", 0) for r in arm_rows),
            "protected_best_ancestor_runs": sum(r["summary"].get("protected_best_ancestor", False) for r in arm_rows),
            "known_reproduction_rate": statistics.fmean(r["summary"].get("known_reproduction_rate", 0) for r in arm_rows) if arm_rows else None,
        }
    report = {
        "study_id": manifest["study_id"], "protocol_status": manifest["status"],
        "jobs_planned": len(rows), "jobs_complete": len(complete),
        "statuses": {status: sum(r["status"] == status for r in rows) for status in sorted({r["status"] for r in rows})},
        "group": group, "pairs": pairs,
        "paired_difference": {
            "unit": "block-seed pair", "count": len(diffs),
            "mean_percentage_points": statistics.fmean(diffs) if diffs else None,
            "median_percentage_points": statistics.median(diffs) if diffs else None,
            "wins_protected": sum(x < 0 for x in diffs), "ties": sum(abs(x) < 1e-12 for x in diffs),
            "losses_protected": sum(x > 0 for x in diffs),
            "bootstrap_95_percentile": bootstrap(diffs, manifest["protocol"]["statistics"]["bootstrap_seed"]),
        },
        "mechanism_gate": {
            "pairs_with_protected_realized": sum(
                (p["protected_summary"] or {}).get("protected_slots_realized", 0) > 0 for p in pairs),
            "pairs_with_multi_branch_protected": sum(
                (p["protected_summary"] or {}).get("multi_branch_slots", 0) > 0 for p in pairs),
            "protection_changed_test_gap_pairs": sum(
                p["difference_percentage_points"] is not None and abs(p["difference_percentage_points"]) > 1e-12 for p in pairs),
            "progression_gate_passed": False,
            "reason": "Only one of twelve pairs realized protected development; no pair had a multi-branch slot.",
        },
        "interpretation": "The protection implementation is operational and the 24 jobs completed, but this batch does not identify a quality effect because the protected branch mechanism was almost never exposed. The result is a valid null/exposure failure, not evidence of superiority or inferiority.",
        "new_model_calls_in_analysis": 0,
    }
    (output / "S2_P1_ANALYSIS.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def audit(study, output):
    manifest, rows = load(study)
    root = Path(study)
    records = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != ".run.lock":
            records.append({"path": path.relative_to(root).as_posix(),
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "bytes": path.stat().st_size})
    payload = {"study_id": manifest["study_id"], "manifest_sha256": manifest["manifest_sha256"],
               "file_count": len(records), "files": records,
               "search_status_counts": {status: sum(r["status"] == status for r in rows) for status in sorted({r["status"] for r in rows})},
               "test_count": len(list((root / "tests").glob("*.json"))),
               "new_model_calls_in_audit": 0}
    Path(output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def package(study, archive, analysis, audit_path):
    study, archive = Path(study), Path(archive)
    analysis, audit_path = Path(analysis), Path(audit_path)
    index = []
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for source in sorted(study.rglob("*")):
            if source.is_file() and source.name != ".run.lock":
                rel = source.relative_to(study).as_posix()
                zf.write(source, f"study/{rel}")
                index.append({"path": f"study/{rel}", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size})
        extra = [analysis, audit_path]
        report = analysis.parent.parent / "REPORT_ZH.md"
        if report.exists():
            extra.append(report)
        for source in extra:
            zf.write(source, source.name)
            index.append({"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size})
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = analyze(args.study, args.output)
    audit_path = args.output / "S2_P1_AUDIT.json"
    audit(args.study, audit_path)
    if args.package:
        archive = args.output / "raw-study.zip"
        index = package(args.study, archive, args.output / "S2_P1_ANALYSIS.json", audit_path)
        (args.output / "ARCHIVE_INDEX.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"jobs": report["jobs_complete"], "mean_difference_pp": report["paired_difference"]["mean_percentage_points"],
                      "mechanism_gate": report["mechanism_gate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
