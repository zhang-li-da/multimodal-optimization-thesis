import json
from pathlib import Path


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
