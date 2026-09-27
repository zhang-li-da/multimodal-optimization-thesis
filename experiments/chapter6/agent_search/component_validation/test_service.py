import io
import json
from urllib.error import HTTPError

import pytest

from chapter6_demo.v12_2.calls import ProviderFailure
from chapter6_demo.v12_2.common import read_json
from .service import DiagnosticDurableCalls, GlobalPauseGate, diagnose_http_error
from .service import error_category
from .study import _latest_diagnostics


def test_provider_diagnostics_retain_code_category_trace_and_retry_without_body():
    error = HTTPError(
        "https://provider.invalid", 429, "limited",
        {"x-request-id": "trace-123", "Retry-After": "17"},
        io.BytesIO(json.dumps({"error": {"code": "1002", "message": "secret"}}).encode()),
    )
    result = diagnose_http_error(error)
    assert result == {
        "http_status": 429,
        "business_code": "1002",
        "error_category": "rate_limit",
        "trace_id": "trace-123",
        "retry_after": "17",
    }
    assert "secret" not in json.dumps(result)


def test_global_pause_gate_requires_consecutive_rate_limits_but_quota_is_immediate():
    gate = GlobalPauseGate(rate_limit_threshold=2)
    assert gate.observe({"error_category": "rate_limit"})["pause"] is False
    second = gate.observe({"error_category": "rate_limit"})
    assert second["pause"] is True and second["consecutive_rate_limits"] == 2

    gate = GlobalPauseGate(rate_limit_threshold=5)
    immediate = gate.observe({"error_category": "quota_exhausted"})
    assert immediate["pause"] is True
    assert error_category(429, "1041") == "connection_limit"
    assert error_category(429, "2056") == "quota_exhausted"


class _FailingTransport:
    def send(self, request, persist):
        failure = ProviderFailure("provider failure")
        failure.diagnostics = {
            "http_status": 429, "business_code": "1002",
            "error_category": "rate_limit", "trace_id": "trace-x",
            "retry_after": "9",
        }
        raise failure


def test_durable_component_call_persists_sanitized_failure_diagnostics(tmp_path):
    config = {"provider": "fixture", "model": "fixture-model", "parameters": {"temperature": 0}}
    calls = DiagnosticDurableCalls(tmp_path, config, _FailingTransport())
    with pytest.raises(ProviderFailure):
        calls.complete(0, "planner", "system", "prompt", 32)
    state = read_json(tmp_path / "calls" / "000-planner" / "state.json")
    assert state["diagnostics"]["business_code"] == "1002"
    assert state["diagnostics"]["trace_id"] == "trace-x"
    assert "secret" not in json.dumps(state)


def test_scheduler_reads_latest_call_diagnostics_for_global_pause(tmp_path):
    state = tmp_path / "calls" / "000-planner" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"diagnostics": {"error_category": "quota_exhausted",
                                                    "business_code": "2056"}}), encoding="utf-8")
    assert _latest_diagnostics(tmp_path)["error_category"] == "quota_exhausted"
