"""Read-only evidence of controller decisions that differ from their intent."""
import copy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys

from chapter6_demo.discovery import SearchState
from chapter6_demo.test_science import node


def main():
    # Same terminal behavior, improving quality, same allocated strategy.
    state = SearchState("tsp", "relational", 9)
    for i, loss in enumerate((.20, .15, .10, .05)):
        item = node(i, [1, 0, 1], loss=loss, tags=["local_distance"])
        item["allocated_tag"] = "local_distance"
        state.observe(item)
    original = state.choose(3)
    # Force all other tags to have neutral, equally visited evidence so target
    # score differences remain inspectable. Do not treat a unit scenario as
    # empirical evidence of what all LLM trajectories will do.
    last = state.events[-1]
    example = {"scenario": "same probe decisions, strictly improving validation loss",
               "event_improved": last["improved"], "event_collision": last["terminal_collision"],
               "useful_gain": last["useful_gain"], "no_growth": state.no_growth,
               "next_action": original["action"], "next_target": original["target"],
               "source_anchor": "chapter6_demo/discovery.py:137-161",
               "scope": "unit-level evidence that improvement and collision coexist; not a causal efficacy test"}
    # Compare an upstream numerical formula with its own documented recurrence.
    # This reads an isolated source module; it does not establish behavior of the
    # entire SkyDiscover stack, which could add updates elsewhere.
    path = Path("chapter6_validation/artifacts/literature/skydiscover/skydiscover/optimize/search/adaevolve/adaptation.py")
    spec = spec_from_file_location("chapter6_upstream_adaptation", path)
    module = module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    obj = module.AdaptiveState(decay=.9)
    obj.record_evaluation(1.0)
    before = obj.accumulated_signal
    obj.record_evaluation(1.0)
    after = obj.accumulated_signal
    upstream = {"module": str(path), "scenario": "same score after initial improvement",
                "signal_before": before, "signal_after": after,
                "paper_ema_expected_after": .9 * before,
                "matches_paper_recurrence_in_isolated_method": abs(after - .9 * before) < 1e-12,
                "scope": "isolated module observation only; full controller calling path not executed, not a published bug claim"}
    out = Path("chapter6_validation/artifacts/implementation_checks.json")
    out.write_text(json.dumps({"frozen_controller": example, "upstream_isolated_check": upstream}, indent=2), encoding="utf-8")
    print(json.dumps({"frozen_controller": example, "upstream_isolated_check": upstream}))


if __name__ == "__main__":
    main()
