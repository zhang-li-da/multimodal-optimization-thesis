"""Create or verify the immutable source/data hash index for this study package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "SOURCE_SHA256.json"
SKIP_DIRS = {"__pycache__", ".pytest_cache", "runs", "dispatch", "studies", "tests"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_digest(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8"))


def canonical_source_bytes(raw: bytes) -> bytes:
    """Match GitHub/Git archive bytes for text files under core.autocrlf."""
    return raw.replace(b"\r\n", b"\n")


def source_rows() -> list[dict]:
    rows = []
    for path in HERE.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(HERE).parts):
            continue
        if path == OUTPUT or path.suffix.lower() not in {".py", ".json", ".md", ".txt", ".tsv"}:
            continue
        raw = canonical_source_bytes(path.read_bytes())
        rows.append({"path": path.relative_to(HERE).as_posix(),
                     "bytes": len(raw), "sha256": sha256(raw)})
    return sorted(rows, key=lambda row: row["path"])


def build_index() -> dict:
    protocol = json.loads((HERE / "protocol.b.json").read_text(encoding="utf-8"))
    value = {
        "schema": "chapter6-minimal-mechanism-source-sha256-v1",
        "status": protocol["status"],
        "basis_commit": protocol["basis_commit"],
        "branch": protocol["branch"],
        "files": source_rows(),
    }
    value["manifest_sha256"] = canonical_digest(value)
    return value


def verify_index(path: Path = OUTPUT) -> dict:
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected_digest = canonical_digest({key: value for key, value in saved.items()
                                        if key != "manifest_sha256"})
    if expected_digest != saved.get("manifest_sha256"):
        raise ValueError("source hash index digest mismatch")
    expected_files = source_rows()
    if expected_files != saved.get("files"):
        expected = {row["path"] for row in expected_files}
        recorded = {row["path"] for row in saved.get("files", [])}
        raise ValueError(f"source hash set differs; missing={sorted(expected-recorded)}, extra={sorted(recorded-expected)}")
    protocol = json.loads((HERE / "protocol.b.json").read_text(encoding="utf-8"))
    if saved.get("status") != protocol.get("status") or saved.get("basis_commit") != protocol.get("basis_commit"):
        raise ValueError("source hash index is bound to a different protocol status or basis commit")
    return {"status": saved["status"], "file_count": len(expected_files),
            "manifest_sha256": saved["manifest_sha256"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.write:
        value = build_index()
        OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(OUTPUT), "file_count": len(value["files"]),
                          "manifest_sha256": value["manifest_sha256"]}, indent=2))
    else:
        print(json.dumps(verify_index(), indent=2))


if __name__ == "__main__":
    main()
