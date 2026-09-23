"""Run the unmodified pilot controller under prospective splits and a hard cap."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from chapter6_demo import discovery as original
from chapter6_demo.benchmarks import SEEDS, TAGS, behavior_distance
from chapter6_demo.providers import ModelClient, ModelError, Completion, parse_json
from .benchmarks import Benchmark, VERSION
from .budget import TokenBudget, BudgetStop
from .protocol import PROTOCOL, fixed_archive


def fingerprint():
    digest = hashlib.sha256(original.source_fingerprint().encode())
    root = Path(__file__).parent
    for name in ("benchmarks.py", "budget.py", "protocol.py", "run.py"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def call_with_journal(client, budget, directory, prompt, limit, stage, iteration):
    path = directory / f"{iteration:03d}_{stage}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["prompt"] != prompt or saved["system"] != original.SYSTEM:
            raise RuntimeError("Journal prompt changed during resume.")
        record = saved["usage"]
        budget.usage.append(record)
        response = Completion(saved["response"], record["model"], record["input_tokens"],
                              record["output_tokens"], record["seconds"], record["request_id"])
    else:
        response, record = budget.request(client, original.SYSTEM, prompt, limit, stage, iteration)
        original._save(path, {"system": original.SYSTEM, "prompt": prompt,
                              "response": response.text, "usage": record})
    if not record["usage_contract_valid"]:
        raise RuntimeError("Provider usage violated the admission contract; run is cost-invalid.")
    return response


def run(task, method, partition, provider, model, output, token_cap=60000, max_steps=60):
    benchmark = Benchmark(task, partition)
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    config = {"protocol": PROTOCOL["id"], "task": task, "method": method, "partition": partition,
              "seed": 88000 + partition, "provider": provider, "model": model,
              "token_cap": token_cap, "max_steps": max_steps, "benchmark_version": VERSION,
              "source_fingerprint": fingerprint(), "frozen_controller": original.source_fingerprint(),
              "quality_tolerance": original.QUALITY_TOLERANCE[task], "behavior_radius": original.BEHAVIOR_RADIUS,
              "splits": {s: benchmark.fingerprint(s) for s in ("probe", "validation", "test")}}
    final = directory / "result.json"
    if final.exists():
        previous = json.loads(final.read_text(encoding="utf-8"))
        if previous["config"] != config:
            raise ValueError("Completed run configuration mismatch.")
        return previous
    checkpoint_path = directory / "checkpoint.json"
    state = original.SearchState(task, method, config["seed"])
    errors, usage, prior_elapsed = [], [], 0.0
    if checkpoint_path.exists():
        saved = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if saved["config"] != config:
            raise ValueError("Checkpoint configuration mismatch.")
        for node in saved["nodes"]:
            state.observe(node)
        def tuples(obj):
            return tuple(tuples(v) for v in obj) if isinstance(obj, list) else obj
        state.rng.setstate(tuples(saved["rng_state"]))
        errors, usage = saved["errors"], saved["usage"]
        prior_elapsed = saved["elapsed_seconds"]
    else:
        for index, (name, tags, code) in enumerate(SEEDS[task]):
            state.observe({"id": index, "name": name, "intent": "shared hand-written seed: " + name,
                           "tags": tags, "code": code, "source": "handwritten_seed",
                           "parent_id": None, "reference_id": None, "action": "seed",
                           "evaluation": benchmark.evaluate(code)})
    anchor = min(n["evaluation"]["loss"] for n in state.nodes if n["source"] == "handwritten_seed")
    cutoff = anchor + original.QUALITY_TOLERANCE[task]
    budget = TokenBudget(token_cap, usage)
    client = ModelClient.from_opencode(provider, model)
    start = time.perf_counter()
    stop_reason, partial, cost_valid = "candidate_safety_cap", None, not any(e.get("usage_unknown") for e in errors)
    completed = len(state.nodes) - len(SEEDS[task])
    for step in range(completed, max_steps):
        selection = state.choose(step)
        plan_prompt = original.planner_prompt(task, selection, step)
        plan = {"name": f"candidate_{step}", "intent": "unavailable", "tags": [selection["target"]]}
        code, stage = "", "planner"
        before_used = budget.used
        try:
            response = call_with_journal(client, budget, directory, plan_prompt, 1800, "planner", step)
            generated_plan = parse_json(response.text)
            if not isinstance(generated_plan, dict):
                raise ValueError("Planner must return an object.")
            plan.update(generated_plan)
            stage = "coder"
            code_prompt = original.coder_prompt(task, plan, selection)
            response = call_with_journal(client, budget, directory, code_prompt, 2000, "coder", step)
            data = parse_json(response.text)
            code = data.get("code", "") if isinstance(data, dict) else ""
            if not isinstance(code, str):
                code = ""
            evaluation = benchmark.evaluate(code)
        except BudgetStop:
            stop_reason = "hard_token_admission"
            partial = {"iteration": step, "stopped_before": stage,
                       "charged_tokens": budget.used - before_used}
            break
        except RuntimeError as exc:
            cost_valid = False
            errors.append({"iteration": step, "stage": stage, "type": type(exc).__name__, "error": str(exc)})
            stop_reason = "usage_contract_failure"
            break
        except (ModelError, ValueError, TypeError, KeyError) as exc:
            unknown = isinstance(exc, ModelError)
            cost_valid = cost_valid and not unknown
            errors.append({"iteration": step, "stage": stage, "type": type(exc).__name__,
                           "error": str(exc)[:160], "usage_unknown": unknown})
            evaluation = benchmark.evaluate("")
            evaluation.update(failure_type=type(exc).__name__, error=str(exc)[:160])
        tags = plan.get("tags", [])
        tags = [t for t in tags if t in TAGS[task]] if isinstance(tags, list) else []
        node = {"id": len(state.nodes), "name": str(plan.get("name", "candidate"))[:80],
                "intent": str(plan.get("intent", ""))[:800], "tags": tags[:2] or [selection["target"]],
                "allocated_tag": selection["target"], "code": code, "source": "live_llm",
                "parent_id": selection["parent"]["id"] if selection["parent"] else None,
                "reference_id": selection["reference"]["id"] if selection["reference"] else None,
                "action": selection["action"], "allocation": selection["evidence"], "evaluation": evaluation,
                "iteration_tokens": budget.used - before_used}
        state.observe(node)
        state.curve[-1].update(total_tokens=budget.used, anchored_modes=len(fixed_archive(state.nodes, cutoff)))
        original._save(checkpoint_path, {"config": config, "nodes": state.nodes, "usage": budget.usage,
                       "errors": errors, "rng_state": state.rng.getstate(),
                       "elapsed_seconds": prior_elapsed + time.perf_counter() - start})
        print(json.dumps({"task": task, "method": method, "partition": partition, "step": step + 1,
                          "tokens": budget.used, "cap": token_cap, "valid": evaluation["valid"]}), flush=True)

    # All output selection is complete before any final-test evaluation.
    archive = fixed_archive(state.nodes, cutoff)
    dynamic = original.report_archive(state.nodes, task)
    best = state._simple_parent()
    seed_nodes = [n for n in state.nodes if n["source"] == "handwritten_seed"]
    selected = {n["id"]: n for n in archive + dynamic + seed_nodes + ([best] if best else [])}
    test = {str(idx): benchmark.evaluate(node["code"], split="test", with_probes=False)
            for idx, node in selected.items()}
    test_anchor = min(test[str(n["id"])]["loss"] for n in seed_nodes)
    test_cutoff = test_anchor + original.QUALITY_TOLERANCE[task]
    test_modes = []
    for node in sorted(archive, key=lambda n: (test[str(n["id"])]["loss"] if test[str(n["id"])]["valid"] else float("inf"), n["id"])):
        ev = test[str(node["id"])]
        if ev["valid"] and ev["loss"] <= test_cutoff:
            if not test_modes or min(behavior_distance(ev["behavior"], test[str(p)]["behavior"]) for p in test_modes) > original.BEHAVIOR_RADIUS:
                test_modes.append(node["id"])
    generated = [n for n in state.nodes if n["source"] == "live_llm"]
    events = state.events[len(seed_nodes):]
    total_tokens = budget.used
    summary = {"generated": len(generated), "valid_generated": sum(n["evaluation"]["valid"] for n in generated),
               "best_validation_loss": state.best, "validation_selected_best_id": best["id"],
               "validation_selected_test_loss": test[str(best["id"])]["loss"],
               "anchored_validation_modes": len(archive), "anchored_test_modes": len(test_modes),
               "dynamic_validation_modes": len(dynamic), "validation_anchor": anchor, "test_anchor": test_anchor,
               "terminal_collision_rate": sum(e["terminal_collision"] for e in events) / max(1, len(events)),
               "different_intent_collisions": sum(e["different_intent_collision"] for e in events),
               "useful_generated": sum(e["useful_gain"] for e in events),
               "input_tokens": sum(u["input_tokens"] for u in budget.usage),
               "output_tokens": sum(u["output_tokens"] for u in budget.usage),
               "total_tokens": total_tokens, "model_calls": len(budget.usage),
               "budget_utilization": total_tokens / token_cap, "cost_valid": cost_valid,
               "model_seconds": sum(u["seconds"] for u in budget.usage),
               "evaluator_cpu_seconds": sum(n["evaluation"]["cpu_seconds"] for n in state.nodes),
               "test_cpu_seconds": sum(e["cpu_seconds"] for e in test.values()),
               "feature_calls": sum(n["evaluation"]["features_called"] for n in state.nodes),
               "local_checks": sum(n["evaluation"]["local_checks"] for n in state.nodes),
               "elapsed_seconds": prior_elapsed + time.perf_counter() - start}
    result = {"config": config, "summary": summary, "nodes": state.nodes, "events": state.events,
              "usage": budget.usage, "errors": errors, "curve": state.curve, "test": test,
              "archive_ids": [n["id"] for n in archive], "dynamic_archive_ids": [n["id"] for n in dynamic],
              "test_mode_ids": test_modes, "working_ids": [n["id"] for n in state.W],
              "stop_reason": stop_reason, "partial_iteration": partial,
              "claims": {"scope": "frozen method prospective hard-budget replication",
                         "not_reproduced": ["MLEvolve", "SeaEvo", "AdaEvolve"]}}
    original._save(final, result)
    programs = directory / "programs"
    programs.mkdir(exist_ok=True)
    for node in selected.values():
        (programs / f"candidate_{node['id']:03d}.py").write_text(node["code"], encoding="utf-8")
    print(json.dumps({"finished": str(directory), "summary": summary}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=TAGS, required=True)
    parser.add_argument("--method", choices=original.METHODS, required=True)
    parser.add_argument("--partition", type=int, required=True)
    parser.add_argument("--provider", default="alibaba-token-plan-cn")
    parser.add_argument("--model", default="qwen3.7-plus")
    parser.add_argument("--token-cap", type=int, default=60000)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.task, args.method, args.partition, args.provider, args.model, args.output, args.token_cap, args.max_steps)


if __name__ == "__main__":
    main()
