import pytest

from experiments.chapter6.agent_search.minimal_mechanism import phase_b_runner as runner
from experiments.chapter6.agent_search.component_validation.e1_runner import BudgetStop


class Calls:
    def usage(self):
        return {"call_attempts": 14, "known_tokens": 73000, "usage_complete": True}


def parameters(**overrides):
    return dict(request_limit=16, token_budget=100000, wall_limit_seconds=900,
                timeout_seconds=600, **overrides)


def test_insufficient_frozen_wall_reserve_stops_before_transport(monkeypatch):
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 895.3)
    class Transport:
        def set_wall_deadline(self, deadline):
            pytest.fail("must refuse before transport setup and dispatch")
    with pytest.raises(BudgetStop, match="wall_request_reservation"):
        runner._check_request_budget(Calls(), parameters(minimum_request_wall_seconds=30),
                                     0, "system", "prompt", 16384, Transport())


def test_old_manifest_keeps_frozen_timeout_and_acceptance_boundary(monkeypatch):
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 895.3)
    assert runner._check_request_budget(Calls(), parameters(), 0, "s", "p", 16384) == pytest.approx(4.7)
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 870)
    assert runner._check_request_budget(Calls(), parameters(minimum_request_wall_seconds=30),
                                        0, "s", "p", 16384) == 30


@pytest.mark.parametrize("value", [-1, 901, float("nan"), float("inf")])
def test_invalid_wall_policy_cannot_dispatch(value, monkeypatch):
    monkeypatch.setattr(runner.time, "perf_counter", lambda: 0)
    with pytest.raises(ValueError, match="minimum_request_wall_seconds"):
        runner._check_request_budget(Calls(), parameters(minimum_request_wall_seconds=value),
                                     0, "s", "p", 16384)
