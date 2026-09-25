"""Evidence preservation and truthful replay are part of the deliverable contract."""
import hashlib
import json
from zipfile import ZipFile

import pytest

from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_3.build_demo import historical, synthetic
from chapter6_demo.v12_3.package import unpack


def archive_fixture(tmp_path, tamper=False):
    path = tmp_path / "study.zip"
    files = {"study/a.json": b"original A", "study/b.json": b"original B"}
    index = {"study_path": "study", "files": {name: {
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in files.items()}}
    with ZipFile(path, "w") as archive:
        archive.writestr("ARCHIVE_INDEX.json", json.dumps(index))
        for name, data in files.items():
            archive.writestr(name, b"tampered" if tamper else data)
    return path


def test_archive_refuses_conflict_before_creating_any_file(tmp_path):
    archive = archive_fixture(tmp_path)
    destination = tmp_path / "destination"
    (destination / "study").mkdir(parents=True)
    conflict = destination / "study/b.json"
    conflict.write_bytes(b"local evidence")
    with pytest.raises(ValueError, match="Existing file differs"):
        unpack(archive, destination)
    assert conflict.read_bytes() == b"local evidence"
    assert not (destination / "study/a.json").exists()


def test_archive_bytes_replay_idempotently_but_tampering_fails(tmp_path):
    archive = archive_fixture(tmp_path)
    destination = tmp_path / "out"
    assert unpack(archive, destination)["new_files"] == 2
    assert unpack(archive, destination)["new_files"] == 0
    with ZipFile(archive, "a") as stream:
        stream.writestr("study/unlisted.json", b"extra")
    with pytest.raises(ValueError, match="members differ"):
        unpack(archive, destination)


def test_archive_digest_mismatch_fails_before_extract(tmp_path):
    archive = archive_fixture(tmp_path, tamper=True)
    with pytest.raises(ValueError, match="digest"):
        unpack(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_demo_preserves_all_runs_and_distinguishes_failed_development():
    with offline_only():
        data = historical()
    assert len(data["runs"]) == 18
    assert sum(len(r["frames"])-1 for r in data["runs"]) == 144
    default = data["runs"][data["default_index"]]
    assert default["id"] == "minimax-niche_fixed_dev-b0"
    assert max(f["corrected_success_depth"] or 0 for f in default["frames"]) == 3
    assert default["frames"][4]["event"]["branch_parent_development"]
    assert not default["frames"][4]["event"]["parent_improved"]
    assert default["frames"][4]["corrected_success_depth"] is None
    assert default["nodes"][3]["parent_diff"]


def test_synthetic_demo_does_not_invent_followup_success():
    with offline_only():
        data = synthetic()
    assert data["model_calls"] == 0
    assert [[p["selected_parent"] for p in c["policies"]] for c in data["cases"]] == [[2, 3], [2, 2], [2, 2]]
    for case in data["cases"]:
        for policy in case["policies"]:
            event = policy["observed_invalid_event"]
            assert not event["valid"] and event["branch_success_depth"] is None
            idx = policy["selected_parent"]
            before = next(b for b in policy["pool_before"] if b["node_id"] == idx)
            after = next(b for b in policy["pool_after_invalid_attempt"] if b["node_id"] == idx)
            assert after["remaining"] == before["remaining"]-1
