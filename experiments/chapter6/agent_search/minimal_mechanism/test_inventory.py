import zipfile

import pytest

from experiments.chapter6.agent_search.minimal_mechanism.inventory import (
    block_claims,
    load_json_bytes,
    source_artifact_rows,
    source_index_bytes,
    verify_source_index,
)
from experiments.chapter6.agent_search.minimal_mechanism.freeze_sources import canonical_source_bytes


def test_json_inventory_parser_accepts_utf16_bom():
    assert load_json_bytes('{"status":"complete"}'.encode("utf-16")) == {
        "status": "complete"
    }


def test_block_claim_parser_handles_scalar_aliases_and_ranges():
    assert block_claims({"jobs": [{"block_id": 60}, {"block": 61}],
                         "source_blocks": {"start": 62, "end": 64}}) == [
        {"field": "block_id", "blocks": [60]},
        {"field": "block", "blocks": [61]},
        {"field": "source_blocks", "blocks": [62, 63, 64]},
    ]


def test_source_index_covers_ignored_batch_outputs_and_fails_on_later_additions(tmp_path):
    ignored_run = tmp_path / "studies" / "old-study" / "runs" / "run-1"
    ignored_run.mkdir(parents=True)
    (ignored_run / "result.json").write_text('{"block": 12}', encoding="utf-8")
    archive = tmp_path / "results" / "raw-study.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("study/manifest.json", '{"study_id":"old-study"}')

    rows = source_artifact_rows(tmp_path)
    index = tmp_path / "DATA_SOURCE_SHA256.tsv"
    index.write_bytes(source_index_bytes(rows))

    assert {row["path"] for row in rows} == {
        "results/raw-study.zip", "studies/old-study/runs/run-1/result.json"
    }
    assert verify_source_index(index, tmp_path)["file_count"] == 2

    (tmp_path / "studies" / "old-study" / "runs" / "run-2.json").write_text(
        '{"block": 13}', encoding="utf-8")
    with pytest.raises(ValueError, match="repository data sources changed"):
        verify_source_index(index, tmp_path)


def test_scan_exclusions_are_relative_to_repository_root(tmp_path):
    root = tmp_path / "venv" / "repository"
    root.mkdir(parents=True)
    (root / "batch.json").write_text('{"block": 60}', encoding="utf-8")

    assert [row["path"] for row in source_artifact_rows(root)] == ["batch.json"]


def test_source_hash_bytes_match_git_lf_normalization():
    assert canonical_source_bytes(b"first\r\nsecond\n") == b"first\nsecond\n"
