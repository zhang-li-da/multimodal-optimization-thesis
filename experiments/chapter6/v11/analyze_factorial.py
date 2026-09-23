"""Summarize every preregistered run and paired 2x2 contrasts without p-values."""
from __future__ import annotations

import argparse
import csv
from itertools import product
import json
import math
from pathlib import Path
import statistics


METRICS = {
    "validation_selected_test_loss": ("validation-selected mean test loss", "lower"),
    "validation_selected_test_valid": ("validation-selected rule has valid test execution", "higher"),
    "common_gate_test_behavior_modes": ("test behavior modes within common quality gate", "higher"),
    "common_gate_test_eligible_size": ("test-eligible common-gate archive size", "higher"),
    "valid_fraction": ("valid generated fraction", "higher"),
    "tokens_used": ("reported input plus output tokens", "lower"),
    "token_budget_valid": ("valid hard-token-ceiling run", "higher"),
    "learned_selector_test_loss": ("validation-fitted selector mean test loss", "lower"),
    "selector_gain_vs_single": ("selector improvement over validation-selected single rule", "higher"),
    "selector_seconds_per_instance": ("selector feature and prediction seconds per test instance", "lower"),
    "oracle_gain_upper_bound": ("test-instance oracle gain (upper bound only)", "higher"),
    "parent_improvements": ("parent improvements", "higher"),
    "neighborhood_improvements": ("behavior-neighborhood improvements", "higher"),
    "productive_collisions": ("productive behavior collisions", "higher"),
    "unproductive_collisions": ("unproductive behavior collisions", "lower"),
    "restarts": ("restarts", "lower"),
}
FACTORIAL_METHODS = ("relational", "relational_qp", "relational_rr", "relational_qp_rr")
METHODS_SCREENING = ("niche", *FACTORIAL_METHODS)


def _mean(values):
    return statistics.fmean(values) if values else None


def _bootstrap_interval(values):
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    n = len(values)
    # With five blocks there are only 5**5 ordered bootstrap resamples; enumerate
    # the full percentile distribution instead of adding Monte Carlo noise.
    draws = sorted(statistics.fmean(values[index] for index in sample)
                   for sample in product(range(n), repeat=n))
    def quantile(probability):
        position = probability * (len(draws) - 1)
        lower = math.floor(position)
        upper = math.ceil(position)
        return draws[lower] + (draws[upper] - draws[lower]) * (position - lower)
    return quantile(.025), quantile(.975)


def _summary_metric(result, metric):
    summary = result["summary"]
    if metric == "tokens_used" and not summary.get("usage_complete", True):
        return None
    if metric == "selector_seconds_per_instance":
        return result.get("selector", {}).get("selection_seconds_per_test_instance")
    if metric in ("learned_selector_test_loss", "selector_gain_vs_single", "oracle_gain_upper_bound"):
        selector = result.get("selector", {})
        keys = {
            "learned_selector_test_loss": "learned_selector_test_loss",
            "selector_gain_vs_single": "learned_selector_gain_vs_single",
            "oracle_gain_upper_bound": "oracle_gain_upper_bound_only",
        }
        return selector.get(keys[metric])
    return summary.get(metric)


def _write_csv(path: Path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def analyze(root: Path, output: Path):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    status_list = json.loads((root / "status.json").read_text(encoding="utf-8")) if (root / "status.json").exists() else []
    status = {x["job_id"]: x for x in status_list}
    jobs = {x["job_id"]: x for x in manifest["jobs"]}
    results = {}
    run_rows = []
    problems = []
    for job_id, job in jobs.items():
        result_path = root / "runs" / job_id / "result.json"
        state = status.get(job_id, {}).get("status", "not_started")
        result = None
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                results[job_id] = result
                if result["config"].get("source_fingerprint") != manifest["source_fingerprint_sha256"]:
                    problems.append({"job_id": job_id, "problem": "source fingerprint mismatch"})
                if result["config"].get("benchmark_version", "").find("chapter6-v11-independent-v1") < 0:
                    problems.append({"job_id": job_id, "problem": "wrong benchmark profile"})
                if result["config"].get("data_block") != job["block"]:
                    problems.append({"job_id": job_id, "problem": "wrong data block"})
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                problems.append({"job_id": job_id, "problem": f"unreadable result: {type(exc).__name__}"})
        if result is None:
            run_rows.append({**job, "status": state, "result_present": False})
            continue
        summary = result["summary"]
        selector = result.get("selector", {})
        row = {**job, "status": state, "result_present": True,
               "source_fingerprint": result["config"].get("source_fingerprint"),
               "benchmark_version": result["config"].get("benchmark_version"),
               "request_errors": summary.get("request_errors"),
               "usage_complete": summary.get("usage_complete"),
               "reservation_violations": len(result.get("reservation_violations", [])),
               "budget_stop_stage": summary.get("budget_stop_stage"),
               "selector_status": selector.get("status")}
        for metric in METRICS:
            row[metric] = None if job["task"] == "binpack" and metric.startswith("selector_") else _summary_metric(result, metric)
        row.update({
            "generated": summary.get("generated"), "valid_generated": summary.get("valid_generated"),
            "input_tokens": summary.get("input_tokens"), "output_tokens": summary.get("output_tokens"),
            "model_calls": summary.get("model_calls"), "partial_attempts": summary.get("partial_attempts"),
            "elapsed_seconds": summary.get("elapsed_seconds"),
            "parent_improvements": summary.get("parent_improvements"),
            "neighborhood_improvements": summary.get("neighborhood_improvements"),
            "productive_collisions": summary.get("productive_collisions"),
            "unproductive_collisions": summary.get("unproductive_collisions"),
            "restarts": summary.get("restarts"),
        })
        run_rows.append(row)

    # Retain one row for every planned job, including infrastructure failures.
    output.mkdir(parents=True, exist_ok=True)
    job_fields = list(manifest["jobs"][0]) + ["status", "result_present", "source_fingerprint", "benchmark_version",
        "request_errors", "usage_complete", "reservation_violations", "budget_stop_stage", "selector_status",
        *METRICS.keys(), "generated", "valid_generated", "input_tokens", "output_tokens", "model_calls",
        "partial_attempts", "elapsed_seconds", "parent_improvements", "neighborhood_improvements",
        "productive_collisions", "unproductive_collisions", "restarts"]
    _write_csv(output / "run_level.csv", run_rows, list(dict.fromkeys(job_fields)))

    group_rows = []
    contrast_rows = []
    cells = sorted({(j["provider"], j["model"], j["task"], j["regime"]) for j in manifest["jobs"]})
    for provider, model, task, regime in cells:
        cell_jobs = [j for j in manifest["jobs"] if (j["provider"], j["model"], j["task"], j["regime"])
                     == (provider, model, task, regime)]
        for method in sorted({j["method"] for j in cell_jobs}):
            selected = [j for j in cell_jobs if j["method"] == method]
            for metric_index, (metric, (label, direction)) in enumerate(METRICS.items()):
                block_values = []
                for job in selected:
                    result = results.get(job["job_id"])
                    value = _summary_metric(result, metric) if result else None
                    if isinstance(value, bool):
                        value = int(value)
                    if isinstance(value, (float, int)) and math.isfinite(value):
                        block_values.append((job["block"], float(value)))
                vals = [x[1] for x in block_values]
                low, high = _bootstrap_interval(vals)
                group_rows.append({
                    "provider": provider, "model": model, "task": task, "regime": regime,
                    "method": method, "metric": metric, "metric_label": label, "direction": direction,
                    "n": len(vals), "mean": _mean(vals), "median": statistics.median(vals) if vals else None,
                    "bootstrap_95_low": low, "bootstrap_95_high": high,
                    "block_values": json.dumps({str(b): v for b, v in block_values}, sort_keys=True),
                })

        factor_jobs = {method: {j["block"]: results.get(j["job_id"])
                                for j in cell_jobs if j["method"] == method}
                       for method in FACTORIAL_METHODS}
        niche = {j["block"]: results.get(j["job_id"])
                 for j in cell_jobs if j["method"] == "niche"}
        for method in FACTORIAL_METHODS:
            for metric, (_, direction) in METRICS.items():
                differences = []
                for block in range(5):
                    left, right = factor_jobs[method].get(block), niche.get(block)
                    if left and right:
                        a, b = _summary_metric(left, metric), _summary_metric(right, metric)
                        if isinstance(a, bool): a = int(a)
                        if isinstance(b, bool): b = int(b)
                        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isfinite(a) and math.isfinite(b):
                            differences.append((block, float(a)-float(b)))
                vals = [x[1] for x in differences]
                low, high = _bootstrap_interval(vals)
                contrast_rows.append({"provider": provider, "model": model, "task": task,
                    "regime": regime, "contrast": f"{method}_minus_niche", "metric": metric,
                    "direction": direction, "n_paired": len(vals), "mean_difference": _mean(vals),
                    "bootstrap_95_low": low, "bootstrap_95_high": high,
                    "block_differences": json.dumps({str(b): v for b,v in differences}, sort_keys=True)})
        for metric, (_, direction) in METRICS.items():
            formulas = {
                "quality_protection_main_effect": lambda y: ((y["relational_qp"]-y["relational"])
                    +(y["relational_qp_rr"]-y["relational_rr"]))/2,
                "restart_correction_main_effect": lambda y: ((y["relational_rr"]-y["relational"])
                    +(y["relational_qp_rr"]-y["relational_qp"]))/2,
                "quality_by_restart_interaction": lambda y: (y["relational_qp_rr"]-y["relational_qp"]
                    -y["relational_rr"]+y["relational"]),
            }
            paired = []
            for block in range(5):
                vals = {}
                for method in FACTORIAL_METHODS:
                    result = factor_jobs[method].get(block)
                    value = _summary_metric(result, metric) if result else None
                    if isinstance(value, bool): value = int(value)
                    if isinstance(value, (int, float)) and math.isfinite(value): vals[method] = float(value)
                if len(vals) == 4:
                    paired.append((block, vals))
            for name, formula in formulas.items():
                estimates = [formula(values) for _, values in paired]
                draws = (sorted(statistics.fmean(formula(paired[index][1]) for index in sample)
                                for sample in product(range(len(paired)), repeat=len(paired)))
                         if paired else [])
                if draws:
                    def quantile(probability):
                        position = probability * (len(draws) - 1)
                        lower, upper = math.floor(position), math.ceil(position)
                        return draws[lower] + (draws[upper] - draws[lower]) * (position - lower)
                    low, high = quantile(.025), quantile(.975)
                else:
                    low = high = None
                contrast_rows.append({"provider": provider, "model": model, "task": task,
                    "regime": regime, "contrast": name, "metric": metric, "direction": direction,
                    "n_paired": len(estimates), "mean_difference": _mean(estimates),
                    "bootstrap_95_low": low, "bootstrap_95_high": high,
                    "block_differences": json.dumps({str(paired[i][0]): v for i,v in enumerate(estimates)}, sort_keys=True)})

    _write_csv(output / "group_summary.csv", group_rows,
               ["provider", "model", "task", "regime", "method", "metric", "metric_label", "direction",
                "n", "mean", "median", "bootstrap_95_low", "bootstrap_95_high", "block_values"])
    _write_csv(output / "paired_contrasts.csv", contrast_rows,
               ["provider", "model", "task", "regime", "contrast", "metric", "direction", "n_paired",
                "mean_difference", "bootstrap_95_low", "bootstrap_95_high", "block_differences"])

    complete = sum(1 for jid in jobs if jid in results)
    counts = {}
    for row in run_rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    report = [
        "# Chapter 6 v1.1 quality-protection experiment report",
        "",
        f"Study: `{manifest['study_id']}`. Preregistered source commit: `{manifest['source_commit']}`.",
        f"Manifest SHA-256: `{manifest['manifest_sha256']}`; source fingerprint: `{manifest['source_fingerprint_sha256']}`.",
        "",
        "## Design and inferential scope",
        "",
        "This is a live screening experiment of parent/neighborhood quality protection and restart correction in a 2×2 controller factorial, with niche search as a contextual baseline. It crosses TSP and online bin packing, Qwen3.7-Plus and MiniMax-M3, five paired data blocks, and fixed 8-slot versus 30,000 input+output token budgets (200 planned runs). The API model writes bounded heuristic code; deterministic evaluators execute it on separate probe, validation, and test instances. The validation-fitted per-instance selector is TSP-only because full-sequence bin-packing descriptors would reveal future arrivals.",
        "",
        "The unit of replication is the paired block-level run. Generated candidates and test instances are not treated as independent algorithm replications. Intervals are percentile bootstrap intervals over five paired blocks; they are descriptive and do not establish small effects or doctoral-level novelty. No confirmatory p-values are reported.",
        "",
        "## Completion and data integrity",
        "",
        f"Results files present: {complete}/{len(jobs)}. Status counts: `{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`.",
        f"Integrity problems detected: {len(problems)}.",
        "",
        "See `run_level.csv` for all planned jobs, including absent/failed runs; `group_summary.csv` for per-cell means, medians, intervals, and block values; `paired_contrasts.csv` for relational-minus-niche paired contrasts and factorial main effects/interactions.",
        "",
        "## Primary outcomes by cell",
        "",
        "Lower test loss is better. The archive mode count requires held-out test quality eligibility against the shared-seed test threshold; the validation-fitted selector is reported separately. A test-instance oracle is an unattainable upper bound only.",
        "",
        "| Model | Task | Budget | Method | Test loss mean [95% interval] | Test modes mean [95% interval] | Selector gain mean [95% interval] | n |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    summary_lookup = {(r["provider"],r["task"],r["regime"],r["method"],r["metric"]):r for r in group_rows}
    for provider, model, task, regime in cells:
        for method in METHODS_SCREENING:
            loss = summary_lookup.get((provider,task,regime,method,"validation_selected_test_loss"),{})
            modes = summary_lookup.get((provider,task,regime,method,"common_gate_test_behavior_modes"),{})
            gain = summary_lookup.get((provider,task,regime,method,"selector_gain_vs_single"),{}) if task == "tsp" else {}
            def fmt(row):
                mean=row.get("mean")
                return "—" if mean is None else f"{mean:.4f} [{row.get('bootstrap_95_low'):.4f}, {row.get('bootstrap_95_high'):.4f}]"
            report.append(f"| {model} | {task} | {regime} | {method} | {fmt(loss)} | {fmt(modes)} | {fmt(gain)} | {loss.get('n',0)} |")
    report.extend([
        "",
        "## Interpretation limits",
        "",
        "Read the individual paired blocks and integrity columns before interpreting a cell average. An absent result, API request failure, missing token usage, reservation violation, or budget stop is retained and is not replaced based on its outcome. A missing provider usage value invalidates strict token-efficiency claims for that run. The `tokens30000` regime is a hard admission ceiling with UTF-8 byte-based conservative reservations; reservation violations are explicitly flagged and do not qualify as compliant runs.",
        "",
        "The TSP algorithm-set selector uses validation outcomes and public instance features only. No full-sequence selector is evaluated for online bin packing. The per-test-instance oracle uses held-out outcomes and is not deployable. Positive screening patterns require a new independently frozen confirmation; null or mixed outcomes narrow the mechanism claim.",
        "",
        f"Integrity details: `{json.dumps(problems, ensure_ascii=False)}`",
    ])
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    (output / "analysis.json").write_text(json.dumps({
        "study_id": manifest["study_id"], "manifest_sha256": manifest["manifest_sha256"],
        "planned_runs": len(jobs), "results_present": complete, "status_counts": counts,
        "integrity_problems": problems, "group_summary": group_rows, "paired_contrasts": contrast_rows,
    }, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"report": str(output / "REPORT.md"), "results_present": complete,
                      "planned_runs": len(jobs), "integrity_problems": len(problems)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    root = Path(args.run_directory)
    output = Path(args.output) if args.output else root / "analysis"
    analyze(root, output)


if __name__ == "__main__":
    main()
