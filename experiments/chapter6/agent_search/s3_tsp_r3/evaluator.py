"""Explicit snapshot evaluation; no global patching or environment mutation."""
from __future__ import annotations

import statistics
import time

from chapter6_demo import benchmarks
from chapter6_demo.programs import Program, ProgramError
from chapter6_demo.v12_2.common import digest
from chapter6_demo.v12_2.data import attach_identity


def evaluate(code, snapshot, *, test=False):
    expected = {"profile", "block", "test"} if test else {"profile", "block", "probe", "validation"}
    if set(snapshot) != expected:
        raise ValueError("Evaluation snapshot contains the wrong splits")
    split = "test" if test else "validation"
    start, cpu = time.perf_counter(), time.process_time()
    program = None
    attempted = checks = 0
    try:
        program = Program(code, "tsp")
        outcomes, probes = [], []
        for role, target in [(split, outcomes)] + ([] if test else [("probe", probes)]):
            for item in snapshot[role]:
                attempted += 1
                out = benchmarks.tsp_execute(program, item)
                checks += out["local_checks"]
                target.append(out)
        groups = {}
        for item, out in zip(snapshot[split], outcomes):
            groups.setdefault(item["family"], []).append(out["loss"])
        behavior_outcomes = probes or outcomes
        result = {
            "valid": True, "loss": statistics.fmean(x["loss"] for x in outcomes),
            "per_instance_loss": [x["loss"] for x in outcomes],
            "per_instance_value": [x["value"] for x in outcomes],
            "family_loss": {k: statistics.fmean(v) for k, v in groups.items()},
            "behavior": [x["behavior"] for x in behavior_outcomes],
            "trajectory_behavior": [x["trajectory_behavior"] for x in behavior_outcomes],
            "trajectory_values": [x["trajectory_values"] for x in behavior_outcomes],
            "solutions": [x["solution"] for x in outcomes],
            "program_hash": program.hash, "ast_nodes": program.ast_nodes,
            "failure_type": None,
        }
    except (ProgramError, ValueError, ArithmeticError, KeyError) as exc:
        result = {"valid": False, "loss": None, "behavior": [],
                  "trajectory_behavior": [], "failure_type": type(exc).__name__,
                  "error": str(exc)[:200]}
    result.update(split=split, features_called=program.calls if program else 0,
                  local_checks=checks, instance_evaluations=attempted,
                  wall_seconds=time.perf_counter() - start,
                  cpu_seconds=time.process_time() - cpu,
                  data_snapshot_sha256=digest(snapshot),
                  evaluated_instance_ids=[x["id"] for x in snapshot[split]],
                  probe_instance_ids=[] if test else [x["id"] for x in snapshot["probe"]])
    return attach_identity(result, code)


def evaluate_search(code, snapshot):
    return evaluate(code, snapshot)


def evaluate_test(code, snapshot):
    return evaluate(code, snapshot, test=True)
