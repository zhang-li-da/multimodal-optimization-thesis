"""Offline, common-target outcomes for the frozen I/B/E0/EG diagnostic.

This is an analysis revision, not a live allocation rule or a new protocol.
No evaluator, model transport, or Test data is called.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from chapter6_demo.v12_2.common import read_json, save_json
from .audit_segments import evidence_run, verify_layers


COMPARISONS = (("B", "I"), ("EG", "E0"), ("E0", "I"), ("EG", "I"))


def horizon_outcome(checkpoint, records, horizon):
    """Select only the requested prefix and charge its whole investment unit."""
    baseline = float(checkpoint["incumbent"]["loss"]) if checkpoint.get("incumbent") else None
    if baseline is None or len(records) < horizon:
        return {"status": "missing_prefix", "horizon": horizon,
                "observed_proposals": min(len(records), horizon),
                "global_gain_pp": None, "gain_pp_per_1000_tokens": None,
                "known_tokens": None, "usage_complete": None}
    prefix = records[:horizon]
    valid = [n for n in checkpoint["nodes"] + [r["node"] for r in prefix]
             if n["evaluation"]["valid"]]
    best = min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"]))
    gain_pp = 100 * (baseline - best["evaluation"]["loss"])
    usage = prefix[-1]["usage_after"]
    known = usage["known_tokens"]
    complete = usage.get("usage_complete") is True
    events = [r["event"] for r in prefix]
    # The E root, failed discoveries, and all developments belong to ONE unit.
    # Local recovery from a poor start is diagnostic, never the global numerator.
    return {"status": "observed_prefix", "horizon": horizon,
            "baseline_validation_loss": baseline,
            "best_id": best["id"], "best_validation_loss": best["evaluation"]["loss"],
            "global_gain_pp": gain_pp, "known_tokens": known,
            "requests": usage["call_attempts"], "usage_complete": complete,
            "gain_pp_per_1000_tokens": 1000 * gain_pp / known if complete and known > 0 else None,
            "valid_proposals": sum(r["node"]["evaluation"]["valid"] for r in prefix),
            "project_progress_events": sum(e["project_progress"] for e in events),
            "exploration_proposals": sum(e["action"] == "explore" for e in events),
            "development_proposals": sum(e["action"] == "develop" for e in events),
            "exploration_admissions": sum(e.get("exploration_admitted", False) for e in events),
            "first_global_improvement_proposal": next(
                (i + 1 for i, r in enumerate(prefix) if r["node"]["evaluation"]["valid"]
                 and r["node"]["evaluation"]["loss"] < baseline - 1e-4), None),
            "evaluation_seconds": sum(r["costs"].get("evaluation_seconds", 0) for r in prefix)}


def marginal_outcome(first, last):
    """4-to-8 marginal return; do not credit the first four proposals twice."""
    if any(x["status"] != "observed_prefix" for x in (first, last)):
        return {"status": "censored", "global_gain_pp": None, "known_tokens": None}
    gain = last["global_gain_pp"] - first["global_gain_pp"]
    cost = last["known_tokens"] - first["known_tokens"]
    complete = first["usage_complete"] and last["usage_complete"]
    return {"status": "observed_prefix", "global_gain_pp": gain,
            "known_tokens": cost, "usage_complete": complete,
            "gain_pp_per_1000_tokens": 1000 * gain / cost if complete and cost > 0 else None}


def paired_summary(rows):
    lookup = {(r["checkpoint_id"], r["repetition"], r["strategy"]): r for r in rows}
    results = []
    for action, comparator in COMPARISONS:
        for horizon in ("4", "8"):
            pairs, missing, by_block = [], [], defaultdict(list)
            for row in rows:
                if row["strategy"] != action:
                    continue
                other = lookup[(row["checkpoint_id"], row["repetition"], comparator)]
                x, y = row["horizons"][horizon], other["horizons"][horizon]
                if any(z["status"] != "observed_prefix" for z in (x, y)):
                    missing.append({"checkpoint_id": row["checkpoint_id"], "repetition": row["repetition"],
                                    "action_status": row["status"], "comparator_status": other["status"]})
                    continue
                assert x["baseline_validation_loss"] == y["baseline_validation_loss"]
                delta = x["global_gain_pp"] - y["global_gain_pp"]
                pairs.append({"checkpoint_id": row["checkpoint_id"], "repetition": row["repetition"],
                              "block": row["block"], "gain_difference_pp": delta,
                              "action_tokens": x["known_tokens"], "comparator_tokens": y["known_tokens"]})
                by_block[row["block"]].append(delta)
            block_means = {str(b): mean(v) for b, v in sorted(by_block.items())}
            results.append({"comparison": action + "-" + comparator, "horizon": int(horizon),
                            "positive_favors": action, "complete_pairs": len(pairs),
                            "independent_blocks_observed": len(block_means),
                            "block_mean_gain_difference_pp": block_means,
                            "equal_block_mean_pp": mean(block_means.values()) if block_means else None,
                            "pairs": pairs, "missing": missing})
    return results


def analyze(root):
    root = Path(root).resolve()
    verify_layers(root)
    manifest = read_json(root / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        layer, run = evidence_run(root, job["job_id"])
        terminal = read_json(run / "terminal_status.json") if (run / "terminal_status.json").exists() else {}
        status = terminal.get("status", "running" if (run / "config.json").exists() else "not_started")
        # Live files may still change. Only final task records are analyzed.
        records = read_json(run / "checkpoint.json")["records"] if terminal and (run / "checkpoint.json").exists() else []
        cp = read_json(root / "checkpoints" / (job["checkpoint_id"] + ".json"))
        windows = {str(h): horizon_outcome(cp, records, h) for h in (4, 8)}
        if records:
            selections = read_json(run / "selection_candidates.json")
            for h, value in windows.items():
                if value["status"] == "observed_prefix":
                    assert value["best_id"] == selections[h]["best_id"]
                    assert value["best_validation_loss"] == selections[h]["validation_loss"]
        rows.append({"job_id": job["job_id"], "checkpoint_id": job["checkpoint_id"],
                     "block": job["data_block"], "strategy": job["strategy"],
                     "repetition": job["repetition"], "status": status,
                     "evidence_layer": layer.relative_to(root).as_posix(),
                     "terminal_wall_seconds": terminal.get("wall_seconds"),
                     "horizons": windows, "marginal_4_to_8": marginal_outcome(windows["4"], windows["8"])})
    return {"schema": "chapter6-common-target-investment-outcomes-v1",
            "manifest_sha256": manifest["manifest_sha256"],
            "analysis_revision": "Retrospective search-only diagnostics, not a pre-registered efficacy endpoint",
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "rows": rows, "comparisons": paired_summary(rows),
            "cost_scope": "Each window includes all exploration and development requests through that proposal; whole-task failures remain in the separate cumulative ledger",
            "interpretation": "Common resource ceilings, not equal actual token spending; validation and descriptive efficiency cannot establish Test efficacy",
            "test_access": False, "model_calls": 0, "numeric_reevaluations": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.study.resolve()):
        raise ValueError("write analysis outside immutable study evidence")
    result = analyze(args.study)
    save_json(args.output, result)
    print(json.dumps({"rows": len(result["rows"]), "status_counts": result["status_counts"],
                      "pairs": [dict(comparison=c["comparison"], horizon=c["horizon"], n=c["complete_pairs"]) for c in result["comparisons"]]}))


if __name__ == "__main__":
    main()
