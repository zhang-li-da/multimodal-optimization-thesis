"""Durable runner for one E1 continuation fork.

This runner is intentionally separate from the S3 natural-search runner.  A
fork has a frozen incumbent and lagging branch, and its three treatments differ
only in the parent supplied to the next proposal.  It can be exercised with a
fixture transport; live use must happen only after an E1 manifest and provider
preflight have been frozen.
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import time

from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import SYSTEM, coder_prompt, planner_prompt
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, file_sha, read_json, run_lock, save_json, source_record, utcnow

from ..s3_tsp_r3.evaluator import evaluate_search
from .service import DiagnosticDurableCalls


def _brief(node: dict | None) -> dict | None:
    if node is None:
        return None
    ev = node.get("evaluation", {})
    return {"id": node["id"], "intent": node.get("intent", "")[:800],
            "tags": node.get("tags", []), "code": node.get("code", ""),
            "loss": ev.get("loss"), "valid": ev.get("valid", False),
            "family_loss": ev.get("family_loss", {}),
            "failure_type": ev.get("failure_type"),
            "trajectory_values": ev.get("trajectory_values", [])[:2]}


def _prompt_node(node: dict | None) -> dict | None:
    """Restore the evaluation wrapper expected by discovery.planner_prompt."""
    if node is None:
        return None
    value = dict(node)
    value["evaluation"] = {"loss": node.get("loss"), "valid": node.get("valid", False),
                            "family_loss": node.get("family_loss", {}),
                            "failure_type": node.get("failure_type"),
                            "trajectory_values": node.get("trajectory_values", [])}
    return value


class ContinuationState:
    """Minimal immutable-history state used by a C-I/C-B/C-E fork."""

    def __init__(self, checkpoint: dict, strategy: str, steps: int = 4):
        if strategy not in {"C-I", "C-B", "C-E"}:
            raise ValueError(f"unknown E1 strategy: {strategy}")
        self.strategy = strategy
        self.steps = int(steps)
        self.nodes = copy.deepcopy(checkpoint["nodes"])
        self.by_id = {node["id"]: node for node in self.nodes}
        self.start_incumbent_id = int(checkpoint["incumbent"]["id"])
        self.start_branch_id = int(checkpoint["branch"]["id"])
        self.incumbent_id = self.start_incumbent_id
        self.branch_id = self.start_branch_id
        self.start_best_loss = float(checkpoint["incumbent"]["loss"])
        self.decisions: list[dict] = []
        self.events: list[dict] = []
        self.pending = None

    @property
    def incumbent(self):
        return self.by_id[self.incumbent_id]

    @property
    def branch(self):
        return self.by_id[self.branch_id]

    def choose(self, step: int) -> dict:
        if self.pending is not None:
            raise ValueError("observe the preceding continuation proposal first")
        if step != len(self.decisions) or step >= self.steps:
            raise ValueError("E1 step is not contiguous")
        if self.strategy == "C-I":
            parent = self.incumbent
            action = "develop"
            target = "incumbent"
        elif self.strategy == "C-B":
            parent = self.branch
            action = "develop"
            target = "lagging_branch"
        else:
            parent = None
            action = "explore"
            target = "explore"
        decision = {
            "step": step, "strategy": self.strategy, "action": action,
            "target": TAGS["tsp"][step % len(TAGS["tsp"])],
            "parent": _brief(parent), "reference": None,
            "allocation": {"continuation_target": target,
                            "start_incumbent_id": self.start_incumbent_id,
                            "start_branch_id": self.start_branch_id,
                            "current_incumbent_id": self.incumbent_id,
                            "current_branch_id": self.branch_id},
            "evidence": {"same_state": True, "strategy": self.strategy,
                         "checkpoint_best_loss": self.start_best_loss},
        }
        self.pending = copy.deepcopy(decision)
        self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def observe(self, node: dict, costs: dict | None = None) -> dict:
        if self.pending is None:
            raise ValueError("no pending continuation decision")
        decision = self.pending
        if node.get("parent_id") != (decision["parent"]["id"] if decision["parent"] else None):
            raise ValueError("continuation candidate parent differs from decision")
        previous_best = self.incumbent["evaluation"].get("loss")
        parent = self.by_id.get(node.get("parent_id"))
        parent_loss = parent.get("evaluation", {}).get("loss") if parent else None
        ev = node.get("evaluation", {})
        valid = bool(ev.get("valid"))
        loss = ev.get("loss")
        parent_gain = (parent_loss - loss) if valid and parent_loss is not None else None
        global_gain = (previous_best - loss) if valid and previous_best is not None else None
        self.nodes.append(copy.deepcopy(node)); self.by_id[node["id"]] = node
        if valid and (self.incumbent["evaluation"].get("loss") is None or
                      loss < self.incumbent["evaluation"]["loss"]):
            self.incumbent_id = node["id"]
        if self.strategy == "C-B" and valid and parent and loss < parent_loss:
            self.branch_id = node["id"]
        event = {
            "step": decision["step"], "node_id": node["id"],
            "strategy": self.strategy, "valid": valid, "loss": loss,
            "parent_id": node.get("parent_id"), "parent_gain": parent_gain,
            "global_gain": global_gain,
            "relative_to_checkpoint_best": (self.start_best_loss - loss)
            if valid else None,
            "global_improvement": bool(global_gain is not None and global_gain > 1e-4),
            "parent_improvement": bool(parent_gain is not None and parent_gain > 1e-4),
            "costs": copy.deepcopy(costs or {}),
        }
        self.events.append(event); self.pending = None
        return event

    def snapshot(self) -> dict:
        return {"strategy": self.strategy, "steps": self.steps,
                "start_incumbent_id": self.start_incumbent_id,
                "start_branch_id": self.start_branch_id,
                "incumbent_id": self.incumbent_id, "branch_id": self.branch_id,
                "decisions": self.decisions, "events": self.events,
                "nodes": self.nodes}


def restore_continuation(checkpoint: dict, saved: dict) -> ContinuationState:
    """Restore a fork from its durable prefix without replaying model calls."""
    state_data = saved["state"]
    state = ContinuationState(checkpoint, state_data["strategy"], state_data["steps"])
    state.nodes = copy.deepcopy(state_data["nodes"])
    state.by_id = {node["id"]: node for node in state.nodes}
    state.start_incumbent_id = state_data["start_incumbent_id"]
    state.start_branch_id = state_data["start_branch_id"]
    state.incumbent_id = state_data["incumbent_id"]
    state.branch_id = state_data["branch_id"]
    state.decisions = copy.deepcopy(state_data["decisions"])
    state.events = copy.deepcopy(state_data["events"])
    state.pending = None
    return state


def _parse_response(response: dict, stage: str) -> tuple[dict | None, dict | None]:
    try:
        value = parse_json(response["text"])
        if not isinstance(value, dict):
            raise ValueError("model response must be an object")
        return value, None
    except (ValueError, TypeError, KeyError) as exc:
        return None, {"kind": "proposal_parse_failure", "stage": stage,
                      "error_type": type(exc).__name__}


def run_continuation(job: dict, checkpoint: dict, snapshot: dict, directory: Path,
                     binding: dict, parameters: dict, transport, *, mode: str = "live") -> dict:
    """Run one four-proposal fork and persist every continuation decision."""
    if mode not in {"live", "fixture"}:
        raise ValueError("mode must be live or fixture")
    directory = Path(directory)
    config = {"schema": "chapter6-e1-continuation-run-v1", **job,
              "provider": job.get("provider", "fixture"),
              "model": job.get("model", "fixture-model"),
              "execution_mode": mode, "checkpoint_sha256": digest(checkpoint),
              "data_search_sha256": digest(snapshot), "binding": binding,
              "parameters": parameters, "source": source_record(),
              "test_access": False}
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        checkpoint_path = directory / "checkpoint.json"
        result_path = directory / "search_result.json"
        if result_path.exists():
            return read_json(result_path)
        if checkpoint_path.exists():
            saved = read_json(checkpoint_path)
            if saved.get("config") != config:
                raise ValueError("E1 continuation checkpoint config differs")
            state = restore_continuation(checkpoint, saved)
            records = saved.get("records", [])
        else:
            state = ContinuationState(checkpoint, job["strategy"], job["steps"])
            records = []
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(),
                                        "records": records}, immutable=True)
        start = time.perf_counter()
        calls = DiagnosticDurableCalls(directory, config, transport)
        status = "continuation_complete"
        stop_details = {}
        for step in range(len(records), job["steps"]):
            selection = state.choose(step)
            slot = directory / "slots" / f"{step:03d}"
            save_json(slot / "decision.json", {"decision": selection}, immutable=True)
            plan = {"name": f"continuation_{step}", "intent": "unavailable",
                    "tags": [selection["target"]]}
            code, failure = "", None
            try:
                plan_value, failure = _parse_response(calls.complete(
                    step, "planner", SYSTEM,
                    planner_prompt("tsp", {"target": selection["target"],
                                            "action": selection["action"],
                                            "parent": _prompt_node(selection["parent"]),
                                            "reference": None, "evidence": {"same_state": True}},
                                     step), parameters["planner_max_tokens"]), "planner")
                if failure is None:
                    plan.update(plan_value)
                    coded, failure = _parse_response(calls.complete(
                        step, "coder", SYSTEM,
                        coder_prompt("tsp", plan, {"target": selection["target"],
                                                   "action": selection["action"],
                                                   "parent": _prompt_node(selection["parent"]),
                                                   "reference": None, "evidence": {"same_state": True}}),
                        parameters["coder_max_tokens"]), "coder")
                    if failure is None:
                        code = coded.get("code", "")
                        if not isinstance(code, str):
                            code, failure = "", {"kind": "proposal_parse_failure", "stage": "coder",
                                                   "error_type": "missing_code"}
            except (IndeterminateCall, ProviderFailure) as exc:
                status = "infrastructure_incomplete"
                stop_details = {"error_type": type(exc).__name__, "reserved_step": step,
                                "completed_proposals": len(records), "no_automatic_retry": True,
                                "diagnostics": getattr(exc, "diagnostics", None)}
                break
            node = {"id": max(state.by_id) + 1, "name": str(plan.get("name", "candidate"))[:80],
                    "intent": str(plan.get("intent", ""))[:800],
                    "tags": plan.get("tags", []) if isinstance(plan.get("tags"), list) else [],
                    "code": code, "source": "live_llm", "parent_id": selection["parent"]["id"]
                    if selection["parent"] else None, "reference_id": None,
                    "action": selection["action"], "allocation": selection["allocation"],
                    "proposal_failure": failure,
                    "evaluation": evaluate_search(code, snapshot)}
            save_json(slot / "candidate.json", node, immutable=True)
            usage = calls.usage()
            prior = records[-1]["usage_after"] if records else {"known_tokens": 0, "call_attempts": 0}
            costs = {"requests_added": usage["call_attempts"] - prior.get("call_attempts", 0),
                     "tokens_added": (usage["known_tokens"] - prior.get("known_tokens", 0)
                                      if usage.get("usage_complete") else None),
                     "complete_responses": 0 if failure else 2,
                     "truncations": 0, "execution_cost": node["evaluation"].get("wall_seconds")}
            event = state.observe(node, costs)
            records.append({"decision": selection, "node": node, "event": event,
                            "costs": costs, "usage_after": usage})
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(),
                                        "records": records}, immutable=False)
        valid = [node for node in state.nodes if node.get("evaluation", {}).get("valid")]
        best = min(valid, key=lambda node: (node["evaluation"]["loss"], node["id"])) if valid else None
        improvement = (state.start_best_loss - best["evaluation"]["loss"]
                       if best else None)
        selection = {"schema": "chapter6-e1-selection-v1", "selected_on": "validation",
                     "strategy": job["strategy"], "best_id": best["id"] if best else None,
                     "code": best.get("code", "") if best else "",
                     "validation_loss": best["evaluation"].get("loss") if best else None,
                     "checkpoint_best_loss": state.start_best_loss,
                     "completed_proposals": len(records), "status": status}
        save_json(directory / "selection_frozen.json", selection, immutable=True)
        summary = {"status": status, "strategy": job["strategy"],
                   "checkpoint_best_loss": state.start_best_loss,
                   "final_best_loss": best["evaluation"]["loss"] if best else None,
                   "improvement_over_checkpoint": improvement,
                   "completed_proposals": len(records),
                   "global_improvements": sum(event["global_improvement"] for event in state.events),
                   "parent_improvements": sum(event["parent_improvement"] for event in state.events),
                   "known_tokens": calls.usage().get("known_tokens"),
                   "request_count": calls.usage().get("call_attempts"),
                   "wall_seconds": time.perf_counter() - start,
                   "new_model_calls": 0 if mode == "fixture" else calls.usage().get("call_attempts", 0)}
        result = {"status": status, "stop_details": stop_details, "config": config,
                  "summary": summary, "usage": calls.usage(),
                  "state_sha256": digest(state.snapshot()),
                  "selection_frozen_sha256": file_sha(directory / "selection_frozen.json")}
        save_json(result_path, result, immutable=True)
        save_json(directory / "status.json", {"status": status,
                                               "completed_proposals": len(records)}, immutable=True)
        return result
