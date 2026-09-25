"""Deterministic mock provider. Its outputs are fixtures, never search evidence."""
from __future__ import annotations

import json

from .calls import response_envelope

FIXTURE_CODES = (
    'def priority(f):\n    return -f["distance"] + 0.12 * f["return_distance"]\n',
    'def priority(f):\n    return -f["distance"] + 0.10 * f["return_distance"]\n',
    'import os\ndef priority(f):\n    return 0\n',
    'def priority(f):\n    return -f["distance"] + 0.12 * f["return_distance"]\n',
    'def priority(f):\n    return -f["distance"] + 0.08 * f["return_distance"]\n',
    'def priority(f):\n    return -f["distance"] + 0.06 * f["return_distance"]\n',
    'def priority(f):\n    return -f["distance"] + 0.09 * f["regret"]\n',
    'def priority(f):\n    return -f["distance"] + 0.07 * f["regret"]\n',
)


class FakeTransport:
    def __init__(self, *, codes=FIXTURE_CODES, missing_usage=False, malformed_planner_steps=(),
                 fail_at=None, crash_after_send=False):
        self.codes, self.missing_usage = codes, missing_usage
        self.malformed_planner_steps = set(malformed_planner_steps)
        self.fail_at, self.crash_after_send = fail_at, crash_after_send
        self.requests = []

    def send(self, request, persist):
        self.requests.append(request)
        step, stage = request["step"], request["stage"]
        if self.fail_at == (step, stage):
            raise OSError("Simulated lost provider response")
        if stage == "planner":
            content = {"name": f"fixture_{step}", "intent": "deterministic engineering fixture",
                       "tags": [json.loads(request["prompt"])["target_strategy_family"]],
                       "formula": "bounded return-distance adjustment"}
            text = "malformed response fixture" if step in self.malformed_planner_steps else json.dumps(content)
        else:
            text = json.dumps({"code": self.codes[step % len(self.codes)]})
        response = {"model": "fixture-no-api", "choices": [{"message": {"content": text}}]}
        if not self.missing_usage:
            response["usage"] = {"prompt_tokens": 17, "completion_tokens": 11}
        persist(response_envelope(json.dumps(response).encode(), request_id=f"fixture-{step}-{stage}"))
