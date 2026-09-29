"""Offline checkpoint construction for the short-horizon diagnostic study."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Iterable

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json
from chapter6_demo.v12_2.data import content_hash

from ..s3_tsp_r3.evaluator import evaluate_search
from .e1 import choose_checkpoint_nodes, reevaluate_history

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE_STUDY = HERE.parent / "s3_tsp_r3" / "studies" / "s3-tsp-minimax-strategy-screen-20260927-r3"
NEW_BLOCKS = tuple(range(52, 60))
SOURCE_BLOCK_FOR_NEW = {block: block - 20 for block in NEW_BLOCKS}
SOURCE_ARM = "FB_P"
PHASES = (("early", 8), ("late", 24))
STRATEGIES = ("C-I", "C-B", "C-E")
REPETITIONS = (0, 1)
BEHAVIOR_RADIUS = 0.08
QUALITY_TOLERANCE = 0.035
GAIN_EPSILON = 1e-4


def load_search_snapshot(block: int) -> dict:
    return {
        "profile": benchmarks.V12_TSP_PROFILE,
        "block": int(block),
        "probe": list(benchmarks._instances_cached("tsp", "probe", benchmarks.V12_TSP_PROFILE, int(block))),
        "validation": list(benchmarks._instances_cached("tsp", "validation", benchmarks.V12_TSP_PROFILE, int(block))),
    }


def load_test_snapshot(block: int) -> dict:
    return {
        "profile": benchmarks.V12_TSP_PROFILE,
        "block": int(block),
        "test": list(benchmarks._instances_cached("tsp", "test", benchmarks.V12_TSP_PROFILE, int(block))),
    }


def _source_path(source_study: Path, source_block: int) -> Path:
    return Path(source_study) / "runs" / f"fb_p-b{source_block}-s0" / "checkpoint.json"


def _prefix_checkpoint(source_path: Path, block: int, phase: str, step: int,
                       snapshot: dict) -> dict:
    source = read_json(source_path)
    source["e1_source_step"] = int(step)
    nodes = reevaluate_history(source, snapshot, evaluator=evaluate_search)
    prefix_nodes = nodes[:len(source.get("seeds", [])) + step]
    try:
        incumbent, branch = choose_checkpoint_nodes(
            prefix_nodes, quality_tolerance=QUALITY_TOLERANCE,
            behavior_radius=BEHAVIOR_RADIUS, gain_epsilon=GAIN_EPSILON)
        prep_status = "ready"
        prep_error = None
    except ValueError as exc:
        valid = [node for node in prefix_nodes if node.get("evaluation", {}).get("valid")]
        incumbent = min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"])) if valid else None
        branch = None
        prep_status = "preparation_incomplete"
        prep_error = {"type": type(exc).__name__, "message": str(exc)}
    checkpoint_id = f"b{block}-{phase}"
    result = {
        "schema": "chapter6-short-horizon-checkpoint-v1",
        "checkpoint_id": checkpoint_id,
        "status": "FROZEN_BEFORE_CONTINUATION" if prep_status == "ready" else prep_status,
        "source": {
            "run_id": source.get("config", {}).get("job_id", source_path.parent.name),
            "source_block": SOURCE_BLOCK_FOR_NEW[block], "source_arm": SOURCE_ARM,
            "source_step": int(step), "source_checkpoint_sha256": file_sha(source_path),
            "historical_prefix_records": int(step),
        },
        "continuation": {"block": int(block), "snapshot_sha256": digest(snapshot),
                          "profile": snapshot["profile"], "search_access": ["probe", "validation"]},
        "config": {"phase": phase, "source_step": int(step), "strategies": list(STRATEGIES),
                   "repetitions": list(REPETITIONS), "steps": 4,
                   "behavior_radius": BEHAVIOR_RADIUS, "quality_tolerance": QUALITY_TOLERANCE,
                   "gain_epsilon": GAIN_EPSILON},
        "nodes": prefix_nodes,
        "incumbent": {"id": incumbent["id"], "loss": incumbent["evaluation"]["loss"]} if incumbent else None,
        "branch": ({"id": branch["id"], "loss": branch["evaluation"]["loss"],
                    "behavior_distance": float(benchmarks.behavior_distance(
                        branch["evaluation"]["behavior"], incumbent["evaluation"]["behavior"])),
                    "quality_gap": branch["evaluation"]["loss"] - incumbent["evaluation"]["loss"]}
                   if branch and incumbent else None),
        "selection_rule": "fixed source prefix; incumbent is lowest validation loss; branch is quality-close and behavior-different; no continuation outcome is inspected",
        "selection_frozen": prep_status == "ready",
        "preparation": {"status": prep_status, "error": prep_error},
    }
    return result


def prepare_checkpoints(source_study: Path = DEFAULT_SOURCE_STUDY,
                        *, new_blocks: Iterable[int] = NEW_BLOCKS) -> list[dict]:
    checkpoints = []
    for block in new_blocks:
        block = int(block)
        source_path = _source_path(Path(source_study), SOURCE_BLOCK_FOR_NEW[block])
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        snapshot = load_search_snapshot(block)
        for phase, step in PHASES:
            checkpoints.append(_prefix_checkpoint(source_path, block, phase, step, snapshot))
    return checkpoints


def short_horizon_jobs(checkpoints: Iterable[dict], *, steps: int = 4) -> list[dict]:
    jobs = []
    for checkpoint in sorted(checkpoints, key=lambda x: x["checkpoint_id"]):
        for strategy in STRATEGIES:
            for repetition in REPETITIONS:
                block = checkpoint["continuation"]["block"]
                jobs.append({
                    "job_id": f"{checkpoint['checkpoint_id']}-{strategy.lower().replace('-', '')}-r{repetition}",
                    "checkpoint_id": checkpoint["checkpoint_id"], "strategy": strategy,
                    "repetition": int(repetition), "task": "tsp", "data_block": block,
                    "steps": int(steps), "model_calls_before_freeze": 0,
                })
    return jobs


def _old_data():
    ids, hashes = set(), set()
    for block in range(3, 52):
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                ids.add(item["id"]); hashes.add(content_hash(item))
    return ids, hashes


def check_new_data_overlap(blocks: Iterable[int] = NEW_BLOCKS) -> dict:
    old_ids, old_hashes = _old_data()
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    count = 0
    for block in blocks:
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, int(block)):
                if item["id"] in seen_ids or content_hash(item) in seen_hashes:
                    raise ValueError(f"short-horizon data overlap at {block}/{split}/{item['id']}")
                seen_ids.add(item["id"]); seen_hashes.add(content_hash(item)); count += 1
    return {"old_instances_checked": len(old_ids), "new_instances": count,
            "id_or_exact_coordinate_collisions": 0, "geometric_equivalence_checked": False}


def write_checkpoint_set(output: Path, checkpoints: Iterable[dict]) -> list[dict]:
    records = []
    for checkpoint in sorted(checkpoints, key=lambda x: x["checkpoint_id"]):
        rel = Path("checkpoints") / f"{checkpoint['checkpoint_id']}.json"
        save_json(Path(output) / rel, checkpoint, immutable=True)
        records.append({"checkpoint_id": checkpoint["checkpoint_id"], "path": rel.as_posix(),
                        "sha256": file_sha(Path(output) / rel),
                        "continuation_block": checkpoint["continuation"]["block"],
                        "phase": checkpoint["config"]["phase"]})
    return records
