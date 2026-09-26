"""Offline S0 acceptance tests; no provider or network calls."""
import ast
import copy
import socket

import pytest

from chapter6_demo.providers import ModelClient
from chapter6_demo.s0_output_calibration.calibration import (
    ACCEPTANCE_TEMPLATES, CALIBRATION_TEMPLATES, CODER_PLANS, gates_pass,
    planner_valid, safe_code_valid, select_config, validate_response, wilson,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("S0 unit tests cannot call a model or network.")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(ModelClient, "complete", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_three_templates_and_frozen_coder_plans_are_distinct():
    assert set(CALIBRATION_TEMPLATES) == set(ACCEPTANCE_TEMPLATES) == set(CODER_PLANS)
    assert len({item["task"] for item in CALIBRATION_TEMPLATES.values()}) == 3
    assert len({item["intent"] for item in CODER_PLANS.values()}) == 3


def test_planner_parser_accepts_schema_and_rejects_thought_only_or_unknown_tag():
    text = '{"name":"r","intent":"bounded change","tags":["regret"],"modifications":["add regret"]}'
    assert planner_valid(text) == (True, "valid")
    assert planner_valid("<think>reasoning</think>") == (False, "ValueError")
    assert planner_valid('{"name":"r","intent":"x","tags":["made_up"],"modifications":["x"]}')[0] is False


def test_code_contract_is_bounded_and_executable():
    assert safe_code_valid('def priority(f):\n    return -f["distance"] + 0.1 * f["regret"]') == (True, "executable")
    assert safe_code_valid('import os\ndef priority(f):\n    return 0') == (False, "unsafe_ast")
    assert safe_code_valid('def priority(f):\n    return __import__("os").getcwd()') == (False, "unsafe_ast")
    assert safe_code_valid('def priority(f):\n    while True:\n        pass') == (False, "unsafe_ast")


def test_response_validation_consumes_persisted_dict_text_once():
    assert validate_response(
        "planner",
        {"text": '{"name":"r","intent":"x","tags":["regret"],"modifications":["x"]}'}["text"],
    ) == (True, "valid")
    assert validate_response("coder", '{"code":"def priority(f):\\n    return -f[\\"distance\\"]"}') == (True, "executable")
    assert validate_response("planner", "", {"type": "ProviderFailure"}) == (False, "ProviderFailure")


def test_wilson_and_config_selection_do_not_use_task_quality():
    assert wilson(17, 18)[0] < 17/18 < wilson(17, 18)[1]
    outcomes = []
    for config in ("legacy_caps", "expanded_caps"):
        for cohort in ("calibration", "acceptance"):
            for role in ("planner", "coder"):
                total = 18
                valid = 17 if (config == "expanded_caps" and cohort == "acceptance") else 0
                for i in range(total):
                    outcomes.append({"config_id": config, "cohort": cohort, "role": role, "valid": i < valid, "input_tokens": 1, "output_tokens": 1})
    assert select_config(outcomes, {"configs":[{"id":"legacy_caps"},{"id":"expanded_caps"}]}) == "expanded_caps"
    assert gates_pass(outcomes)
