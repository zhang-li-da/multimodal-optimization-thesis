"""Split files, immutable study descriptions and test-denying search evaluator."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import random
from unittest.mock import patch

from chapter6_demo import benchmarks
from .common import (CONTROLLER, ROOT, VERSION, digest, environment, file_sha, git,
                     read_json, save_json, source_record, utcnow)
from .identity import FORMAT, identity

DRAFT = ROOT / "experiments/chapter6/v12_2/protocol.draft.json"
HISTORICAL = ROOT / "experiments/chapter6/v12_1/results/mechanism-only-20260924/r2_instances.json"


def planned_jobs(protocol=None):
    protocol = protocol or read_json(DRAFT)
    jobs = [{"job_id": f"{provider.split('-')[0]}-{controller}-b{block}",
             "provider": provider, "model": model, "controller": controller,
             "controller_class": CONTROLLER, "task": "tsp",
             "data_block": block, "search_seed": block, "steps": protocol["steps"],
             "token_budget": None}
            for provider, model in protocol["models"]
            for controller in protocol["controllers"] for block in protocol["blocks"]]
    random.Random(protocol["order_seed"]).shuffle(jobs)
    return jobs


def content_hash(instance):
    # Ignore labels/IDs when checking exact coordinate duplication. This is
    # not geometric-isomorphism or distribution-independence certification.
    return digest(instance["points"])


def prepare(directory):
    directory = Path(directory).resolve()
    if directory.exists():
        raise ValueError("Prepare requires a new directory.")
    protocol = read_json(DRAFT)
    old = [item for block in read_json(HISTORICAL)["blocks"].values()
           for split in block.values() for item in split["instances"]]
    known_ids, known_content = {x["id"] for x in old}, {content_hash(x) for x in old}
    directory.mkdir(parents=True)
    files, split_hashes = {}, {}
    count = 0
    for block in protocol["blocks"]:
        splits = {split: copy.deepcopy(benchmarks._instances_cached(
            "tsp", split, benchmarks.V12_TSP_PROFILE, block))
                  for split in ("probe", "validation", "test")}
        for split, items in splits.items():
            for item in items:
                hashed = content_hash(item)
                if item["id"] in known_ids or hashed in known_content:
                    raise ValueError("New/old or between-split instance overlap.")
                known_ids.add(item["id"]); known_content.add(hashed)
                count += 1
        split_hashes[str(block)] = {split: {"count": len(items), "sha256": digest(items)}
                                    for split, items in splits.items()}
        search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                  "probe": splits["probe"], "validation": splits["validation"]}
        test = {"profile": benchmarks.V12_TSP_PROFILE, "block": block, "test": splits["test"]}
        files[str(block)] = {}
        for role, value in (("search", search), ("test", test)):
            name = f"data/{role}-b{block}.json"
            save_json(directory / name, value, immutable=True)
            files[str(block)][role] = {"path": name, "sha256": file_sha(directory / name)}
    manifest = {"schema": VERSION, "status": "DRAFT_NOT_EXECUTABLE", "created_utc": utcnow(),
                "source_commit": git("rev-parse", "HEAD"), "source": source_record(),
                "environment": environment(), "protocol": protocol,
                "data": files, "splits": split_hashes, "jobs": planned_jobs(protocol),
                "program_hash_format": FORMAT, "new_model_calls": 0,
                "overlap_check": {"old_instances": len(old), "new_instances": count,
                                  "id_or_exact_coordinate_collisions": 0,
                                  "geometric_equivalence_checked": False}}
    manifest["manifest_sha256"] = digest(manifest)
    save_json(directory / "manifest.json", manifest, immutable=True)
    return manifest


def verify_manifest(directory):
    manifest = read_json(Path(directory) / "manifest.json")
    payload = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("schema") != VERSION or digest(payload) != manifest["manifest_sha256"]:
        raise ValueError("Study manifest fingerprint mismatch.")
    if manifest["source"] != source_record():
        raise ValueError("Study source differs; create a new study version.")
    if manifest["jobs"] != planned_jobs(manifest["protocol"]):
        raise ValueError("Study job matrix differs from its protocol.")
    return manifest


def split_path(directory, manifest, block, role):
    directory = Path(directory).resolve()
    record = manifest["data"][str(block)][role]
    path = (directory / record["path"]).resolve()
    if not path.is_relative_to(directory) or file_sha(path) != record["sha256"]:
        raise ValueError("Snapshot path or bytes do not match the study.")
    return path


def freeze(draft_directory, directory, protocol_path):
    """Future explicit freeze. This command sends no network/model requests."""
    draft_directory, directory, protocol_path = map(Path, (draft_directory, directory, protocol_path))
    if directory.exists() or git("status", "--porcelain"):
        raise ValueError("Freeze needs a clean committed source and a new output directory.")
    protocol_path = protocol_path.resolve()
    git("ls-files", "--error-unmatch", protocol_path.relative_to(ROOT).as_posix())
    protocol = read_json(protocol_path)
    if protocol.get("status") != "FINAL_PROTOCOL_PENDING_EXECUTION":
        raise ValueError("A draft protocol cannot authorize a frozen study.")
    for key in ("minimum_practical_gain_percentage_points", "maximum_acceptable_token_increase_fraction"):
        if not isinstance(protocol.get(key), (int, float)) or protocol[key] < 0:
            raise ValueError("Finalize the practical decision thresholds before freezing.")
    draft = verify_manifest(draft_directory)
    allowed_changes = {"status", "minimum_practical_gain_percentage_points",
                       "maximum_acceptable_token_increase_fraction", "provider_immutable_versions"}
    if {k: v for k, v in protocol.items() if k not in allowed_changes} != {
            k: v for k, v in draft["protocol"].items() if k not in allowed_changes}:
        raise ValueError("Execution design changed; prepare and test a new version.")
    # Snapshots are copied as bytes, never regenerated at freeze or resume.
    for block in draft["data"]:
        for role in ("search", "test"):
            source = split_path(draft_directory, draft, block, role)
            target = directory / draft["data"][block][role]["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    manifest = {**draft, "status": "FROZEN_PENDING_EXECUTION", "protocol": protocol,
                "created_utc": utcnow(), "source_commit": git("rev-parse", "HEAD"),
                "protocol_file": {"path": protocol_path.relative_to(ROOT).as_posix(),
                                  "sha256": file_sha(protocol_path)}}
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = digest(manifest)
    save_json(directory / "manifest.json", manifest, immutable=True)
    return manifest


@contextmanager
def snapshot_evaluator(snapshot, allowed_splits):
    """No generation fallback; only the explicitly supplied splits can load."""
    allowed_splits = frozenset(allowed_splits)
    def load(task, split):
        if task != "tsp" or split not in allowed_splits:
            raise ValueError("This evaluation phase cannot access that split.")
        return snapshot[split]
    with patch.object(benchmarks, "instances", load), patch.dict(os.environ, {
            "CHAPTER6_BENCHMARK_PROFILE": benchmarks.V12_TSP_PROFILE,
            "CHAPTER6_DATA_BLOCK": str(snapshot["block"])}):
        yield


def evaluate_search(code, snapshot):
    if set(snapshot) != {"profile", "block", "probe", "validation"}:
        raise ValueError("Search snapshot must contain only probe and validation.")
    with snapshot_evaluator(snapshot, ("probe", "validation")):
        result = benchmarks.evaluate(code, "tsp")
    return attach_identity(result, code)


def evaluate_test(code, snapshot):
    if set(snapshot) != {"profile", "block", "test"}:
        raise ValueError("Test snapshot must contain only test instances.")
    with snapshot_evaluator(snapshot, ("test",)):
        result = benchmarks.evaluate(code, "tsp", split="test", with_probes=False)
    return attach_identity(result, code)


def attach_identity(result, code):
    # Retain the old value as provenance; the new primary identity is explicit.
    result["legacy_runtime_program_hash"] = result.pop("program_hash", None)
    result["program_identity"] = identity(code)
    result["program_hash"] = result["program_identity"]["structural_sha256"]
    return result
