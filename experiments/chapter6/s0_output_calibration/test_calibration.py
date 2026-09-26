"""Offline-only integration and failure tests for S0 r2."""
import copy
import json
import socket
from pathlib import Path

import pytest

from chapter6_demo.providers import ModelClient
from chapter6_demo.s0_output_calibration import calibration as s0
from chapter6_demo.v12_2.calls import response_envelope
from chapter6_demo.v12_2.common import digest, read_json, save_json


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests cannot access network or credentials.")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(ModelClient, "complete", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


class FakeTransport:
    def __init__(self, malformed=None, fail=None, interrupt=None):
        self.requests = []
        self.malformed, self.fail, self.interrupt = malformed, fail, interrupt
    def send(self, request, persist):
        n = len(self.requests)
        self.requests.append(request)
        if n == self.fail:
            raise OSError("Synthetic uncertain request")
        role = request["stage"]
        payload = ({"name": f"actual_plan_{n}", "intent": "bounded update",
                    "tags": ["regret"], "formula": "-distance + 0.17*regret"} if role == "planner"
                   else {"code": 'def priority(f):\n    return -f["distance"] + 0.17*f["regret"]'})
        text = '{"code":' if n == self.malformed else json.dumps(payload)
        body = {"model": "MiniMax-M3", "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 30}}
        persist(response_envelope(json.dumps(body).encode(), seconds=.01))
        if n == self.interrupt:
            raise KeyboardInterrupt()


def study(tmp_path):
    protocol = read_json(s0.PROTOCOL)
    m = {"schema": "chapter6-s0-r2-manifest", "protocol": protocol,
         "jobs": s0.calibration_jobs(protocol) + s0.acceptance_jobs() + s0.e2e_jobs(),
         "source_commit": "offline-fixture"}
    m["manifest_sha256"] = digest(m)
    save_json(tmp_path / "manifest.json", m)
    return m


def test_denominators_and_fixtures():
    p = read_json(s0.PROTOCOL)
    calibration = s0.calibration_jobs(p)
    acceptance = s0.acceptance_jobs()
    assert len(calibration) == 36 and len(acceptance) == 36 and len(s0.e2e_jobs()) == 12
    for role in ("planner", "coder"):
        assert sum(j["role"] == role for j in acceptance) == 18
        for cfg in ("legacy_caps", "expanded_caps"):
            assert sum(j["role"] == role and j["config_id"] == cfg for j in calibration) == 9
    sets = [{digest(j["fixture"]) for j in group} for group in (calibration, acceptance, s0.e2e_jobs())]
    assert not sets[0] & sets[1] and not sets[0] & sets[2] and not sets[1] & sets[2]


@pytest.mark.parametrize("text", ["", '{"code":', '<think>{"code":"x"}'])
def test_truncated_output_does_not_crash_or_pass(text):
    assert not s0.validate_response("coder", text)["valid"]


def test_parser_separates_completion_schema_and_execution():
    plan = {"name": "a", "intent": "b", "tags": ["regret"], "formula": "-distance"}
    assert s0.validate_response("planner", json.dumps(plan))["valid"]
    assert not s0.validate_response("planner", json.dumps(plan), "length")["valid"]
    value = s0.validate_response("coder", json.dumps({"code": 'import os\ndef priority(f):\n    return 0'}))
    assert value["schema_valid"] and not value["executable"] and not value["valid"]
    assert s0.safe_code_valid('def priority(f):\n    return -f["distance"] + min(0.1, f["regret"])')[0]
    assert not s0.safe_code_valid('def priority(f):\n    while True:\n        pass')[0]
    assert not s0.safe_code_valid('def priority(f):\n    return 1 / f["nearest_remaining"]')[0]


def test_full_fixture_84_requests_real_plan_handoff_resume_without_credentials(tmp_path):
    m = study(tmp_path)
    transport = FakeTransport()
    result = s0.run(tmp_path, transport=transport, runtime=False)
    assert result["ready_for_s1"] and result["dispatched_requests"] == 84
    assert result["acceptance"]["planner"]["observed"] == 18
    for request in transport.requests[-12:][1::2]:
        assert json.loads(request["prompt"])["plan"]["name"].startswith("actual_plan_")
    assert s0.run(tmp_path, transport=transport, runtime=False) == result
    assert len(transport.requests) == 84
    summary = s0.analyze(tmp_path, tmp_path / "analysis")
    assert summary["known_tokens"] == 4200 and summary["total_requests"] == 84


@pytest.mark.parametrize("index", [0, 20, 36, 72, 73, 83])
def test_durable_resume_after_interrupt_never_resends(tmp_path, index):
    study(tmp_path)
    transport = FakeTransport(interrupt=index)
    with pytest.raises(KeyboardInterrupt):
        s0.run(tmp_path, transport=transport, runtime=False)
    transport.interrupt = None
    result = s0.run(tmp_path, transport=transport, runtime=False)
    assert result["ready_for_s1"] and len(transport.requests) == 84


@pytest.mark.parametrize("index", [0, 36, 72, 73])
def test_unknown_request_halts_without_retry(tmp_path, index):
    study(tmp_path)
    transport = FakeTransport(fail=index)
    result = s0.run(tmp_path, transport=transport, runtime=False)
    assert result["status"] == "infrastructure_halted"
    assert len(transport.requests) == index + 1
    s0.run(tmp_path, transport=transport, runtime=False)
    assert len(transport.requests) == index + 1


def test_invalid_e2e_planner_skips_coder_without_replacement(tmp_path):
    study(tmp_path)
    transport = FakeTransport(malformed=72)
    result = s0.run(tmp_path, transport=transport, runtime=False)
    assert result["status"] == "complete" and result["dispatched_requests"] == 83
    assert result["e2e_successes"] == 5


def test_selection_cannot_use_acceptance_or_task_quality():
    protocol = read_json(s0.PROTOCOL)
    rows = []
    for cfg in ("legacy_caps", "expanded_caps"):
        for role in ("planner", "coder"):
            for i in range(9):
                rows.append({"cohort": "calibration", "config_id": cfg, "role": role,
                    "valid": cfg == "expanded_caps" or i < 7, "usage_complete": True,
                    "input_tokens": 10, "output_tokens": 20})
    baseline = s0.select_config(rows, protocol)
    assert baseline["selected_config"] == "expanded_caps"
    rows += [{"cohort": "acceptance", "config_id": "legacy_caps", "role": "planner", "valid": True}] * 100
    assert s0.select_config(rows, protocol) == baseline
    assert s0.wilson(17, 18)[0] < 17/18 < s0.wilson(17, 18)[1]
