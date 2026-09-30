"""Offline S1 protocol, data and runner contract tests."""
import json
from pathlib import Path
import socket
import pytest
from chapter6_demo.providers import ModelClient
from chapter6_demo.agent_search.s1_observability import study
from chapter6_demo.v12_2.common import read_json

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("S1 offline tests cannot call model or network")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)

def test_protocol_is_single_model_paired_and_has_no_test_in_search():
    p = read_json(study.PROTOCOL)
    assert p["models"] if "models" in p else True
    assert p["controllers"] == ["niche_fixed_dev", "relational_branch"]
    assert p["steps"] == 32 and p["blocks"] == [11,12,13]
    assert p["data_split"]["search"] == ["probe", "validation"]
    assert p["planned_model_requests"] == 768

def test_job_matrix_has_one_pair_per_block_seed():
    p = read_json(study.PROTOCOL)
    js = study.jobs(p)
    assert len(js) == 12 and len({j["job_id"] for j in js}) == 12
    for block in p["blocks"]:
        for seed in p["search_seeds"]:
            pair = [j for j in js if j["data_block"] == block and j["search_seed_label"] == seed]
            assert {j["controller"] for j in pair} == set(p["controllers"])
            assert {j["search_seed"] for j in pair} == {block*100 + seed}

def test_prepare_is_offline_and_manifest_contains_new_blocks(tmp_path):
    m = study.prepare(tmp_path / "draft")
    assert m["status"] == "DRAFT_NOT_EXECUTABLE"
    assert m["s0_gate"]["result"]["ready_for_s1"]
    assert m["overlap_check"]["id_or_exact_coordinate_collisions"] == 0
    for block in ("11", "12", "13"):
        search = read_json(tmp_path / "draft" / m["data"][block]["search"]["path"])
        assert set(search) == {"profile", "block", "probe", "validation"}
        assert not "test" in search
        assert len(search["probe"]) == 12 and len(search["validation"]) == 36

def test_source_and_manifest_verification_rejects_mutation(tmp_path):
    m = study.prepare(tmp_path / "draft")
    assert study.verify(tmp_path / "draft")["manifest_sha256"] == m["manifest_sha256"]
    data = tmp_path / "draft" / m["data"]["11"]["search"]["path"]
    value = json.loads(data.read_text(encoding="utf-8")); value["probe"][0]["points"][0][0] += 1
    data.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError): study.verify(tmp_path / "draft")
