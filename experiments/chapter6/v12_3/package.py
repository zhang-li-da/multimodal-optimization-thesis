"""Portable immutable study archive, including unsuccessful jobs and raw responses."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from chapter6_demo.v12_2.archives import member_index
from chapter6_demo.v12_2.common import ROOT, canonical, file_sha, read_json, save_json
from chapter6_demo.v12_3.study import TERMINAL, verify


def package(study, output):
    study, output = Path(study).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Never overwrite a released archive.")
    manifest = verify(study, frozen=True)
    for job in manifest["jobs"]:
        path = study / "runs" / job["job_id"] / "status.json"
        if (not path.exists() or read_json(path)["status"] not in TERMINAL) and not (study / "dispatch/halt.json").exists():
            raise ValueError("Study is still running.")
        if path.exists() and read_json(path)["status"] == "search_complete_test_not_run" and not (study / "tests" / (job["job_id"] + ".json")).exists():
            raise ValueError("Archive the separate test readout too.")
    paths = sorted((p for p in study.rglob("*") if p.is_file() and
                    p.name != ".run.lock" and "__pycache__" not in p.parts and
                    p.suffix not in (".pyc", ".tmp")), key=lambda p: p.relative_to(ROOT).as_posix())
    files = {p.relative_to(ROOT).as_posix(): {"sha256": file_sha(p), "bytes": p.stat().st_size}
             for p in paths}
    index = {"schema": "chapter6-portable-study-archive-1", "manifest_sha256": manifest["manifest_sha256"],
             "study_path": study.relative_to(ROOT).as_posix(), "files": files,
             "claim": "Original bytes; all planned jobs are retained in manifest; no source secrets or machine auth files included."}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in [("ARCHIVE_INDEX.json", canonical(index) + b"\n")] + [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in paths]:
            info = ZipInfo(name, date_time=(2026, 9, 26, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    report = {"archive": output.name, "sha256": file_sha(output), "bytes": output.stat().st_size,
              "file_count": len(files), "uncompressed_bytes": sum(v["bytes"] for v in files.values()),
              "manifest_sha256": manifest["manifest_sha256"], "files": files}
    save_json(output.with_suffix(".index.json"), report, immutable=True)
    return report


def inspect_archive(path):
    """Verify every member before any extraction can write to a workspace."""
    with ZipFile(path) as archive:
        members = member_index(archive)
        index = json.loads(archive.read(members["ARCHIVE_INDEX.json"]))
        expected = set(index["files"]) | {"ARCHIVE_INDEX.json"}
        if set(members) != expected:
            raise ValueError("Archive members differ from the index.")
        files = {}
        for name, value in index["files"].items():
            if not name.startswith(index["study_path"] + "/"):
                raise ValueError("A member lies outside the archived study.")
            data = archive.read(members[name])
            if hashlib.sha256(data).hexdigest() != value["sha256"] or len(data) != value["bytes"]:
                raise ValueError("Archived bytes fail their digest.")
            files[name] = data
    return files


def unpack(archive, destination):
    destination = Path(destination).resolve()
    files = inspect_archive(archive)
    targets = {}
    for name, data in files.items():
        target = (destination / name).resolve()
        if not target.is_relative_to(destination):
            raise ValueError("Extraction target escapes the destination.")
        if target.exists() and target.read_bytes() != data:
            raise ValueError("Existing file differs; use a separate clean checkout or destination.")
        targets[target] = data
    # Conflicts are checked for every member before the first file is created.
    written = 0
    for target, data in targets.items():
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        written += 1
    return {"verified_files": len(files), "new_files": written, "existing_identical": len(files)-written,
            "new_model_calls": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create"); p.add_argument("--study", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("unpack"); p.add_argument("--archive", type=Path, required=True); p.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = package(args.study, args.output) if args.command == "create" else unpack(args.archive, args.destination)
    print(json.dumps({k: v for k, v in result.items() if k != "files"}))


if __name__ == "__main__":
    main()
