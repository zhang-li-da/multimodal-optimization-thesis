"""Prospective size-transfer evaluation of S3's validation-selected programs.

No model transport. Prepare and commit the manifest before opening S3 test results.
The heuristic reference is a feasible upper bound, NOT the TSP optimum.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import statistics
import subprocess

from chapter6_demo.programs import ProgramError
from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json, utcnow
from . import tsp_scale
from .s3_tsp_r3.analyze import bootstrap

ROOT = Path(__file__).resolve().parents[3]
PROTOCOL = {
    "name": "s3-transfer-n50-n100-20260927",
    "role": "prospective secondary transfer evaluation; not an additional algorithm-search study",
    "blocks": list(range(32, 40)), "sizes": [50, 100],
    "families": ["uniform", "clustered", "grid"], "instances_per_family_per_block": 4,
    "seed_rule": "927390000 + block*1000 + size*5 + family_index*10 + repetition",
    "selection": "each S3 run's frozen validation-best program and validation-best shared seed; no transfer-based selection",
    "reference": "4-start nearest neighbor plus up to 200*n 2-opt delta checks per start; feasible upper bound, not OPT",
    "candidate_local_budget": "fixed 24 2-opt checks, matching TSP14 search; no scale-dependent extra local budget",
    "instance_wall_seconds": 30,
    "invalid_program_loss": 1.0,
    "failure_policy": "retain failed instances with loss=1.0 and explicit failure; missing frozen outputs remain missing",
    "primary_description": "signed mean (tour_length/reference_length - 1); can be negative; do not call this optimality gap",
    "paired_contrasts": [["FB_P", "FB_U"], ["TS_P", "FB_P"], ["AD_P", "FB_P"]],
    "statistics": "separately by size; resample the 8 complete data blocks; 20000 percentile replicates, seed 9273901; descriptive only",
    "distribution_limit": "grid uses a nearly square layout in the scale adapter, unlike the TSP14 grid generator; size and grid geometry both change",
    "external_validity": "tests transfer of TSP14-discovered priority rules; no search at 50/100 cities and no cross-task evidence",
    "new_model_calls": 0,
}


def sources():
    paths = [Path(__file__), Path(tsp_scale.__file__),
             ROOT / "experiments/chapter6/demo/programs.py", ROOT / "experiments/chapter6/v12_2/common.py",
             ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/analyze.py"]
    return {p.relative_to(ROOT).as_posix(): file_sha(p) for p in paths}


def prepare(args):
    if args.output.exists():
        raise ValueError("Transfer preparation requires a new directory")
    if (args.study / "tests").exists():
        raise ValueError("Prepare before S3 test results are opened")
    source = sources()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *source], cwd=ROOT, text=True)
    if dirty:
        raise ValueError("Commit transfer evaluation source before preparing its frozen manifest")
    study_manifest = read_json(args.study / "manifest.json")
    data = {}
    for block in PROTOCOL["blocks"]:
        data[str(block)] = {}
        for size in PROTOCOL["sizes"]:
            instances = []
            for family_index, family in enumerate(PROTOCOL["families"]):
                for repetition in range(PROTOCOL["instances_per_family_per_block"]):
                    seed = 927390000 + block * 1000 + size * 5 + family_index * 10 + repetition
                    item = tsp_scale.instance(family, seed, size)
                    item["reference"] = tsp_scale.reference(item, starts=4, checks_per_city=200)
                    tsp_scale.prepare(item)  # Check the archived certificate.
                    instances.append(item)
            relative = f"data/b{block}-n{size}.json"
            save_json(args.output / relative, {"block": block, "size": size, "instances": instances}, immutable=True)
            data[str(block)][str(size)] = {"path": relative, "sha256": file_sha(args.output / relative)}
    manifest = {"status": "FROZEN_BEFORE_S3_TEST", "created_utc": utcnow(),
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source": source, "protocol": PROTOCOL, "data": data,
                "s3_manifest_sha256": study_manifest["manifest_sha256"],
                "s3_jobs": study_manifest["jobs"], "new_model_calls": 0}
    manifest["manifest_sha256"] = digest(manifest)
    save_json(args.output / "manifest.json", manifest, immutable=True)
    return {"status": manifest["status"], "instances": 192, "manifest_sha256": manifest["manifest_sha256"]}


def verify(directory):
    manifest = read_json(directory / "manifest.json")
    assert manifest["manifest_sha256"] == digest({k: v for k, v in manifest.items() if k != "manifest_sha256"})
    assert manifest["source"] == sources()
    assert manifest["protocol"] == PROTOCOL
    for sizes in manifest["data"].values():
        for record in sizes.values():
            path = (directory / record["path"]).resolve()
            assert path.is_relative_to(directory.resolve()) and file_sha(path) == record["sha256"]
    return manifest


def execute_one(args):
    study, directory, job, size = args
    study, directory = Path(study), Path(directory)
    selection_path = study / "runs" / job["job_id"] / "selection_frozen.json"
    target = directory / "evaluations" / f"{job['job_id']}-n{size}.json"
    if not selection_path.exists():
        result = {"job_id": job["job_id"], "arm": job["arm_id"], "block": job["data_block"],
                  "size": size, "status": "missing_frozen_output", "new_model_calls": 0}
        save_json(target, result, immutable=True)
        return result
    selection = read_json(selection_path)
    snapshot_path = directory / "data" / f"b{job['data_block']}-n{size}.json"
    binding = {"selection_sha256": file_sha(selection_path), "snapshot_sha256": file_sha(snapshot_path)}
    if target.exists():
        saved = read_json(target)
        assert saved["binding"] == binding
        return saved
    snapshot = read_json(snapshot_path)
    programs = {p["id"]: p for p in selection["programs"]}
    rows = []
    for item in snapshot["instances"]:
        prepared = tsp_scale.prepare(item)
        row = {"instance_id": item["id"], "family": item["family"]}
        for role, identifier in (("selected", selection["best_id"]), ("seed", selection["seed_best_id"])):
            try:
                evaluation = tsp_scale.execute(programs[identifier]["code"], prepared,
                                               local_checks_per_city=0, max_seconds=30)
                evaluation["failure"] = None
            except (ProgramError, ValueError, ArithmeticError, RecursionError) as exc:
                evaluation = {"valid": False, "loss": 1.0, "failure": type(exc).__name__, "error": str(exc)[:180]}
            row[role] = evaluation
        rows.append(row)
    result = {"job_id": job["job_id"], "arm": job["arm_id"], "block": job["data_block"],
              "size": size, "status": "evaluated", "binding": binding, "evaluated_utc": utcnow(),
              "new_model_calls": 0, "best_id": selection["best_id"], "seed_best_id": selection["seed_best_id"],
              "selected_loss": statistics.fmean(row["selected"]["loss"] for row in rows),
              "seed_loss": statistics.fmean(row["seed"]["loss"] for row in rows),
              "invalid_selected": sum(not row["selected"]["valid"] for row in rows),
              "invalid_seed": sum(not row["seed"]["valid"] for row in rows), "rows": rows}
    save_json(target, result, immutable=True)
    return read_json(target)


def run(args):
    manifest = verify(args.output)
    s3 = read_json(args.study / "manifest.json")
    assert s3["manifest_sha256"] == manifest["s3_manifest_sha256"]
    terminal = {"search_complete_test_not_run", "infrastructure_incomplete", "budget_exhausted"}
    for job in s3["jobs"]:
        path = args.study / "runs" / job["job_id"] / "status.json"
        if not path.exists() or read_json(path).get("status") not in terminal:
            raise ValueError("Transfer evaluation waits until every search is terminal")
    tasks = [(str(args.study.resolve()), str(args.output.resolve()), j, n)
             for j in s3["jobs"] for n in PROTOCOL["sizes"]]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(execute_one, tasks))
    output = {"manifest_sha256": manifest["manifest_sha256"], "new_model_calls": 0,
              "planned_program_size_evaluations": len(tasks),
              "missing": [r for r in rows if r["status"] != "evaluated"], "groups": {}, "contrasts": {}}
    for n in PROTOCOL["sizes"]:
        subset = [r for r in rows if r["size"] == n and r["status"] == "evaluated"]
        for arm in sorted({j["arm_id"] for j in s3["jobs"]}):
            selected = [r for r in subset if r["arm"] == arm]
            output["groups"][f"{arm}-n{n}"] = {
                "evaluated_blocks": len(selected),
                "mean_signed_relative_reference_loss": statistics.fmean(r["selected_loss"] for r in selected) if selected else None,
                "improvement_vs_seed_pp": statistics.fmean(100*(r["seed_loss"]-r["selected_loss"]) for r in selected) if selected else None,
                "invalid_instances": sum(r["invalid_selected"] for r in selected),
                "families": {f: statistics.fmean(x["selected"]["loss"] for r in selected for x in r["rows"] if x["family"] == f)
                             for f in PROTOCOL["families"]} if selected else {}}
        by_key = {(r["arm"], r["block"]): r for r in subset}
        for left, right in PROTOCOL["paired_contrasts"]:
            pairs = [{"block": b, "difference_pp": 100*(by_key[left,b]["selected_loss"]-by_key[right,b]["selected_loss"])}
                     for b in PROTOCOL["blocks"] if (left,b) in by_key and (right,b) in by_key]
            output["contrasts"][f"{left}_minus_{right}-n{n}"] = {
                "pairs": pairs, "descriptive_block_interval": bootstrap([r["difference_pp"] for r in pairs], 9273901)}
    save_json(args.output / "TRANSFER_ANALYSIS.json", output)
    return {"evaluated": len(rows)-len(output["missing"]), "missing": len(output["missing"]), "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(prepare(args) if args.command == "prepare" else run(args)))


if __name__ == "__main__":
    main()
