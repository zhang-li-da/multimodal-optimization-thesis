"""Safety and measurement checks for the separately frozen transfer readout."""
import argparse

import pytest

from chapter6_demo.benchmarks import SEEDS
from chapter6_demo.v12_2.common import read_json, save_json
from . import s3_transfer as transfer
from . import tsp_scale


def fixture(tmp_path, code):
    study, output = tmp_path / "search", tmp_path / "transfer"
    job = {"job_id": "fb_p-b32-s0", "arm_id": "FB_P", "data_block": 32}
    item = tsp_scale.instance("uniform", 1234, 6)
    item["reference"] = tsp_scale.reference(item)
    save_json(output / "data/b32-n6.json", {"instances": [item]})
    save_json(study / "runs" / job["job_id"] / "selection_frozen.json", {
        "best_id": 3, "seed_best_id": 0,
        "programs": [{"id": 3, "code": code}, {"id": 0, "code": SEEDS["tsp"][0][2]}]})
    return study, output, job


def test_invalid_transfer_program_is_kept_and_penalized(tmp_path):
    study, output, job = fixture(tmp_path, "import os")
    result = transfer.execute_one((str(study), str(output), job, 6))
    assert result["status"] == "evaluated"
    assert result["invalid_selected"] == 1
    assert result["invalid_seed"] == 0
    assert result["selected_loss"] == 1.0
    assert result["rows"][0]["selected"]["failure"] == "ProgramError"


def test_transfer_reuses_bound_result_but_rejects_changed_selection(tmp_path):
    study, output, job = fixture(tmp_path, SEEDS["tsp"][0][2])
    args = (str(study), str(output), job, 6)
    first = transfer.execute_one(args)
    assert transfer.execute_one(args) == first
    assert first["rows"][0]["selected"]["local_checks"] <= 24
    path = study / "runs" / job["job_id"] / "selection_frozen.json"
    data = read_json(path); data["best_id"] = 0; save_json(path, data)
    with pytest.raises(AssertionError):
        transfer.execute_one(args)


def test_missing_readout_is_reported(tmp_path):
    result = transfer.execute_one((str(tmp_path / "search"), str(tmp_path / "out"),
                                  {"job_id": "missing", "arm_id": "SP", "data_block": 32}, 50))
    assert result["status"] == "missing_frozen_output"
    assert result["new_model_calls"] == 0


def test_cannot_register_after_s3_test_is_open(tmp_path):
    (tmp_path / "study/tests").mkdir(parents=True)
    with pytest.raises(ValueError, match="before S3 test"):
        transfer.prepare(argparse.Namespace(study=tmp_path / "study", output=tmp_path / "transfer"))


def test_source_files_resolve_to_real_repository_files():
    assert len(transfer.sources()) == 5
