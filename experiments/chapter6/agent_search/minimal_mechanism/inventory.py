"""Offline inventory of prior Chapter 6 batches and candidate TSP data blocks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
from pathlib import Path
import subprocess
import zipfile

from chapter6_demo import benchmarks


HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).with_name("DATA_INVENTORY.json")
SOURCE_INDEX = Path(__file__).with_name("DATA_SOURCE_SHA256.tsv")
PUBLISHED_IGNORED_ARCHIVE = Path(__file__).with_name("ignored-artifacts.zip")
SKIP_PARTS = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules"}
SENSITIVE_NAMES = {"credentials.json", "secrets.json"}
DATA_ARTIFACT_SUFFIXES = {".json", ".csv", ".zip"}
MANIFEST_NAMES = {"manifest.json", "protocol.json", "protocol.final.json", "protocol.r2.final.json",
                  "protocol.r3.final.json"}
BLOCK_LIST_KEYS = {"blocks", "source_blocks", "continuation_blocks", "block_ids",
                   "source_block_ids", "continuation_block_ids", "test_blocks"}
BLOCK_SCALAR_KEYS = {"block", "block_id", "block_index", "block_number"}
COORD_KEYS = {"points", "coordinates", "coords", "cities"}
OLD_BLOCKS = range(3, 60)
CANDIDATE_BLOCKS = range(60, 112)
SPLITS = ("probe", "validation", "test")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def digest(value) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(payload.encode("utf-8"))


def load_json_bytes(data: bytes):
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def block_claims(value, key=""):
    claims = []
    if isinstance(value, dict):
        for name, child in value.items():
            if name in BLOCK_LIST_KEYS:
                if isinstance(child, list):
                    nums = [int(x) for x in child if str(x).isdigit()]
                    if nums:
                        claims.append({"field": name, "blocks": sorted(set(nums))})
                elif isinstance(child, dict):
                    nums = [int(x) for x in child if str(x).isdigit()]
                    if nums:
                        claims.append({"field": name, "blocks": sorted(set(nums))})
                    elif (isinstance(child.get("start"), int) and isinstance(child.get("end"), int)
                          and child["end"] >= child["start"]):
                        claims.append({"field": name, "blocks": list(range(child["start"], child["end"] + 1))})
            elif name in BLOCK_SCALAR_KEYS and str(child).isdigit():
                claims.append({"field": name, "blocks": [int(child)]})
            elif (name == "block_range" and isinstance(child, list) and len(child) == 2
                  and all(str(item).isdigit() for item in child) and int(child[1]) >= int(child[0])):
                claims.append({"field": name, "blocks": list(range(int(child[0]), int(child[1]) + 1))})
            claims.extend(block_claims(child, name))
    elif isinstance(value, list):
        for child in value:
            claims.extend(block_claims(child, key))
    return claims


def coordinate_rows(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in COORD_KEYS and isinstance(child, list) and child:
                if all(isinstance(row, (list, tuple)) and len(row) == 2 and
                       all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in row)
                       for row in child):
                    points = [[float(row[0]), float(row[1])] for row in child]
                    ordered = digest(points)
                    unordered = digest(sorted((p[0], p[1]) for p in points))
                    yield {"path": f"{path}.{key}", "count": len(points),
                           "ordered_sha256": ordered, "unordered_sha256": unordered,
                           "id": value.get("id")}
            yield from coordinate_rows(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from coordinate_rows(child, f"{path}[{index}]")


def is_skipped(path: Path, root: Path) -> bool:
    try:
        parts = Path(path).resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        parts = Path(path).parts
    return any(part in SKIP_PARTS for part in parts)


def _is_package_file(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(HERE).as_posix()
        return relative not in {PUBLISHED_IGNORED_ARCHIVE.name, "ignored-artifacts.zip"}
    except ValueError:
        return False


def source_artifact_rows(root: Path):
    rows = []
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or path.suffix.lower() not in DATA_ARTIFACT_SUFFIXES
                or path.name.lower() in SENSITIVE_NAMES or is_skipped(path, root)
                or _is_package_file(path)):
            continue
        rows.append({"kind": path.suffix.lower().lstrip("."),
                     "path": path.relative_to(root).as_posix(),
                     "bytes": path.stat().st_size, "sha256": file_sha256(path)})
    return rows


def published_archive_rows(root: Path):
    """Return data rows covered by the published supplement archive."""
    if not PUBLISHED_IGNORED_ARCHIVE.is_file():
        return []
    rows = []
    with zipfile.ZipFile(PUBLISHED_IGNORED_ARCHIVE) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            raw = archive.read(member)
            rows.append({"kind": Path(member.filename).suffix.lower().lstrip("."),
                         "path": member.filename, "bytes": len(raw), "sha256": sha256(raw)})
    return rows


def source_index_bytes(rows: list[dict]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=("kind", "path", "bytes", "sha256"),
                            delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def source_index_summary(rows: list[dict], path: Path, root: Path) -> dict:
    try:
        relative_path = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative_path = path.name
    data = source_index_bytes(rows)
    return {"path": relative_path, "file_count": len(rows),
            "total_bytes": sum(row["bytes"] for row in rows),
            "sha256": sha256(data)}


def verify_source_index(path: Path = SOURCE_INDEX, root: Path = ROOT,
                        expected: dict | None = None) -> dict:
    path, root = Path(path).resolve(), Path(root).resolve()
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            recorded = [{"kind": row["kind"], "path": row["path"],
                         "bytes": int(row["bytes"]), "sha256": row["sha256"]}
                        for row in reader]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("data source hash index is missing or malformed") from exc
    current = source_artifact_rows(root)
    current_paths = {row["path"] for row in current}
    if root == ROOT.resolve() and PUBLISHED_IGNORED_ARCHIVE.is_file():
        current.extend(row for row in published_archive_rows(root)
                       if row["path"] not in current_paths)
        current.sort(key=lambda row: row["path"])
    if current != recorded:
        current_paths = {row["path"] for row in current}
        recorded_paths = {row["path"] for row in recorded}
        raise ValueError("repository data sources changed; "
                         f"added={sorted(current_paths-recorded_paths)[:20]}, "
                         f"removed={sorted(recorded_paths-current_paths)[:20]}, "
                         "or file hashes/sizes differ")
    summary = source_index_summary(recorded, path, root)
    if expected is not None and summary != expected:
        raise ValueError("data source hash index differs from the frozen inventory")
    return summary


def json_sources(root: Path):
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or path.suffix.lower() != ".json" or is_skipped(path, root)
                or path.name.lower() in SENSITIVE_NAMES or _is_package_file(path)):
            continue
        yield path.relative_to(root).as_posix(), path.read_bytes()


def zip_sources(root: Path, errors: list[dict] | None = None):
    for path in root.rglob("*.zip"):
        if is_skipped(path, root) or not path.is_file() or _is_package_file(path):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir() or not member.filename.lower().endswith(".json"):
                        continue
                    try:
                        yield (path.relative_to(root).as_posix() + "!" + member.filename,
                               archive.read(member))
                    except (OSError, zipfile.BadZipFile) as exc:
                        if errors is not None:
                            errors.append({"archive": path.relative_to(root).as_posix(),
                                           "member": member.filename,
                                           "error": type(exc).__name__})
                        continue
        except (OSError, zipfile.BadZipFile) as exc:
            if errors is not None:
                errors.append({"archive": path.relative_to(root).as_posix(),
                               "error": type(exc).__name__})
            continue


def repo_commit(root: Path):
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def source_file_records(root: Path):
    prefixes = (
        "experiments/chapter6/agent_search/minimal_mechanism/",
        "experiments/chapter6/agent_search/component_validation/",
        "experiments/chapter6/agent_search/s3_tsp_r3/",
        "experiments/chapter6/v12_2/",
    )
    exact = {
        "experiments/chapter6/demo/benchmarks.py",
        "experiments/chapter6/demo/discovery.py",
        "experiments/chapter6/demo/providers.py",
    }
    try:
        names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                              cwd=root, check=True, capture_output=True, text=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError):
        names = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and not is_skipped(p, root)]
    rows = []
    for name in names:
        rel = Path(name).as_posix()
        if rel in {
            "experiments/chapter6/agent_search/minimal_mechanism/DATA_INVENTORY.json",
            "experiments/chapter6/agent_search/minimal_mechanism/SOURCE_SHA256.json",
        }:
            continue
        if not (rel in exact or rel.startswith(prefixes)):
            continue
        path = root / rel
        if not path.is_file() or is_skipped(path, root):
            continue
        if any(part in {"results", "studies", "runs", "tests", "dispatch"} for part in Path(rel).parts):
            continue
        if path.suffix.lower() not in {".py", ".json", ".md", ".txt"}:
            continue
        rows.append({"path": rel, "bytes": path.stat().st_size, "sha256": file_sha256(path)})
    return sorted(rows, key=lambda x: x["path"])


def build_inventory(root: Path = ROOT, *, source_rows: list[dict] | None = None,
                    source_index_path: Path = SOURCE_INDEX) -> dict:
    root = Path(root).resolve()
    source_rows = source_artifact_rows(root) if source_rows is None else source_rows
    try:
        excluded_package = HERE.relative_to(root).as_posix()
    except ValueError:
        excluded_package = None
    archive_errors = []
    sources = itertools.chain(json_sources(root), zip_sources(root, archive_errors))
    manifests, study_ids, claimed_blocks = [], set(), set()
    old_coordinate_sources = {}
    old_coordinate_ids = set()
    candidate_data = {}
    scan = {"filesystem_json_files": 0, "filesystem_json_bytes": 0,
            "zip_json_members": 0, "zip_json_uncompressed_bytes": 0,
            "json_parse_failures": []}

    for source, raw in sources:
        if "!" in source:
            scan["zip_json_members"] += 1
            scan["zip_json_uncompressed_bytes"] += len(raw)
        else:
            scan["filesystem_json_files"] += 1
            scan["filesystem_json_bytes"] += len(raw)
        data = load_json_bytes(raw)
        if data is None:
            scan["json_parse_failures"].append({"source": source, "sha256": sha256(raw)})
            continue
        leaf = source.rsplit("!", 1)[-1].rsplit("/", 1)[-1].lower()
        if (leaf in MANIFEST_NAMES or "manifest" in leaf or leaf == "protocol.json"
                or leaf.startswith("protocol.")):
            claims = block_claims(data)
            study_id = data.get("study_id") if isinstance(data, dict) else None
            if study_id:
                study_ids.add(str(study_id))
            claimed_blocks.update(n for claim in claims for n in claim["blocks"])
            manifests.append({"source": source, "sha256": sha256(raw), "study_id": study_id,
                              "status": data.get("status") if isinstance(data, dict) else None,
                              "block_claims": claims})
        for row in coordinate_rows(data):
            key = (row["count"], row["ordered_sha256"])
            unordered_key = (row["count"], row["unordered_sha256"])
            record = {"source": source, **row}
            if row["id"] is not None:
                old_coordinate_ids.add(str(row["id"]))
            old_coordinate_sources.setdefault(key, record)
            old_coordinate_sources.setdefault(unordered_key, record)

    # Inspect the full deterministic TSP14 dataset generator across every prior and candidate block.
    prior_ids, prior_ordered, prior_unordered = set(), set(), set()
    per_block = {}
    for block in list(OLD_BLOCKS) + list(CANDIDATE_BLOCKS):
        rows = []
        for split in SPLITS:
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                ordered = digest(item["points"])
                unordered = digest(sorted((float(x), float(y)) for x, y in item["points"]))
                rows.append({"id": item["id"], "split": split, "ordered_sha256": ordered,
                             "unordered_sha256": unordered, "city_count": len(item["points"])})
                if block in OLD_BLOCKS:
                    prior_ids.add(item["id"])
                    prior_ordered.add((len(item["points"]), ordered))
                    prior_unordered.add((len(item["points"]), unordered))
        per_block[str(block)] = {
            "split_counts": {split: sum(row["split"] == split for row in rows) for split in SPLITS},
            "instance_count": len(rows),
            "instances": rows,
        }

    candidate_collisions = []
    candidate_ids, candidate_ordered, candidate_unordered = set(prior_ids), set(prior_ordered), set(prior_unordered)
    for block in CANDIDATE_BLOCKS:
        for item in per_block[str(block)]["instances"]:
            key1, key2 = (item["city_count"], item["ordered_sha256"]), (item["city_count"], item["unordered_sha256"])
            if item["id"] in candidate_ids or key1 in candidate_ordered or key2 in candidate_unordered:
                candidate_collisions.append({"block": block, "instance_id": item["id"],
                                            "ordered_match": key1 in candidate_ordered,
                                            "unordered_match": key2 in candidate_unordered,
                                            "id_match": item["id"] in candidate_ids})
            candidate_ids.add(item["id"]); candidate_ordered.add(key1); candidate_unordered.add(key2)

    # Candidates are also checked against every point-array JSON payload found in the tree and ZIPs.
    archive_matches = []
    for block in CANDIDATE_BLOCKS:
        for item in per_block[str(block)]["instances"]:
            key1 = (item["city_count"], item["ordered_sha256"])
            key2 = (item["city_count"], item["unordered_sha256"])
            if (item["id"] in old_coordinate_ids or key1 in old_coordinate_sources
                    or key2 in old_coordinate_sources):
                archive_matches.append({"block": block, "instance_id": item["id"],
                                        "id_match": item["id"] in old_coordinate_ids,
                                        "ordered_match": key1 in old_coordinate_sources,
                                        "unordered_match": key2 in old_coordinate_sources,
                                        "source": (old_coordinate_sources.get(key1) or
                                                   old_coordinate_sources.get(key2) or
                                                   next((record for record in old_coordinate_sources.values()
                                                         if record.get("id") == item["id"]), None))})

    zip_records = []
    for path in root.rglob("*.zip"):
        if not is_skipped(path, root) and path.is_file():
            zip_records.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                                "sha256": file_sha256(path)})
    claimed_candidate_blocks = sorted(claimed_blocks.intersection(CANDIDATE_BLOCKS))
    return {
        "schema": "chapter6-minimal-mechanism-data-inventory-v1",
        "source_commit": repo_commit(root),
        "scope": {"historical_repository_json_and_all_archive_json": True,
                  "excluded_directories": sorted(SKIP_PARTS),
                  "excluded_sensitive_basenames": sorted(SENSITIVE_NAMES),
                  "excluded_candidate_package": excluded_package,
                  "git_ignored_artifacts_included": True,
                  "hashed_artifact_extensions": sorted(DATA_ARTIFACT_SUFFIXES),
                  "test_outputs_computed": False,
                  "historical_tsp_profile": benchmarks.V12_TSP_PROFILE},
        "data_source_index": source_index_summary(source_rows, source_index_path, root),
        "scan": {**scan, "archive_errors": archive_errors,
                 "complete": not scan["json_parse_failures"] and not archive_errors},
        "source_dependency_files": source_file_records(root),
        "manifest_scan": {"count": len(manifests), "unique_study_ids": sorted(study_ids),
                          "claimed_block_ids": sorted(claimed_blocks),
                          "claimed_candidate_blocks": claimed_candidate_blocks,
                          "manifests": manifests},
        "historical_tsp14_blocks": {"range_inclusive": [min(OLD_BLOCKS), max(OLD_BLOCKS)],
                                    "instances": len(prior_ids) * 1,
                                    "id_count": len(prior_ids)},
        "candidate_blocks": {"range_inclusive": [min(CANDIDATE_BLOCKS), max(CANDIDATE_BLOCKS)],
                             "status": "offline-generated only; no evaluation results and no authorization to execute",
                             "instances": sum(per_block[str(b)]["instance_count"] for b in CANDIDATE_BLOCKS),
                             "groups": {
                                 "stage_b_diagnostic": {"blocks": list(range(60, 68)),
                                                        "status": "candidate, freeze only after stage-A acceptance"},
                                 "stage_c_development": {"blocks": list(range(68, 72)),
                                                         "status": "candidate, independent from B; freeze only after stage-B gate"},
                                 "stage_d_confirmation_pool": {"blocks": list(range(72, 112)),
                                                               "status": "reserved candidates only; select N after powered design"}},
                             "exact_coordinate_collisions_vs_blocks_3_59_and_between_candidates": candidate_collisions,
                             "exact_coordinate_collisions_vs_repository_json_and_zip_json": archive_matches},
        "all_block_data": per_block,
        "archives": zip_records,
        "inventory_sha256": None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.root.resolve()
    if root != ROOT.resolve():
        parser.error(f"--root must be the current repository root: {ROOT.resolve()}")
    rows = source_artifact_rows(root)
    existing_paths = {row["path"] for row in rows}
    rows.extend(row for row in published_archive_rows(root)
                if row["path"] not in existing_paths)
    rows.sort(key=lambda row: row["path"])
    SOURCE_INDEX.write_bytes(source_index_bytes(rows))
    inventory = build_inventory(root, source_rows=rows, source_index_path=SOURCE_INDEX)
    verify_source_index(SOURCE_INDEX, root, expected=inventory["data_source_index"])
    inventory["inventory_sha256"] = digest({k: v for k, v in inventory.items() if k != "inventory_sha256"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "manifest_count": inventory["manifest_scan"]["count"],
                      "archive_count": len(inventory["archives"]),
                      "candidate_instances": inventory["candidate_blocks"]["instances"],
                      "candidate_collisions": len(inventory["candidate_blocks"]["exact_coordinate_collisions_vs_blocks_3_59_and_between_candidates"]),
                      "repository_coordinate_matches": len(inventory["candidate_blocks"]["exact_coordinate_collisions_vs_repository_json_and_zip_json"]),
                      "inventory_sha256": inventory["inventory_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
