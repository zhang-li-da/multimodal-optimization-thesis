"""Read original archive bytes using unique portable member names."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from zipfile import ZipFile

from .common import ROOT, file_sha, read_json

R2 = ROOT / "experiments/chapter6/v12/results/screening-20260924-r2"


def member_index(archive):
    indexed = {}
    for item in archive.infolist():
        name = item.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts or any(":" in part for part in parts):
            raise ValueError("Unsafe archive member name.")
        name = str(PurePosixPath(name))
        if name in indexed:
            raise ValueError(f"Ambiguous normalized archive member: {name}")
        indexed[name] = item
    return indexed


def read_member(archive, name):
    return archive.read(member_index(archive)[name.replace("\\", "/")])


def archived_runs(package=R2):
    package = Path(package)
    for job in read_json(package / "manifest.json")["jobs"]:
        provider = "alibaba" if job["provider"].startswith("alibaba") else "minimax"
        with ZipFile(package / f"{provider}-raw-runs.zip") as archive:
            data = read_member(archive, f"{job['job_id']}/result.json")
        yield job, json.loads(data), hashlib.sha256(data).hexdigest()


def tracked_package_files(package):
    package = Path(package).resolve()
    relative = package.relative_to(ROOT).as_posix()
    names = subprocess.check_output(["git", "ls-files", "-z", "--", relative], cwd=ROOT).decode().split("\0")
    return sorted((ROOT / name for name in names if name
                   and Path(name).name != "SHA256SUMS.txt"
                   and "__pycache__" not in Path(name).parts
                   and Path(name).suffix not in (".pyc", ".tmp", ".log")),
                  key=lambda path: path.relative_to(package).as_posix())


def write_checksums(package):
    package = Path(package).resolve()
    paths = tracked_package_files(package)
    if not paths:
        raise ValueError("Stage the intended release files before generating checksums.")
    text = "".join(f"{file_sha(path)}  {path.relative_to(package).as_posix()}\n" for path in paths)
    (package / "SHA256SUMS.txt").write_bytes(text.encode("utf-8"))
    return len(paths)


def verify_checksums(package):
    package = Path(package).resolve()
    entries = {}
    for line in (package / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, name = line.split("  ", 1)
        path = (package / name).resolve()
        if not path.is_relative_to(package) or name in entries:
            raise ValueError("Invalid checksum member.")
        entries[name] = path.is_file() and file_sha(path) == expected
    tracked = {p.relative_to(package).as_posix() for p in tracked_package_files(package)}
    return {"entries": len(entries), "matching": sum(entries.values()),
            "failed": [name for name, ok in entries.items() if not ok],
            "unlisted_tracked_files": sorted(tracked - entries.keys()),
            "listed_untracked_files": sorted(entries.keys() - tracked),
            "passed": all(entries.values()) and set(entries) == tracked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_checksums(args.package)
    report = verify_checksums(args.package)
    print(json.dumps(report))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
