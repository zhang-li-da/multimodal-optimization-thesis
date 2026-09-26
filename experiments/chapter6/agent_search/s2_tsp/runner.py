"""Durable live runner for S2 protection and scheduling experiments."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time

from chapter6_demo.benchmarks import SEEDS
from chapter6_demo.discovery import SYSTEM, coder_prompt, planner_prompt, report_archive
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_2.calls import DurableCalls, IndeterminateCall, ProviderFailure, no_hook
from chapter6_demo.v12_2.common import canonical, digest, environment, file_sha, read_json, run_lock, save_json, source_record, utcnow
from chapter6_demo.v12_2.data import evaluate_search
from chapter6_demo.v12_2.identity import identity

from .controller import S2State, brief, plain, restore_state


class BudgetStop(RuntimeError):
    pass


def config_for(job, snapshot, binding, parameters, mode):
    if job["task"] != "tsp" or snapshot["block"] != job["data_block"]:
        raise ValueError("S2 job and snapshot mismatch")
    return {
        "schema": "chapter6-s2-tsp-run-v1",
        **job,
        "execution_mode": mode,
        "data_search_sha256": digest(snapshot),
        "binding": binding,
        "parameters": parameters,
        "source": source_record(),
        "environment": environment(),
        "test_access": False,
        "selector_enabled": False,
    }


def validate_candidate(node, decision):
    expected = {
        "parent_id": decision["parent"]["id"] if decision["parent"] else None,
        "reference_id": decision["reference"]["id"] if decision["reference"] else None,
        "allocated_tag": decision["target"],
        "action": decision["action"],
        "allocation": decision["allocation"],
    }
    for key, value in expected.items():
        if plain(node.get(key)) != plain(value):
            raise ValueError(f"Candidate {key} differs from immutable decision")
    if node["evaluation"].get("program_identity") != identity(node.get("code", "")):
        raise ValueError("Candidate program identity differs from evaluation")


def _seed_nodes(snapshot, evaluate):
    seeds = []
    for idx, (name, tags, code) in enumerate(SEEDS["tsp"]):
        node = {
            "id": idx, "name": name, "intent": "shared hand-written seed: " + name,
            "tags": tags, "reported_tags": tags, "allocated_tag": None,
            "code": code, "source": "handwritten_seed", "parent_id": None,
            "reference_id": None, "action": "seed", "allocation": {},
            "generation_mode": "seed", "proposal_failure": None,
            "evaluation": evaluate(code, snapshot),
        }
        if not node["evaluation"].get("valid"):
            raise ValueError(f"Shared seed {name} failed evaluation")
        seeds.append(node)
    return seeds


def _brief_selection(selection):
    # The prompt receives only the operational decision, never the arm name.
    def prompt_node(value):
        if value is None:
            return None
        return {
            "id": value["id"], "intent": value.get("intent", ""),
            "tags": value.get("tags", []), "code": value.get("code", ""),
            "evaluation": {
                "loss": value.get("loss"), "valid": value.get("valid", False),
                "family_loss": value.get("family_loss", {}),
                "failure_type": value.get("failure_type"),
                "trajectory_values": value.get("trajectory_values", []),
            },
        }
    return {
        "target": selection["target"], "action": selection["action"],
        "parent": prompt_node(selection["parent"]),
        "reference": prompt_node(selection["reference"]),
        "evidence": selection["evidence"],
        "recent": [],
    }


def _check_budget(calls, started, parameters):
    usage = calls.usage()
    if usage["total_tokens"] is not None and usage["total_tokens"] > parameters["token_budget"]:
        raise BudgetStop("token_budget")
    if usage["call_attempts"] > parameters["request_limit"]:
        raise BudgetStop("request_limit")
    if time.perf_counter() - started > parameters["wall_limit_seconds"]:
        raise BudgetStop("wall_limit")


def run_search(job, snapshot, directory, binding, parameters, transport, *, mode="live", hook=no_hook):
    directory = Path(directory)
    if mode not in ("live", "fixture"):
        raise ValueError("Unsupported S2 execution mode")
    config = config_for(job, snapshot, binding, parameters, mode)
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        result_path = directory / "search_result.json"
        checkpoint_path = directory / "checkpoint.json"
        if result_path.exists():
            result = read_json(result_path)
            if result["config"] != config:
                raise ValueError("Completed S2 config differs")
            state = restore_state(read_json(checkpoint_path))
            if file_sha(directory / "selection_frozen.json") != result["selection_frozen_sha256"]:
                raise ValueError("Completed S2 selection changed")
            save_json(directory / "status.json", {"status": result["status"], "completed_proposals": job["steps"]})
            return result

        started = time.perf_counter()
        if checkpoint_path.exists():
            checkpoint = read_json(checkpoint_path)
            if checkpoint["config"] != config:
                raise ValueError("S2 checkpoint config differs")
            state = restore_state(checkpoint)
            seeds = checkpoint["seeds"]
            records = checkpoint["records"]
            prior_elapsed = checkpoint.get("elapsed_seconds", 0.0)
        else:
            evaluate = evaluate_search
            seeds = _seed_nodes(snapshot, evaluate)
            state = S2State(job["controller"], job["search_seed"], steps=job["steps"],
                            capacity=job["capacity"], grant=job["grant"],
                            maximum_attempts=job["maximum_direction_attempts"],
                            quality_tolerance=job["quality_tolerance"],
                            gain_epsilon=job["gain_epsilon"], protection=job["protection"])
            state.initialize(seeds)
            records = []
            checkpoint = {"config": config, "seeds": seeds, "records": [],
                          "state": state.snapshot(), "elapsed_seconds": 0.0}
            save_json(checkpoint_path, checkpoint)
            prior_elapsed = 0.0

        calls = DurableCalls(directory, config, transport, hook)
        status = None
        for step in range(len(records), job["steps"]):
            selection = plain(state.choose(step))
            slot = directory / "slots" / f"{step:03d}"
            save_json(slot / "decision.json", {"decision": selection,
                                                "controller": job["controller"]}, immutable=True)
            hook("decision_saved", step, None)
            plan = {"name": f"candidate_{step}", "intent": "unavailable",
                    "tags": [selection["target"]]}
            code, failure = "", None
            try:
                response = calls.complete(step, "planner", SYSTEM,
                                          planner_prompt("tsp", _brief_selection(selection), step),
                                          parameters["planner_max_tokens"])
                _check_budget(calls, started, parameters)
                try:
                    parsed = parse_json(response["text"])
                    if not isinstance(parsed, dict):
                        raise ValueError("planner JSON must be an object")
                    plan.update(parsed)
                except (ValueError, TypeError, KeyError) as exc:
                    failure = {"kind": "proposal_parse_failure", "stage": "planner",
                               "error_type": type(exc).__name__}
                if failure is None:
                    response = calls.complete(step, "coder", SYSTEM,
                                              coder_prompt("tsp", plan, _brief_selection(selection)),
                                              parameters["coder_max_tokens"])
                    _check_budget(calls, started, parameters)
                    try:
                        parsed = parse_json(response["text"])
                        if not isinstance(parsed, dict) or not isinstance(parsed.get("code"), str):
                            raise ValueError("coder JSON must contain a string code")
                        code = parsed["code"]
                    except (ValueError, TypeError, KeyError) as exc:
                        failure = {"kind": "proposal_parse_failure", "stage": "coder",
                                   "error_type": type(exc).__name__}
            except (IndeterminateCall, ProviderFailure) as exc:
                status = {"status": "infrastructure_incomplete", "error_type": type(exc).__name__,
                          "completed_proposals": len(records), "reserved_step": step,
                          "usage": calls.usage(), "run_config_sha256": digest(config),
                          "no_automatic_retry": True}
                save_json(directory / "status.json", status)
                return status
            except BudgetStop as exc:
                status = {"status": "budget_exhausted", "reason": str(exc),
                          "completed_proposals": len(records), "reserved_step": step,
                          "usage": calls.usage(), "run_config_sha256": digest(config),
                          "no_automatic_retry": True}
                save_json(directory / "status.json", status)
                return status

            reported_tags = plan.get("tags", [])
            allowed_tags = set(__import__("chapter6_demo.benchmarks", fromlist=["TAGS"]).TAGS["tsp"])
            tags = [tag for tag in reported_tags if tag in allowed_tags] if isinstance(reported_tags, list) else []
            node = {
                "id": len(state.nodes), "name": str(plan.get("name", "candidate"))[:80],
                "intent": str(plan.get("intent", ""))[:800],
                "tags": tags[:2] or [selection["target"]], "reported_tags": reported_tags,
                "allocated_tag": selection["target"], "code": code, "source": "live_llm",
                "parent_id": selection["parent"]["id"] if selection["parent"] else None,
                "reference_id": selection["reference"]["id"] if selection["reference"] else None,
                "action": selection["action"], "allocation": selection["allocation"],
                "generation_mode": mode, "proposal_failure": failure,
                "evaluation": evaluate_search(code, snapshot),
            }
            candidate_path = slot / "candidate.json"
            if candidate_path.exists():
                saved = read_json(candidate_path)
                if {k: v for k, v in saved.items() if k != "evaluation"} != {k: v for k, v in node.items() if k != "evaluation"}:
                    raise ValueError("Stored S2 candidate differs")
                node = saved
            else:
                save_json(candidate_path, node, immutable=True)
            validate_candidate(node, selection)
            event = state.observe(copy.deepcopy(node))
            record = {"decision": selection, "node": node, "event": event,
                      "pool_after": copy.deepcopy(state.pool),
                      "usage_after": calls.usage(),
                      "elapsed_after": prior_elapsed + time.perf_counter() - started}
            records.append(record)
            checkpoint = {"config": config, "seeds": seeds, "records": records,
                          "state": state.snapshot(),
                          "elapsed_seconds": record["elapsed_after"]}
            save_json(checkpoint_path, checkpoint)
            hook("checkpoint_committed", step, None)

        valid = [n for n in state.nodes if n["evaluation"].get("valid")]
        best = min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"]))
        seed_best = min(seeds, key=lambda n: (n["evaluation"]["loss"], n["id"]))
        archive = report_archive(state.nodes, "tsp", quality_reference=seed_best["evaluation"]["loss"])
        ids = sorted({best["id"], seed_best["id"], *(n["id"] for n in archive)})
        readout = {
            "run_config_sha256": digest(config), "execution_mode": mode,
            "selected_on": "validation", "best_id": best["id"],
            "seed_best_id": seed_best["id"], "archive_ids": [n["id"] for n in archive],
            "programs": [{"id": n["id"], "code": n["code"], **identity(n["code"])}
                         for n in state.nodes if n["id"] in ids],
        }
        readout_path = directory / "selection_frozen.json"
        save_json(readout_path, {**readout, "frozen_utc": utcnow()}, immutable=True)
        usage = calls.usage()
        summary = {**state.summary(), "completed_proposals": len(records),
                   "model_calls": usage["call_attempts"] if mode == "live" else 0,
                   "known_tokens": usage["known_tokens"], "total_tokens": usage["total_tokens"],
                   "request_count": usage["call_attempts"],
                   "wall_seconds": prior_elapsed + time.perf_counter() - started,
                   "archive_size": len(archive)}
        result = {"config": config, "status": "search_complete_test_not_run",
                  "summary": summary, "usage": usage,
                  "selection_frozen_sha256": file_sha(readout_path),
                  "checkpoint_sha256": file_sha(checkpoint_path),
                  "claim": "S2 prospective protection factor; no superiority claim"}
        save_json(result_path, result, immutable=True)
        save_json(directory / "status.json", {"status": result["status"],
                                                "completed_proposals": len(records)})
        return result
