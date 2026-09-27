"""Offline construction of the E1 same-state continuation checkpoints.

E1 deliberately starts from immutable S3 histories, but evaluates every
historical program on a *new* block before a checkpoint is frozen.  The
continuation runner consumes the resulting checkpoint and never reads test
instances.  This module performs no model calls.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable, Iterable

from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json

from ..s3_tsp_r3.evaluator import evaluate_search


HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE_STUDY = (
    HERE.parent / "s3_tsp_r3" / "studies" /
    "s3-tsp-minimax-strategy-screen-20260927-r3"
)
NEW_BLOCKS = (44, 45, 46, 47)
# The protocol has eight checkpoints.  The mapping is frozen here instead of
# selecting a convenient successful run after looking at continuation results.
SOURCE_BLOCK_FOR_NEW = {44: 32, 45: 33, 46: 34, 47: 35}
SOURCE_ARMS = ("SP", "FB_P")
SOURCE_STEP = 16
STRATEGIES = ("C-I", "C-B", "C-E")
REPETITIONS = (0, 1)
CONTINUATION_STEPS = 4
BEHAVIOR_RADIUS = 0.08
QUALITY_TOLERANCE = 0.035
GAIN_EPSILON = 1e-4


def continuation_jobs(checkpoints: Iterable[dict], *, steps: int = CONTINUATION_STEPS,
                       repetitions: Iterable[int] = REPETITIONS) -> list[dict]:
    """Return the immutable 48-job E1 matrix (8 checkpoints x 3 x 2)."""
    jobs = []
    for checkpoint in sorted(checkpoints, key=lambda value: value["checkpoint_id"]):
        cid = checkpoint["checkpoint_id"]
        block = checkpoint["continuation"]["block"]
        for strategy in STRATEGIES:
            for repetition in repetitions:
                jobs.append({
                    "job_id": f"{cid}-{strategy.lower().replace('-', '')}-r{repetition}",
                    "checkpoint_id": cid,
                    "strategy": strategy,
                    "repetition": int(repetition),
                    "task": "tsp",
                    "data_block": block,
                    "steps": int(steps),
                    "model_calls_before_freeze": 0,
                })
    return jobs


def load_search_snapshot(block: int) -> dict:
    """Load only the probe/validation snapshot needed to construct E1."""
    return {
        "profile": benchmarks.V12_TSP_PROFILE,
        "block": int(block),
        "probe": list(benchmarks._instances_cached(
            "tsp", "probe", benchmarks.V12_TSP_PROFILE, int(block))),
        "validation": list(benchmarks._instances_cached(
            "tsp", "validation", benchmarks.V12_TSP_PROFILE, int(block))),
    }


def _source_nodes(source_checkpoint: dict) -> list[dict]:
    """Return seeds plus the immutable prefix ending at the chosen step."""
    records = source_checkpoint.get("records", [])
    step = int(source_checkpoint["e1_source_step"])
    if step < 0 or step > len(records):
        raise ValueError("source checkpoint step is outside its history")
    nodes = copy.deepcopy(source_checkpoint.get("seeds", []))
    nodes.extend(copy.deepcopy(record["node"]) for record in records[:step])
    ids = [node["id"] for node in nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("source checkpoint contains duplicate node ids")
    return nodes


def reevaluate_history(source_checkpoint: dict, snapshot: dict,
                       evaluator: Callable[[str, dict], dict] = evaluate_search) -> list[dict]:
    """Re-evaluate each source-prefix program on the new block.

    Source decisions and costs remain historical evidence; their evaluation is
    replaced with the explicit new-block result so branch selection is based on
    the continuation data rather than the old S3 validation split.
    """
    nodes = _source_nodes(source_checkpoint)
    output = []
    for source_node in nodes:
        node = copy.deepcopy(source_node)
        node["evaluation"] = evaluator(node.get("code", ""), snapshot)
        node["source_evaluation"] = copy.deepcopy(source_node.get("evaluation", {}))
        node["evaluation_basis"] = {
            "profile": snapshot["profile"], "block": snapshot["block"],
            "splits": ["probe", "validation"],
        }
        output.append(node)
    return output


def _behavior_distance(left: dict, right: dict) -> float:
    lb = left.get("evaluation", {}).get("behavior")
    rb = right.get("evaluation", {}).get("behavior")
    if not lb or not rb:
        return 0.0
    return float(benchmarks.behavior_distance(lb, rb))


def choose_checkpoint_nodes(nodes: list[dict], *, quality_tolerance: float = QUALITY_TOLERANCE,
                           behavior_radius: float = BEHAVIOR_RADIUS,
                           gain_epsilon: float = GAIN_EPSILON) -> tuple[dict, dict]:
    """Choose incumbent and a quality-close, behavior-different branch.

    The branch is selected only from the frozen prefix.  A missing eligible
    branch is an explicit preparation failure rather than silently substituting
    a later or test-informed candidate.
    """
    valid = [node for node in nodes if node.get("evaluation", {}).get("valid")]
    if not valid:
        raise ValueError("new-block checkpoint has no valid historical program")
    incumbent = min(valid, key=lambda node: (node["evaluation"]["loss"], node["id"]))
    best_loss = incumbent["evaluation"]["loss"]
    candidates = []
    for node in valid:
        loss = node["evaluation"]["loss"]
        distance = _behavior_distance(node, incumbent)
        if (loss > best_loss + gain_epsilon and
                loss <= best_loss + quality_tolerance and
                distance > behavior_radius):
            candidates.append((loss - best_loss, -distance, node["id"], node))
    if not candidates:
        raise ValueError("checkpoint has no quality-close behavior-different branch")
    branch = min(candidates, key=lambda value: value[:3])[3]
    return incumbent, branch


def build_checkpoint(source_run: Path, *, continuation_block: int,
                     source_block: int, source_arm: str,
                     source_step: int = SOURCE_STEP,
                     snapshot: dict | None = None) -> dict:
    """Build one frozen E1 checkpoint without contacting a model provider."""
    source_run = Path(source_run).resolve()
    source_path = source_run / "checkpoint.json"
    source = read_json(source_path)
    source["e1_source_step"] = int(source_step)
    continuation_snapshot = snapshot or load_search_snapshot(continuation_block)
    if continuation_snapshot["block"] != continuation_block:
        raise ValueError("continuation snapshot block mismatch")
    nodes = reevaluate_history(source, continuation_snapshot)
    incumbent, branch = choose_checkpoint_nodes(nodes)
    records = copy.deepcopy(source.get("records", [])[:source_step])
    checkpoint_id = f"b{continuation_block}-{source_arm.lower()}-s{source_step}"
    return {
        "schema": "chapter6-e1-same-state-checkpoint-v1",
        "checkpoint_id": checkpoint_id,
        "status": "FROZEN_BEFORE_CONTINUATION",
        "source": {
            "run_id": source.get("config", {}).get("job_id", source_run.name),
            "source_block": int(source_block), "source_arm": source_arm,
            "source_step": int(source_step),
            "source_checkpoint_sha256": file_sha(source_path),
            "historical_prefix_records": len(records),
        },
        "continuation": {
            "block": int(continuation_block),
            "snapshot_sha256": digest(continuation_snapshot),
            "profile": continuation_snapshot["profile"],
            "search_access": ["probe", "validation"],
        },
        "config": {
            "strategies": list(STRATEGIES), "repetitions": list(REPETITIONS),
            "steps": CONTINUATION_STEPS, "behavior_radius": BEHAVIOR_RADIUS,
            "quality_tolerance": QUALITY_TOLERANCE, "gain_epsilon": GAIN_EPSILON,
        },
        "nodes": nodes,
        "source_records": records,
        "incumbent": {"id": incumbent["id"], "loss": incumbent["evaluation"]["loss"]},
        "branch": {
            "id": branch["id"], "loss": branch["evaluation"]["loss"],
            "behavior_distance": _behavior_distance(branch, incumbent),
            "quality_gap": branch["evaluation"]["loss"] - incumbent["evaluation"]["loss"],
        },
        "selection_rule": (
            "prefix step is fixed before continuation; incumbent is the lowest new-block "
            "validation loss; branch is closest quality-eligible node with behavior distance "
            "> behavior_radius; no continuation outcome is inspected"
        ),
        "selection_frozen": True,
    }


def source_run_path(source_study: Path, source_block: int, source_arm: str) -> Path:
    return Path(source_study) / "runs" / f"{source_arm.lower()}-b{source_block}-s0"


def prepare_checkpoints(source_study: Path = DEFAULT_SOURCE_STUDY,
                        *, new_blocks: Iterable[int] = NEW_BLOCKS,
                        source_arms: Iterable[str] = SOURCE_ARMS,
                        source_step: int = SOURCE_STEP) -> list[dict]:
    """Prepare the eight deterministic checkpoints for offline inspection."""
    checkpoints = []
    for continuation_block in new_blocks:
        if continuation_block not in SOURCE_BLOCK_FOR_NEW:
            raise ValueError(f"no frozen source-block mapping for {continuation_block}")
        source_block = SOURCE_BLOCK_FOR_NEW[continuation_block]
        snapshot = load_search_snapshot(continuation_block)
        for source_arm in source_arms:
            run = source_run_path(source_study, source_block, source_arm)
            checkpoints.append(build_checkpoint(
                run, continuation_block=continuation_block,
                source_block=source_block, source_arm=source_arm,
                source_step=source_step, snapshot=snapshot))
    if len(checkpoints) != 8:
        raise ValueError("E1 requires exactly eight frozen checkpoints")
    return checkpoints


def write_checkpoint_set(output: Path, checkpoints: Iterable[dict]) -> list[dict]:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for checkpoint in sorted(checkpoints, key=lambda value: value["checkpoint_id"]):
        rel = Path("checkpoints") / f"{checkpoint['checkpoint_id']}.json"
        save_json(output / rel, checkpoint, immutable=True)
        records.append({"checkpoint_id": checkpoint["checkpoint_id"],
                        "path": rel.as_posix(), "sha256": file_sha(output / rel),
                        "continuation_block": checkpoint["continuation"]["block"]})
    return records

