"""Independent MiniMax workload acceptance for the E1 runner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from chapter6_demo.discovery import SYSTEM, planner_prompt, coder_prompt
from chapter6_demo import benchmarks
from chapter6_demo.programs import Program
from chapter6_demo.v12_2.common import read_json, save_json, source_record, utcnow, environment
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.providers import parse_json

from .service import DiagnosticDurableCalls, DiagnosticHTTPTransport

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.final.json"


def run(output: Path) -> dict:
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new acceptance directory")
    protocol = read_json(PROTOCOL)
    output.mkdir(parents=True)
    config = {
        "purpose": "independent_full_workload_service_acceptance",
        "provider": protocol["model"]["provider"],
        "model": protocol["model"]["requested_model"],
        "parameters": {"temperature": protocol["generation"]["temperature"]},
        "planner_max_tokens": protocol["generation"]["planner_max_tokens"],
        "coder_max_tokens": protocol["generation"]["coder_max_tokens"],
        "timeout_seconds": protocol["generation"]["timeout_seconds"],
        "source": source_record(), "environment": environment(),
    }
    save_json(output / "config.json", config, immutable=True)
    transport = DiagnosticHTTPTransport(config["provider"], config["model"],
                                        config["timeout_seconds"], min_interval_seconds=1.0)
    calls = DiagnosticDurableCalls(output, config, transport)
    planner_completed = coder_completed = 0
    planner_responses = coder_responses = 0
    coder_executions = []
    failures = []
    for step in range(3):
        selection = {"target": "local_distance", "action": "refine", "parent": None,
                     "reference": None, "evidence": {"acceptance": True}, "recent": []}
        try:
            plan_response = calls.complete(step, "planner", SYSTEM,
                planner_prompt("tsp", selection, step), config["planner_max_tokens"])
            planner_responses += 1
            if plan_response["returned_model"] != config["model"]:
                raise ValueError("planner returned model identity mismatch")
            plan = parse_json(plan_response["text"])
            if not isinstance(plan, dict):
                raise ValueError("planner response is not an object")
            planner_completed += 1
            coder_response = calls.complete(step, "coder", SYSTEM,
                coder_prompt("tsp", plan, selection), config["coder_max_tokens"])
            coder_responses += 1
            if coder_response["returned_model"] != config["model"]:
                raise ValueError("coder returned model identity mismatch")
            coded = parse_json(coder_response["text"])
            if not isinstance(coded, dict) or not isinstance(coded.get("code"), str):
                raise ValueError("coder response has no code string")
            program = Program(coded["code"], "tsp")
            fixture = benchmarks._make_instance("tsp", "uniform", 918001, 14)
            execution = benchmarks.tsp_execute(program, fixture)
            if not execution.get("solution") or not isinstance(execution.get("loss"), (int, float)):
                raise ValueError("coder program did not execute on the bounded TSP fixture")
            coder_completed += 1
            coder_executions.append({"step": step, "program_sha256": hashlib.sha256(
                coded["code"].encode("utf-8")).hexdigest(), "valid": True,
                "fixture_id": fixture["id"], "feature_calls": execution["features_called"]})
        except (IndeterminateCall, ProviderFailure, ValueError, TypeError, KeyError) as exc:
            failures.append({"step": step, "error_type": type(exc).__name__,
                             "error": str(exc),
                             "diagnostics": getattr(exc, "diagnostics", None)})
            break
    usage = calls.usage()
    returned_models = [row.get("returned_model") for row in usage["calls"]
                       if row.get("status") == "response_persisted"]
    passed = (planner_completed == 3 and coder_completed == 3 and
              planner_responses == 3 and coder_responses == 3 and
              len(returned_models) == 6 and all(model == config["model"] for model in returned_models) and
              usage.get("usage_complete") is True)
    summary = {
        "schema": "chapter6-minimax-workload-acceptance-v1",
        "status": "passed" if passed else "failed",
        "requested_model": config["model"],
        "returned_model": config["model"] if passed else next(
            (model for model in returned_models if model != config["model"]),
            next(iter(returned_models), None)),
        "workload": {"planner_planned": 3, "coder_planned": 3,
                      "planner_responses": planner_responses,
                      "coder_responses": coder_responses,
                      "planner_completed": planner_completed,
                      "coder_completed": coder_completed,
                      "coder_bounded_executions": len(coder_executions)},
        "coder_executions": coder_executions,
        "usage": usage, "failures": failures, "new_search_runs": 0,
        "utc": utcnow(),
    }
    save_json(output / "summary.json", summary, immutable=True)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
