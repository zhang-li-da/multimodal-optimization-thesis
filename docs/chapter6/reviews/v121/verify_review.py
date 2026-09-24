"""Independent offline review adapter; leaves frozen experiment sources unchanged."""
from pathlib import Path
import argparse
import csv
import hashlib
import importlib.metadata
import json
import platform
import sys
import tempfile
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from chapter6_demo.v12_1 import audit_r2, verify_numeric
from chapter6_demo.v12_1_controller import v121_source_fingerprint

PUBLISHED = ROOT / "experiments/chapter6/v12_1/results/mechanism-only-20260924"


def portable_archived_runs(package=audit_r2.PACKAGE):
    """Normalize ZIP member separators only, preserving every member's bytes."""
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    for job in manifest["jobs"]:
        archive = "alibaba" if job["provider"].startswith("alibaba") else "minimax"
        with ZipFile(package / f"{archive}-raw-runs.zip") as stream:
            logical_name = f"{job['job_id']}/result.json"
            matches = [name for name in stream.namelist()
                       if name.replace("\\", "/") == logical_name]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one ZIP member for {logical_name}")
            data = stream.read(matches[0])
        yield job, json.loads(data), hashlib.sha256(data).hexdigest()


def checksums(package):
    rows = []
    for line in (package / "SHA256SUMS.txt").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        path = package / name.lstrip("*")
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        rows.append({"path": name, "expected": expected, "actual": actual,
                     "status": "missing" if actual is None else "pass" if actual == expected else "mismatch"})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New output directory; existing directories are rejected.")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output directory to preserve existing evidence.")
    args.output.mkdir(parents=True)
    source_manifest = json.loads((PUBLISHED / "manifest.json").read_text())
    summary = {"reviewed_commit": "e5d79fd18cae3ec7175a4ca5151dd4924386e060",
               "platform": platform.platform(), "python": platform.python_version(),
               "new_model_calls": 0, "adapter": "ZIP member separator normalization only",
               "frozen_controller_modified": False,
               "v121_source_fingerprint": v121_source_fingerprint(),
               "v121_source_matches_manifest": v121_source_fingerprint() == source_manifest["source_fingerprint_sha256"],
               "package_checksums": {"r2": checksums(audit_r2.PACKAGE), "v121": checksums(PUBLISHED)},
               "dependencies": {name: importlib.metadata.version(name)
                                for name in ("numpy", "scikit-learn", "matplotlib", "pytest")}}
    with audit_r2.offline_only():
        try:
            next(audit_r2.archived_runs())
            summary["unmodified_archive_reader"] = {"passed": True}
        except KeyError as exc:
            summary["unmodified_archive_reader"] = {"passed": False, "exception": repr(exc)}
        with patch.object(audit_r2, "archived_runs", portable_archived_runs), \
             patch.object(verify_numeric, "archived_runs", portable_archived_runs):
            with tempfile.TemporaryDirectory() as temp:
                result = audit_r2.audit(output=Path(temp))
                summary["recomputed_audit_equal_published"] = result == json.loads((PUBLISHED / "r2_audit.json").read_text())
                summary["archived_runs_replayed"] = result["archived_runs_replayed"]
                summary["archived_decisions_replayed"] = result["archived_decisions_replayed"]
                summary["matched_history_probe"] = result["matched_history_probe"]
                summary["audit_files_equal_published"] = {
                    path.name: path.read_bytes() == (PUBLISHED / path.name).read_bytes()
                    for path in Path(temp).iterdir()}
                summary["audit_csv_records_equal_published"] = {}
                for path in Path(temp).glob("*.csv"):
                    with path.open(encoding="utf-8", newline="") as left, \
                         (PUBLISHED / path.name).open(encoding="utf-8", newline="") as right:
                        summary["audit_csv_records_equal_published"][path.name] = list(csv.DictReader(left)) == list(csv.DictReader(right))
            snapshot = json.loads((PUBLISHED / "r2_instances.json").read_text())
            manifest = json.loads((audit_r2.PACKAGE / "manifest.json").read_text())
            summary["snapshot_split_hashes"] = [
                {"block": block, "split": split,
                 "matches_original_manifest": hashlib.sha256(json.dumps(data["instances"], sort_keys=True).encode()).hexdigest()
                     == manifest["splits"][block][split]["sha256"] == data["sha256"]}
                for block, splits in snapshot["blocks"].items() for split, data in splits.items()]
            audit_r2.write_json(args.output / "review_checks.json", summary)
            print("Archive audit complete; starting 330 program evaluations.", flush=True)
            numeric = verify_numeric.verify(audit_r2.PACKAGE, PUBLISHED / "r2_instances.json",
                                            args.output / "numeric_verification_linux.json")
    summary["numeric_replay"] = {key: numeric[key] for key in (
        "program_evaluations", "validation_programs", "test_programs", "strict_equal",
        "within_numeric_contract", "decision_errors", "passed")}
    differences = [d for row in numeric["evaluations"] for d in row["numeric_differences"]]
    summary["numeric_replay"]["differing_numeric_leaves"] = len(differences)
    summary["numeric_replay"]["max_absolute_error"] = max(
        (d["absolute_error"] for d in differences if d.get("absolute_error") is not None), default=0)
    audit_r2.write_json(args.output / "review_checks.json", summary)
    print(json.dumps(summary["numeric_replay"], ensure_ascii=False))
    if not numeric["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
