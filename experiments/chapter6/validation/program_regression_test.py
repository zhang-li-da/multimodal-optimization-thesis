"""Check a concrete 'probe collision plus useful improvement' from saved pilot logs."""
import json
from pathlib import Path

import pytest

from chapter6_demo.benchmarks import behavior_distance, evaluate
from chapter6_demo.discovery import BEHAVIOR_RADIUS


def test_recorded_behavior_collision_can_still_improve_validation_quality():
    # Select by a fixed factual predicate in the historical pilot. This is an
    # existence counterexample, not a prevalence or efficacy hypothesis test.
    roots = Path("chapter6_demo/runs/pilot_qwen")
    found = None
    for path in sorted(roots.glob("tsp_niche_*/result.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        for event in result["events"]:
            if event["improved"] and event["terminal_collision"]:
                found = result, event
                break
        if found:
            break
    assert found is not None
    result, event = found
    nodes = {n["id"]: n for n in result["nodes"]}
    candidate = nodes[event["node_id"]]
    nearest = nodes[event["nearest_node"]]
    first, second = evaluate(candidate["code"], "tsp"), evaluate(nearest["code"], "tsp")
    assert first["valid"] and second["valid"]
    assert behavior_distance(first["behavior"], second["behavior"]) <= BEHAVIOR_RADIUS
    assert first["loss"] < min(n["evaluation"]["loss"] for n in result["nodes"]
                               if n["id"] < candidate["id"] and n["evaluation"]["valid"])
