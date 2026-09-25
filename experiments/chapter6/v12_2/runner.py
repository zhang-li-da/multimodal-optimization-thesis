"""Dedicated V121SearchState runner with replayed transitions and sealed readout."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time

from chapter6_demo.benchmarks import SEEDS, TAGS
from chapter6_demo.discovery import SYSTEM, coder_prompt, planner_prompt, report_archive
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_1_controller import V121SearchState
from .calls import DurableCalls, IndeterminateCall, ProviderFailure, no_hook
from .common import (CONTROLLER, VERSION, canonical, digest, environment, file_sha,
                     read_json, run_lock, save_json, source_record, utcnow)
from .data import evaluate_search
from .identity import identity


def plain(value):
    return json.loads(canonical(value))


def state_snapshot(state):
    return plain({"branch_pool": state.branch_pool, "branch_serial": state.branch_serial,
                  "success_depths": state.success_depths, "decision_history": state.decision_history,
                  "pending": state._pending, "rng": state.rng.getstate(),
                  "A": [n["id"] for n in state.A], "W": [n["id"] for n in state.W],
                  "M": state.M, "events": state.events, "curve": state.curve,
                  "best": state.best, "no_growth": state.no_growth, "local_credits": state.local_credits})


def validate_node(node, decision):
    expected = {"parent_id": decision["parent"]["id"] if decision["parent"] else None,
                "reference_id": decision["reference"]["id"] if decision["reference"] else None,
                "allocated_tag": decision["target"], "action": decision["action"],
                "allocation": decision["audit"]}
    if any(plain(node[key]) != plain(value) for key, value in expected.items()):
        raise ValueError("Candidate does not match its immutable selection.")
    if node["evaluation"]["program_identity"] != identity(node["code"]):
        raise ValueError("Candidate source identity mismatch.")


def restore(checkpoint):
    config = checkpoint["config"]
    if config["controller_class"] != CONTROLLER:
        raise ValueError("Wrong controller class in checkpoint.")
    state = V121SearchState("tsp", config["controller"], config["search_seed"])
    if len(checkpoint["seeds"]) != len(SEEDS["tsp"]):
        raise ValueError("Unexpected seed count.")
    for idx, (node, (name, tags, code)) in enumerate(zip(checkpoint["seeds"], SEEDS["tsp"], strict=True)):
        if (node["id"], node["name"], node["tags"], node["code"]) != (idx, name, tags, code):
            raise ValueError("Checkpoint handwritten seeds differ.")
        if node["evaluation"]["program_identity"] != identity(code):
            raise ValueError("Seed identity mismatch.")
        state.observe(copy.deepcopy(node))
    for step, record in enumerate(checkpoint["records"]):
        selection = plain(copy.deepcopy(state.choose(step)))
        if selection != record["decision"]:
            raise ValueError("Restored decision/allocation differs from saved choice.")
        validate_node(record["node"], selection)
        if record["node"]["id"] != len(state.nodes):
            raise ValueError("Nonsequential node ID.")
        state.observe(copy.deepcopy(record["node"]))
        if plain(state.events[-1]) != record["event"]:
            raise ValueError("Restored event differs from saved observation.")
    if state_snapshot(state) != checkpoint["controller_state"]:
        raise ValueError("Checkpoint pool, ancestry, decision history or RNG mismatch.")
    return state


def branch_exposure(state, selection):
    available = state._available()
    statistics = selection["audit"]["family_statistics"]
    entries = [{**copy.deepcopy(item), "family_priority": statistics[item["tag"]]["priority"],
                "family_observations": statistics[item["tag"]]["observations"],
                "family_fallback": statistics[item["tag"]]["fallback"],
                "gain_term": item["parent_gain"] / (1 + item["attempts"]),
                "selection_time_gap_to_best": item["loss"] - state.best}
               for item in available]
    full = max(available, key=lambda b: (state._score(b, statistics), -b["created_order"], -b["node_id"])) if available else None
    gain = max(available, key=lambda b: (b["parent_gain"] / (1 + b["attempts"]), -b["created_order"], -b["node_id"])) if available else None
    develop = selection["branch_development_scheduled"]
    return {"branch_slot": develop, "entries": entries,
            "multi_branch_slot": develop and len(entries) >= 2,
            "distinct_available_tags": len({item["tag"] for item in available}),
            "family_priority_varies": len({item["family_priority"] for item in entries}) >= 2,
            "full_rank_parent_id": full["node_id"] if full else None,
            "gain_only_parent_id": gain["node_id"] if gain else None,
            "full_vs_gain_choice_differs": bool(develop and full and full["node_id"] != gain["node_id"]),
            "selected_parent_gap_to_best": selection["parent"]["evaluation"]["loss"] - state.best if selection["parent"] else None}


def run_config(job, snapshot, binding, parameters, mode):
    if job["controller_class"] != CONTROLLER or job["controller"] not in ("niche_fixed_dev", "relational_branch"):
        raise ValueError("This runner requires the two V121 development policies.")
    if job["task"] != "tsp" or mode not in ("fixture", "live"):
        raise ValueError("Unsupported task or execution mode.")
    if job["data_block"] != snapshot["block"] or job["steps"] <= 0:
        raise ValueError("Snapshot block or proposal count mismatch.")
    return {"schema": VERSION, **job, "execution_mode": mode,
            "data_search_sha256": digest(snapshot), "binding": binding,
            "parameters": parameters, "source": source_record(),
            "environment": environment(), "test_access": False, "selector_enabled": False}


def run_search(job, snapshot, directory, binding, parameters, transport, *,
               mode="fixture", hook=no_hook, evaluator=None):
    """One job. Caller supplies search-only data; no test path is accepted."""
    directory = Path(directory)
    if evaluator is not None and mode != "fixture":
        raise ValueError("Live execution cannot inject a different evaluator.")
    evaluate = evaluator or evaluate_search
    config = run_config(job, snapshot, binding, parameters, mode)
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        if (directory / "search_result.json").exists():
            result = read_json(directory / "search_result.json")
            if result["config"] != plain(config):
                raise ValueError("Completed run configuration mismatch.")
            if file_sha(directory / "checkpoint.json") != result["checkpoint_sha256"]:
                raise ValueError("Completed checkpoint changed.")
            restore(read_json(directory / "checkpoint.json"))
            if file_sha(directory / "selection_frozen.json") != result["selection_frozen_sha256"]:
                raise ValueError("Completed readout changed.")
            save_json(directory / "status.json", {"status": result["status"], "completed_proposals": job["steps"]})
            return result
        start = time.perf_counter()
        cp_path = directory / "checkpoint.json"
        if cp_path.exists():
            cp = read_json(cp_path)
            if cp["config"] != plain(config):
                raise ValueError("Checkpoint configuration mismatch.")
            state = restore(cp)
        else:
            state = V121SearchState("tsp", job["controller"], job["search_seed"])
            seeds = []
            for idx, (name, tags, code) in enumerate(SEEDS["tsp"]):
                node = {"id": idx, "name": name, "intent": "shared hand-written seed: " + name,
                        "tags": tags, "code": code, "parent_id": None, "reference_id": None,
                        "action": "seed", "source": "handwritten_seed", "evaluation": evaluate(code, snapshot)}
                if not node["evaluation"]["valid"]:
                    raise ValueError("Shared seed failed evaluation; do not start model calls.")
                seeds.append(copy.deepcopy(node)); state.observe(node)
            cp = {"config": config, "seeds": seeds, "records": [],
                  "controller_state": state_snapshot(state), "elapsed_seconds": 0.0}
            save_json(cp_path, cp)
        prior_elapsed = cp["elapsed_seconds"]
        calls = DurableCalls(directory, config, transport, hook)
        for step in range(len(cp["records"]), job["steps"]):
            before_pool = copy.deepcopy(state.branch_pool)
            selection = plain(copy.deepcopy(state.choose(step)))
            exposure = branch_exposure(state, selection)
            slot = directory / "slots" / f"{step:03d}"
            save_json(slot / "decision.json", {"decision": selection, "exposure": exposure}, immutable=True)
            hook("decision_saved", step, None)
            plan = {"name": f"candidate_{step}", "intent": "unavailable", "tags": [selection["target"]]}
            code, failure = "", None
            try:
                response = calls.complete(step, "planner", SYSTEM, planner_prompt("tsp", selection, step), parameters["planner_max_tokens"])
                try:
                    parsed = parse_json(response["text"])
                    if not isinstance(parsed, dict):
                        raise ValueError("Planner must return a JSON object.")
                    plan.update(parsed)
                except (ValueError, TypeError, KeyError) as exc:
                    failure = {"kind": "proposal_parse_failure", "stage": "planner", "error_type": type(exc).__name__}
                if failure is None:
                    hook("planner_parsed", step, "planner")
                    response = calls.complete(step, "coder", SYSTEM, coder_prompt("tsp", plan, selection), parameters["coder_max_tokens"])
                    try:
                        parsed = parse_json(response["text"])
                        if not isinstance(parsed, dict) or not isinstance(parsed.get("code"), str):
                            raise ValueError("Coder must return a string code field.")
                        code = parsed["code"]
                    except (ValueError, TypeError, KeyError) as exc:
                        failure = {"kind": "proposal_parse_failure", "stage": "coder", "error_type": type(exc).__name__}
            except (IndeterminateCall, ProviderFailure) as exc:
                stopped = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                           "completed_proposals": len(cp["records"]), "reserved_step": step,
                           "usage": calls.usage(), "run_config_sha256": digest(config),
                           "resume_policy": "Recover durable responses only; never repost an indeterminate request."}
                save_json(directory / "status.json", stopped)
                return stopped
            reported_tags = plan.get("tags", [])
            tags = [tag for tag in reported_tags if tag in TAGS["tsp"]] if isinstance(reported_tags, list) else []
            base = {"id": len(state.nodes), "name": str(plan.get("name", "candidate"))[:80],
                    "intent": str(plan.get("intent", ""))[:800], "tags": tags[:2] or [selection["target"]],
                    "reported_tags": reported_tags, "allocated_tag": selection["target"],
                    "code": code, "source": "live_llm",
                    "parent_id": selection["parent"]["id"] if selection["parent"] else None,
                    "reference_id": selection["reference"]["id"] if selection["reference"] else None,
                    "action": selection["action"], "allocation": selection["audit"],
                    "generation_mode": mode, "proposal_failure": failure}
            candidate_path = slot / "candidate.json"
            if candidate_path.exists():
                node = read_json(candidate_path)
                if {k: v for k, v in node.items() if k != "evaluation"} != plain(base):
                    raise ValueError("Stored candidate metadata differs from persisted responses.")
            else:
                node = {**base, "evaluation": evaluate(code, snapshot)}
                save_json(candidate_path, node, immutable=True)
            validate_node(node, selection)
            hook("candidate_saved", step, None)
            state.observe(copy.deepcopy(node))
            record = {"decision": selection, "exposure": exposure, "node": node,
                      "event": copy.deepcopy(state.events[-1]), "pool_before": before_pool,
                      "pool_after": copy.deepcopy(state.branch_pool),
                      "tokens_after": calls.usage()["total_tokens"]}
            cp["records"].append(record)
            cp.update(controller_state=state_snapshot(state), elapsed_seconds=prior_elapsed + time.perf_counter() - start)
            save_json(cp_path, cp)
            hook("checkpoint_committed", step, None)
        # All choices below use validation. No test file has been opened.
        best = min((n for n in state.nodes if n["evaluation"]["valid"]),
                   key=lambda n: (n["evaluation"]["loss"], n["id"]))
        seed_best = min(cp["seeds"], key=lambda n: (n["evaluation"]["loss"], n["id"]))
        archive = report_archive(state.nodes, "tsp", quality_reference=seed_best["evaluation"]["loss"])
        ids = sorted({n["id"] for n in archive + cp["seeds"] + [best]})
        readout = {"run_config_sha256": digest(config), "execution_mode": mode,
                   "selected_on": "validation", "best_id": best["id"],
                   "seed_best_id": seed_best["id"], "archive_ids": [n["id"] for n in archive],
                   "programs": [{"id": n["id"], "code": n["code"], **identity(n["code"])}
                                for n in state.nodes if n["id"] in ids]}
        readout_path = directory / "selection_frozen.json"
        if readout_path.exists():
            saved = read_json(readout_path)
            if {k: v for k, v in saved.items() if k != "frozen_utc"} != readout:
                raise ValueError("Previously frozen readout differs.")
        else:
            save_json(readout_path, {**readout, "frozen_utc": utcnow()}, immutable=True)
        hook("readout_frozen", job["steps"], None)
        usage = calls.usage()
        summary = {**state.summary(), "completed_proposals": len(cp["records"]),
                   "best_validation_loss": best["evaluation"]["loss"],
                   "valid_generated": sum(r["node"]["evaluation"]["valid"] for r in cp["records"]),
                   "model_calls": usage["call_attempts"] if mode == "live" else 0,
                   "fixture_calls": usage["call_attempts"] if mode == "fixture" else 0,
                   "family_differentiated_branch_slots": sum(r["exposure"]["branch_slot"] and r["exposure"]["family_priority_varies"] for r in cp["records"]),
                   "full_vs_gain_choice_differences": sum(r["exposure"]["full_vs_gain_choice_differs"] for r in cp["records"]),
                   "evaluation_cpu_seconds": sum(n["evaluation"]["cpu_seconds"] for n in state.nodes),
                   "feature_calls": sum(n["evaluation"]["features_called"] for n in state.nodes),
                   "local_checks": sum(n["evaluation"]["local_checks"] for n in state.nodes)}
        result = {"config": config, "status": "search_complete_test_not_run",
                  "summary": summary, "usage": usage,
                  "selection_frozen_sha256": file_sha(readout_path),
                  "checkpoint_sha256": file_sha(cp_path),
                  "claim": "Engineering fixture only" if mode == "fixture" else "Fixed-proposal screening; no superiority claim"}
        save_json(directory / "search_result.json", result, immutable=True)
        save_json(directory / "status.json", {"status": result["status"], "completed_proposals": job["steps"]})
        return result
