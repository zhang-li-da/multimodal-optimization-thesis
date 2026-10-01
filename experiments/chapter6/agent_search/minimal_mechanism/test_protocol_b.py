import json
from pathlib import Path

import pytest
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_study import _validate_protocol


PROTOCOL_PATH = Path(__file__).with_name("protocol.b.json")


def test_phase_b_authorization_caps_match_frozen_workload():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    stage = protocol["stages"]["B"]
    caps = stage["authorization_caps"]

    prefix = caps["public_prefix"]
    continuation = caps["continuation"]
    assert prefix == {"max_requests": 384, "max_tokens": 2_000_000}
    assert continuation == {"max_requests": 2_048, "max_tokens": 12_800_000}
    assert prefix["max_requests"] == (
        stage["public_prefix"]["jobs"]
        * stage["public_prefix"]["proposals_per_block_max"]
        * stage["requests_per_proposal_max"]
    )
    assert continuation["max_requests"] == (
        stage["continuation_jobs_planned"]
        * stage["proposals_per_job_max"]
        * stage["requests_per_proposal_max"]
    )
    assert prefix["max_tokens"] == (
        stage["public_prefix"]["jobs"] * stage["token_limit_prefix_per_job"]
    )
    assert continuation["max_tokens"] == (
        stage["continuation_jobs_planned"]
        * stage["token_limit_continuation_per_job"]
    )
    assert prefix["max_requests"] + continuation["max_requests"] == stage["max_requests"]
    assert prefix["max_tokens"] + continuation["max_tokens"] == stage["max_tokens"]


def test_c_evaluation_parts_and_total_match_matrix():
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    c = protocol["stages"]["C"]
    assert c["search_proposal_evaluation_cap"] == 48 * 64 * 48 == 147456
    assert c["shared_seed_evaluation_cap"] == 48 * 3 * 48 == 6912
    assert c["search_instance_evaluation_cap"] == 147456 + 6912
    _validate_protocol(protocol)


@pytest.mark.parametrize("field,value", [
    ("search_proposal_evaluation_cap", 49152),
    ("shared_seed_evaluation_cap", 2304),
    ("search_instance_evaluation_cap", 154367),
])
def test_c_validator_rejects_wrong_parts_even_when_other_caps_match(field, value):
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    protocol["stages"]["C"][field] = value
    with pytest.raises(ValueError, match="phase-C evaluation"):
        _validate_protocol(protocol)
