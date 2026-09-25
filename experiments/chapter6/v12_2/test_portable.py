import ast
import csv
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from chapter6_demo.v12_2.archives import R2, archived_runs, member_index, read_member, verify_checksums
from chapter6_demo.v12_2.common import ROOT, read_json
from chapter6_demo.v12_2.identity import identity
from chapter6_demo.v12_2.replay_r2 import compatibility_report


def test_published_zip_bytes_match_all_archived_run_digests():
    path = ROOT / "experiments/chapter6/v12_1/results/mechanism-only-20260924/r2_runs.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        expected = {row["job_id"]: row["result_sha256"] for row in csv.DictReader(stream)}
    runs = list(archived_runs())
    assert len(runs) == 18
    assert all(expected[job["job_id"]] == sha for job, _, sha in runs)
    assert sum(len(run["nodes"]) - 3 for _, run, _ in runs) == 144


def test_both_zip_separators_work_and_normalization_collisions_fail():
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("job_result.json", b"untouched bytes")
    # The Windows ZIP writer normalizes separators. Replace the same-length
    # name in both headers to model a valid legacy member, then reopen it.
    legacy_bytes = buffer.getvalue().replace(b"job_result.json", b"job\\result.json")
    with ZipFile(io.BytesIO(legacy_bytes)) as archive:
        assert read_member(archive, "job/result.json") == b"untouched bytes"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("job_result.json", b"untouched bytes")
        archive.writestr("job/result.json", b"different bytes")
    collision_bytes = buffer.getvalue().replace(b"job_result.json", b"job\\result.json")
    with ZipFile(io.BytesIO(collision_bytes)) as archive:
        with pytest.raises(ValueError, match="Ambiguous"):
            member_index(archive)


@pytest.mark.parametrize("name", ["../outside", "/outside", "C:/outside"] )
def test_unsafe_zip_names_are_rejected(name):
    with ZipFile(io.BytesIO(), "w") as archive:
        archive.writestr(name, b"x")
        with pytest.raises(ValueError, match="Unsafe"):
            member_index(archive)


def test_structural_identity_is_explicit_and_does_not_use_ast_dump_defaults(monkeypatch):
    code = 'def priority(f):\n    return -f["distance"] + 0.1\n'
    baseline = identity(code)
    monkeypatch.setattr(ast, "dump", lambda *args, **kwargs: "different Python display default")
    assert identity(code) == baseline
    crlf = identity(code.replace("\n", "\r\n"))
    assert crlf["raw_code_sha256"] != baseline["raw_code_sha256"]
    assert crlf["structural_sha256"] == baseline["structural_sha256"]
    assert identity(code.replace("0.1", "0.2"))["structural_sha256"] != baseline["structural_sha256"]
    assert identity("import os")["structural_sha256"] is None


def test_linux_strict_failure_remains_failure_and_327_hashes_are_explained():
    original_path = ROOT / "docs/chapter6/reviews/v121/evidence/numeric_verification_linux.json"
    before = original_path.read_bytes()
    strict = read_json(original_path)
    assert strict["passed"] is False
    report = compatibility_report(strict)
    assert report["original_strict_passed"] is False
    assert report["legacy_hash_matches"] == report["hash_evaluations"] == 327
    assert report["compatibility_passed"] is True
    assert original_path.read_bytes() == before


def test_hash_compatibility_does_not_excuse_discrete_or_numeric_errors():
    strict = read_json(ROOT / "docs/chapter6/reviews/v121/evidence/numeric_verification_linux.json")
    strict["evaluations"][0]["exact_mismatches"].append("solutions")
    assert not compatibility_report(strict)["compatibility_passed"]


def test_r2_checksum_manifest_lists_only_published_files():
    report = verify_checksums(R2)
    assert report["passed"] and report["entries"] == 23
