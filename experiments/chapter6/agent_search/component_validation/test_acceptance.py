import base64
import json

from chapter6_demo.v12_2.common import read_json

from . import acceptance


class _FixtureTransport:
    def __init__(self):
        self.requests = []

    def send(self, request, persist):
        self.requests.append({"stage": request["stage"], "max_tokens": request["max_tokens"]})
        content = (json.dumps({"name": "fixture", "intent": "acceptance",
                               "tags": ["local_distance"], "formula": "-distance"})
                   if request["stage"] == "planner" else
                   json.dumps({"code": "def priority(f):\n    return -f['distance']\n"}))
        body = {"model": request["model"], "id": f"fixture-{request['step']}-{request['stage']}",
                "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        persist({"body_base64": base64.b64encode(json.dumps(body).encode()).decode(),
                 "seconds": 0.001, "request_id": body["id"], "protocol": "openai"})


def test_acceptance_runs_three_formal_planner_coder_pairs(monkeypatch, tmp_path):
    transport = _FixtureTransport()
    monkeypatch.setattr(acceptance, "DiagnosticHTTPTransport",
                        lambda *args, **kwargs: transport)
    result = acceptance.run(tmp_path / "acceptance")

    protocol = read_json(acceptance.PROTOCOL)
    assert result["status"] == "passed"
    assert result["workload"] == {
        "planner_planned": 3, "coder_planned": 3,
        "planner_responses": 3, "coder_responses": 3,
        "planner_completed": 3, "coder_completed": 3,
        "coder_bounded_executions": 3,
    }
    assert [request["max_tokens"] for request in transport.requests] == [
        token for _ in range(3) for token in (
            protocol["generation"]["planner_max_tokens"],
            protocol["generation"]["coder_max_tokens"])
    ]
    assert len(list((tmp_path / "acceptance" / "calls").glob("*"))) == 6
    serialized = json.dumps(result)
    assert "apiKey" not in serialized and "Authorization" not in serialized
