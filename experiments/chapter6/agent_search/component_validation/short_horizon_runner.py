"""Durable four-proposal C-I/C-B/C-E short-horizon runner."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import SYSTEM, coder_prompt, planner_prompt
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, file_sha, read_json, run_lock, save_json, source_record

from ..s3_tsp_r3.evaluator import evaluate_search
from .e1_runner import BudgetStop, _brief, _check_budget, _parse_response, _prompt_node
from .service import DiagnosticDurableCalls


class ShortHorizonState:
    def __init__(self, checkpoint: dict, strategy: str, steps: int = 4):
        if strategy not in {"C-I", "C-B", "C-E"}:
            raise ValueError(f"unknown short-horizon strategy: {strategy}")
        if not checkpoint.get("incumbent"):
            raise ValueError("checkpoint has no incumbent")
        self.strategy, self.steps = strategy, int(steps)
        self.nodes = copy.deepcopy(checkpoint["nodes"])
        self.by_id = {node["id"]: node for node in self.nodes}
        self.start_incumbent_id = int(checkpoint["incumbent"]["id"])
        self.start_branch_id = int(checkpoint["branch"]["id"]) if checkpoint.get("branch") else None
        self.start_best_loss = float(checkpoint["incumbent"]["loss"])
        self.global_best_id = self.start_incumbent_id
        self.project_best_id = self.start_incumbent_id if strategy == "C-I" else self.start_branch_id if strategy == "C-B" else None
        self.investment_id = f"{strategy}-I0"
        self.exploration_root_id = None
        self.decisions, self.events = [], []
        self.pending = None

    @property
    def global_best(self):
        return self.by_id[self.global_best_id]

    @property
    def project_best(self):
        return self.by_id[self.project_best_id] if self.project_best_id is not None else None

    def _parent(self):
        if self.strategy == "C-E" and self.exploration_root_id is None:
            return None
        return self.project_best

    def choose(self, step: int) -> dict:
        if self.pending is not None or step != len(self.decisions) or step >= self.steps:
            raise ValueError("short-horizon step is not contiguous")
        parent = self._parent()
        action = "explore" if self.strategy == "C-E" and parent is None else "develop"
        target = "explore" if action == "explore" else {
            "C-I": "incumbent_project", "C-B": "branch_project", "C-E": "exploration_project"
        }[self.strategy]
        decision = {
            "step": step, "strategy": self.strategy, "action": action,
            "target": TAGS["tsp"][step % len(TAGS["tsp"])], "parent": _brief(parent),
            "reference": None,
            "allocation": {"investment_id": self.investment_id, "project_best_id": self.project_best_id,
                            "exploration_root_id": self.exploration_root_id,
                            "start_incumbent_id": self.start_incumbent_id,
                            "start_branch_id": self.start_branch_id},
            "evidence": {"same_state": True, "strategy": self.strategy,
                         "checkpoint_best_loss": self.start_best_loss,
                         "phase": "explore" if action == "explore" else "development"},
        }
        self.pending = copy.deepcopy(decision); self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def observe(self, node: dict, costs: dict | None = None) -> dict:
        if self.pending is None:
            raise ValueError("no pending short-horizon decision")
        decision = self.pending
        expected_parent = decision["parent"]["id"] if decision["parent"] else None
        if node.get("parent_id") != expected_parent:
            raise ValueError("candidate parent differs from short-horizon decision")
        ev = node.get("evaluation", {})
        valid, loss = bool(ev.get("valid")), ev.get("loss")
        parent = self.by_id.get(node.get("parent_id"))
        parent_loss = parent.get("evaluation", {}).get("loss") if parent else None
        before_global = self.global_best["evaluation"].get("loss")
        before_project = self.project_best["evaluation"].get("loss") if self.project_best else None
        parent_gain = parent_loss - loss if valid and parent_loss is not None else None
        global_gain = before_global - loss if valid and before_global is not None else None
        project_gain = before_project - loss if valid and before_project is not None else None
        self.nodes.append(copy.deepcopy(node)); self.by_id[node["id"]] = node
        if valid and (self.global_best["evaluation"].get("loss") is None or loss < self.global_best["evaluation"]["loss"]):
            self.global_best_id = node["id"]
        project_progress = bool(project_gain is not None and project_gain > 1e-4)
        if self.strategy == "C-E" and decision["action"] == "explore" and valid:
            self.exploration_root_id = node["id"]
            self.project_best_id = node["id"]
        elif valid and project_progress:
            self.project_best_id = node["id"]
        event = {
            "step": decision["step"], "node_id": node["id"], "strategy": self.strategy,
            "action": decision["action"], "valid": valid, "loss": loss,
            "parent_id": node.get("parent_id"), "parent_gain": parent_gain,
            "global_gain": global_gain, "project_gain": project_gain,
            "global_improvement": bool(global_gain is not None and global_gain > 1e-4),
            "parent_improvement": bool(parent_gain is not None and parent_gain > 1e-4),
            "project_progress": project_progress,
            "project_id": self.investment_id,
            "exploration_followup": self.exploration_root_id is not None and decision["step"] > 0,
            "costs": copy.deepcopy(costs or {}),
        }
        self.events.append(event); self.pending = None
        return event

    def snapshot(self) -> dict:
        return {"strategy": self.strategy, "steps": self.steps,
                "start_incumbent_id": self.start_incumbent_id, "start_branch_id": self.start_branch_id,
                "start_best_loss": self.start_best_loss, "global_best_id": self.global_best_id,
                "project_best_id": self.project_best_id, "investment_id": self.investment_id,
                "exploration_root_id": self.exploration_root_id, "decisions": self.decisions,
                "events": self.events, "nodes": self.nodes}


def restore_short_horizon(checkpoint: dict, saved: dict) -> ShortHorizonState:
    data = saved["state"]
    state = ShortHorizonState(checkpoint, data["strategy"], data["steps"])
    for key in ("start_incumbent_id", "start_branch_id", "start_best_loss", "global_best_id",
                "project_best_id", "investment_id", "exploration_root_id", "decisions", "events", "nodes"):
        setattr(state, key, copy.deepcopy(data[key]))
    state.by_id = {node["id"]: node for node in state.nodes}; state.pending = None
    return state


def run_short_horizon(job: dict, checkpoint: dict, snapshot: dict, directory: Path,
                      binding: dict, parameters: dict, transport, *, mode: str = "live") -> dict:
    if mode not in {"live", "fixture"}:
        raise ValueError("mode must be live or fixture")
    directory = Path(directory)
    config = {"schema": "chapter6-short-horizon-run-v1", **job,
              "execution_mode": mode, "checkpoint_sha256": digest(checkpoint),
              "data_search_sha256": digest(snapshot), "binding": binding,
              "parameters": parameters, "source": source_record(), "test_access": False}
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        result_path, checkpoint_path = directory / "search_result.json", directory / "checkpoint.json"
        if result_path.exists(): return read_json(result_path)
        if checkpoint_path.exists():
            saved = read_json(checkpoint_path)
            if saved.get("config") != config: raise ValueError("short-horizon checkpoint config differs")
            state, records = restore_short_horizon(checkpoint, saved), saved.get("records", [])
        else:
            state, records = ShortHorizonState(checkpoint, job["strategy"], job["steps"]), []
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(), "records": records}, immutable=True)
        started, status, stop_details = time.perf_counter(), "continuation_complete", {}
        calls = DiagnosticDurableCalls(directory, config, transport)
        for step in range(len(records), job["steps"]):
            selection = state.choose(step); slot = directory / "slots" / f"{step:03d}"
            save_json(slot / "decision.json", {"decision": selection}, immutable=True)
            plan, code, failure = {"name": f"short_horizon_{step}", "intent": "unavailable", "tags": [selection["target"]]}, "", None
            try:
                _check_budget(calls, parameters, started)
                plan_value, failure = _parse_response(calls.complete(step, "planner", SYSTEM,
                    planner_prompt("tsp", {"target": selection["target"], "action": selection["action"],
                        "parent": _prompt_node(selection["parent"]), "reference": None,
                        "evidence": selection["evidence"]}, step), parameters["planner_max_tokens"]), "planner")
                if failure is None:
                    plan.update(plan_value); _check_budget(calls, parameters, started)
                    coded, failure = _parse_response(calls.complete(step, "coder", SYSTEM,
                        coder_prompt("tsp", plan, {"target": selection["target"], "action": selection["action"],
                            "parent": _prompt_node(selection["parent"]), "reference": None,
                            "evidence": selection["evidence"]}), parameters["coder_max_tokens"]), "coder")
                    if failure is None:
                        code = coded.get("code", "")
                        if not isinstance(code, str): code, failure = "", {"kind": "proposal_parse_failure", "stage": "coder"}
            except BudgetStop as exc:
                status, stop_details = "budget_exhausted", {"reason": str(exc), "reserved_step": step}; break
            except (IndeterminateCall, ProviderFailure) as exc:
                status, stop_details = "infrastructure_incomplete", {"error_type": type(exc).__name__, "reserved_step": step,
                    "diagnostics": getattr(exc, "diagnostics", None), "no_automatic_retry": True}; break
            node = {"id": max(state.by_id) + 1, "name": str(plan.get("name", "candidate"))[:80],
                    "intent": str(plan.get("intent", ""))[:800], "tags": plan.get("tags", []), "code": code,
                    "source": "live_llm", "parent_id": selection["parent"]["id"] if selection["parent"] else None,
                    "reference_id": None, "action": selection["action"], "allocation": selection["allocation"],
                    "proposal_failure": failure, "evaluation": evaluate_search(code, snapshot)}
            save_json(slot / "candidate.json", node, immutable=True)
            usage, prior = calls.usage(), records[-1].get("usage_after", {"known_tokens": 0, "call_attempts": 0}) if records else {"known_tokens": 0, "call_attempts": 0}
            costs = {"requests_added": usage["call_attempts"] - prior.get("call_attempts", 0),
                     "tokens_added": usage.get("known_tokens") - prior.get("known_tokens", 0) if usage.get("usage_complete") else None,
                     "complete_responses": 0 if failure else 2, "execution_cost": node["evaluation"].get("wall_seconds")}
            event = state.observe(node, costs); records.append({"decision": selection, "node": node, "event": event, "costs": costs, "usage_after": usage})
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(), "records": records}, immutable=False)
        valid = [node for node in state.nodes if node.get("evaluation", {}).get("valid")]
        best = min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"])) if valid else None
        selection = {"schema": "chapter6-short-horizon-selection-v1", "selected_on": "validation",
                     "strategy": job["strategy"], "best_id": best["id"] if best else None,
                     "code": best.get("code", "") if best else "", "validation_loss": best["evaluation"].get("loss") if best else None,
                     "checkpoint_best_loss": state.start_best_loss, "completed_proposals": len(records), "status": status}
        save_json(directory / "selection_frozen.json", selection, immutable=True)
        usage = calls.usage(); summary = {"status": status, "strategy": job["strategy"],
            "checkpoint_best_loss": state.start_best_loss, "final_best_loss": best["evaluation"]["loss"] if best else None,
            "improvement_over_checkpoint": state.start_best_loss - best["evaluation"]["loss"] if best else None,
            "completed_proposals": len(records), "global_improvements": sum(e["global_improvement"] for e in state.events),
            "parent_improvements": sum(e["parent_improvement"] for e in state.events), "project_progress": sum(e["project_progress"] for e in state.events),
            "exploration_followups": sum(e["exploration_followup"] for e in state.events), "known_tokens": usage.get("known_tokens"),
            "request_count": usage.get("call_attempts"), "wall_seconds": time.perf_counter() - started,
            "new_model_calls": 0 if mode == "fixture" else usage.get("call_attempts", 0)}
        result = {"status": status, "stop_details": stop_details, "config": config, "summary": summary, "usage": usage,
                  "state_sha256": digest(state.snapshot()), "selection_frozen_sha256": file_sha(directory / "selection_frozen.json")}
        save_json(result_path, result, immutable=True); save_json(directory / "status.json", {"status": status, "completed_proposals": len(records)}, immutable=True)
        return result
