"""Evaluate an already sealed validation readout in a separate process."""
from __future__ import annotations

import argparse
from pathlib import Path

from .common import digest, file_sha, read_json, run_lock, save_json, utcnow
from .data import evaluate_test, split_path, verify_manifest
from .identity import identity
from .runner import restore

__test__ = False


def evaluate_frozen(run_directory, test_snapshot, output, *, evaluator=evaluate_test):
    run_directory, output = Path(run_directory), Path(output)
    result = read_json(run_directory / "search_result.json")
    readout = read_json(run_directory / "selection_frozen.json")
    config = result["config"]
    checkpoint = read_json(run_directory / "checkpoint.json")
    if result["status"] != "search_complete_test_not_run" or checkpoint["config"] != config:
        raise ValueError("Test requires a completed search.")
    if (file_sha(run_directory / "selection_frozen.json") != result["selection_frozen_sha256"]
            or file_sha(run_directory / "checkpoint.json") != result["checkpoint_sha256"]
            or readout["run_config_sha256"] != digest(config)):
        raise ValueError("Search checkpoint/readout identity changed.")
    state = restore(checkpoint)
    if len(checkpoint["records"]) != config["steps"] or test_snapshot["block"] != config["data_block"]:
        raise ValueError("Incomplete search or wrong test block.")
    best = min((n for n in state.nodes if n["evaluation"]["valid"]),
               key=lambda n: (n["evaluation"]["loss"], n["id"]))
    seed_best = min(checkpoint["seeds"], key=lambda n: (n["evaluation"]["loss"], n["id"]))
    if (readout["best_id"], readout["seed_best_id"], readout["selected_on"]) != (best["id"], seed_best["id"], "validation"):
        raise ValueError("Readout must be selected only on validation.")
    binding = {"readout_sha256": result["selection_frozen_sha256"],
               "test_snapshot_sha256": digest(test_snapshot), "config_sha256": digest(config)}
    with run_lock(output.parent):
        if output.exists():
            saved = read_json(output)
            if saved["binding"] != binding:
                raise ValueError("Test output binding differs; do not overwrite evidence.")
            return saved
        by_id = {n["id"]: n for n in state.nodes}
        evaluations = {}
        for program in readout["programs"]:
            if (program["code"] != by_id[program["id"]]["code"] or
                    {k: program[k] for k in identity(program["code"])} != identity(program["code"])):
                raise ValueError("Frozen program identity differs.")
            evaluations[str(program["id"])] = evaluator(program["code"], test_snapshot)
        primary, seed = evaluations[str(readout["best_id"])], evaluations[str(readout["seed_best_id"])]
        report = {"binding": binding, "config": config, "evaluated_utc": utcnow(),
                  "best_id": readout["best_id"], "seed_best_id": readout["seed_best_id"],
                  "archive_ids": readout["archive_ids"], "selected_on": "validation",
                  "primary_test_gap": primary["loss"] if primary["valid"] else 1.0,
                  "primary_test_valid": primary["valid"],
                  "seed_validation_selected_test_gap": seed["loss"] if seed["valid"] else 1.0,
                  "test_failure_penalty": 1.0, "evaluations": evaluations,
                  "selector": None, "new_model_calls": 0}
        save_json(output, report, immutable=True)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", required=True, type=Path)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    manifest = verify_manifest(args.study)
    job = next(item for item in manifest["jobs"] if item["job_id"] == args.job)
    # Do not open held-out coordinates before the completed readout is present.
    result_path = args.study / "runs" / args.job / "search_result.json"
    if not result_path.exists() or read_json(result_path)["status"] != "search_complete_test_not_run":
        raise ValueError("Finish and freeze this search before loading test data.")
    run_config = read_json(result_path)["config"]
    if (run_config["binding"] != {"manifest_sha256": manifest["manifest_sha256"]}
            or any(run_config.get(key) != value for key, value in job.items())):
        raise ValueError("Search result does not belong to this frozen job.")
    test = read_json(split_path(args.study, manifest, job["data_block"], "test"))
    from chapter6_demo.v12_1.audit_r2 import offline_only
    with offline_only():
        report = evaluate_frozen(args.study / "runs" / args.job, test, args.study / "tests" / f"{args.job}.json")
    print({"job": args.job, "primary_test_gap": report["primary_test_gap"], "new_model_calls": 0})


if __name__ == "__main__":
    main()
