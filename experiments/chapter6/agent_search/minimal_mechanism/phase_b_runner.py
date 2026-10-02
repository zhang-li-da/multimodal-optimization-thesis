"""Durable, test-blind runner for the phase-B four-action diagnostic."""
from __future__ import annotations

import copy
import json
import math
import time
from pathlib import Path
from statistics import median

from chapter6_demo import benchmarks
from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import BEHAVIOR_RADIUS, SYSTEM, coder_prompt, planner_prompt
from chapter6_demo.v12_2.calls import IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import digest, file_sha, read_json, run_lock, save_json, source_record

from ..component_validation.e1_runner import BudgetStop, _brief, _parse_response, _prompt_node
from ..component_validation.service import DiagnosticDurableCalls
from ..s3_tsp_r3.evaluator import evaluate_search


STRATEGIES = ("I", "B", "E0", "EG")
REQUEST_OVERHEAD_TOKEN_RESERVE = 512
TERMINAL = {"continuation_complete", "infrastructure_incomplete", "budget_exhausted",
            "branch_unavailable", "preparation_incomplete", "not_started",
            "sent_unknown", "provider_failed"}


def _check_request_budget(calls, parameters: dict, started: float,
                          system: str, prompt: str, max_output_tokens: int,
                          transport=None) -> float:
    usage = calls.usage()
    attempts = int(usage.get("call_attempts", 0))
    if attempts and not usage.get("usage_complete"):
        raise IndeterminateCall("Prior request usage is incomplete; refusing another dispatch.")
    if attempts >= int(parameters["request_limit"]):
        raise BudgetStop("request_limit")
    prompt_byte_upper_bound = len(system.encode("utf-8")) + len(prompt.encode("utf-8"))
    request_reserve = (prompt_byte_upper_bound + REQUEST_OVERHEAD_TOKEN_RESERVE
                       + int(max_output_tokens))
    if int(usage.get("known_tokens", 0)) + request_reserve > int(parameters["token_budget"]):
        raise BudgetStop("token_budget_reservation")
    wall_limit = float(parameters["wall_limit_seconds"])
    # Optional, prospective protocol field. Frozen studies which omit it
    # keep their original admission rule; never infer it from live outcomes.
    minimum_wall = float(parameters.get("minimum_request_wall_seconds", 0))
    if not math.isfinite(minimum_wall) or not 0 <= minimum_wall <= wall_limit:
        raise ValueError("minimum_request_wall_seconds must be finite and within the task wall limit")
    remaining = wall_limit - (time.perf_counter() - started)
    if remaining <= 0:
        raise BudgetStop("wall_limit")
    if remaining < minimum_wall:
        raise BudgetStop("wall_request_reservation")
    set_deadline = getattr(transport, "set_wall_deadline", None)
    if callable(set_deadline):
        set_deadline(started + wall_limit)
    return min(float(parameters.get("timeout_seconds", remaining)), remaining)


def _check_completed_call_usage(calls, parameters: dict) -> None:
    usage = calls.usage()
    if not usage.get("usage_complete"):
        raise BudgetStop("token_usage_incomplete")
    if int(usage.get("known_tokens", 0)) > int(parameters["token_budget"]):
        raise BudgetStop("token_budget_overrun_reported_by_provider")
def _valid_hypothesis(value):
    if not isinstance(value, dict):
        return False
    aliases = {
        "target_failure": ("target_failure",),
        "mechanism": ("mechanism",),
        "expected_behavior": ("expected_behavior_change", "expected_behavior"),
        "prediction": ("falsifiable_prediction", "prediction"),
    }
    return all(any(isinstance(value.get(key), str) and value[key].strip()
                   for key in keys) for keys in aliases.values())


def history_summary(nodes: list[dict], incumbent_loss: float) -> dict:
    """Summarize only persisted search-time nodes; behavior cells are not directions."""
    valid = [n for n in nodes if n.get("evaluation", {}).get("valid") and
             isinstance(n.get("evaluation", {}).get("loss"), (int, float))]
    ranked = sorted(valid, key=lambda n: (n["evaluation"]["loss"], n["id"]))
    represented = {tag for node in nodes for tag in node.get("tags", []) if isinstance(tag, str)}
    available_tags = list(TAGS["tsp"])
    evidence_nodes = []
    for node in nodes:
        evaluation = node.get("evaluation", {})
        evidence_nodes.append({
            "node_id": node.get("id"),
            "parent_id": node.get("parent_id"),
            "intent": str(node.get("intent", ""))[:240],
            "tags": node.get("tags", []),
            "strategy_hypothesis_present": _valid_hypothesis(node.get("strategy_hypothesis")),
            "strategy_hypothesis": (node.get("strategy_hypothesis")
                                    if isinstance(node.get("strategy_hypothesis"), dict) else None),
            "program_fingerprint": node.get("program_fingerprint") or evaluation.get("program_identity", {}).get("raw_code_sha256"),
            "structure_fingerprint": node.get("structure_fingerprint") or evaluation.get("program_identity", {}).get("structural_sha256"),
            "valid": bool(evaluation.get("valid")),
            "validation_loss": evaluation.get("loss"),
            "failure_type": evaluation.get("failure_type"),
        })
    return {
        "source": "frozen search prefix and proposals already observed in this run",
        "test_data_access": False,
        "quality_level": {
            "incumbent_validation_loss": incumbent_loss,
            "best_validation_loss": ranked[0]["evaluation"]["loss"] if ranked else None,
            "median_valid_validation_loss": median([n["evaluation"]["loss"] for n in valid]) if valid else None,
            "valid_program_count": len(valid),
        },
        "recorded_program_summaries": [
            {"node_id": n["id"], "intent": str(n.get("intent", ""))[:240],
             "tags": n.get("tags", []), "validation_loss": n["evaluation"]["loss"],
             "strategy_hypothesis": n.get("strategy_hypothesis")}
            for n in ranked[:5]
        ],
        "observed_failures": [
            {"node_id": n["id"], "failure_type": n.get("evaluation", {}).get("failure_type"),
             "proposal_failure": n.get("proposal_failure"), "tags": n.get("tags", [])}
            for n in nodes if not n.get("evaluation", {}).get("valid")
        ][-5:],
        "not_yet_observed_operator_tags": [tag for tag in available_tags if tag not in represented],
        "direction_evidence": evidence_nodes,
        "strategy_hypothesis_status": {
            "present_count": sum(item["strategy_hypothesis_present"] for item in evidence_nodes),
            "missing_count": sum(not item["strategy_hypothesis_present"] for item in evidence_nodes),
            "interpretation": "Missing hypotheses are missing evidence, not negative evidence; EG must not treat them as zero-value directions.",
        },
    }


class PhaseBState:
    """Track one frozen action, keeping project progress distinct from global gain."""

    def __init__(self, checkpoint: dict, strategy: str, steps: int = 8):
        if strategy not in STRATEGIES or steps != 8:
            raise ValueError("phase B requires I/B/E0/EG and exactly eight proposals")
        if strategy == "B" and not checkpoint.get("branch"):
            raise ValueError("branch-unavailable checkpoints cannot start B")
        if not checkpoint.get("incumbent"):
            raise ValueError("checkpoint has no valid incumbent")
        self.strategy, self.steps = strategy, steps
        self.nodes = copy.deepcopy(checkpoint["nodes"])
        self.by_id = {n["id"]: n for n in self.nodes}
        self.start_incumbent_id = int(checkpoint["incumbent"]["id"])
        self.start_branch_id = int(checkpoint["branch"]["id"]) if checkpoint.get("branch") else None
        self.start_incumbent_loss = float(checkpoint["incumbent"]["loss"])
        self.global_best_id = self.start_incumbent_id
        self.project_best_id = (self.start_incumbent_id if strategy == "I" else
                                 self.start_branch_id if strategy == "B" else None)
        self.investment_id = f"{strategy}:{checkpoint['checkpoint_id']}:0" if strategy in {"I", "B"} else None
        self.lineage_id = self.investment_id
        self.exploration_root_id = None
        self.behavior_cell_id: dict[int, str | None] = {}
        self.behavior_representatives: dict[str, list] = {}
        self.behavior_serial = 0
        self.decisions, self.events = [], []
        self.pending = None
        for node in self.nodes:
            identity = node.get("evaluation", {}).get("program_identity", {})
            node.setdefault("program_fingerprint", identity.get("raw_code_sha256"))
            node.setdefault("structure_fingerprint", identity.get("structural_sha256"))
            cell_id = self._assign_behavior_cell(node.get("evaluation", {}))
            self.behavior_cell_id[node["id"]] = cell_id
            node["behavior_cell_id"] = cell_id

    def _assign_behavior_cell(self, evaluation: dict) -> str | None:
        behavior = evaluation.get("behavior")
        if not evaluation.get("valid") or not isinstance(behavior, list) or not behavior:
            return None
        matches = []
        for cell_id, representative in self.behavior_representatives.items():
            distance = benchmarks.behavior_distance(behavior, representative)
            if distance <= BEHAVIOR_RADIUS:
                matches.append((distance, cell_id))
        if matches:
            return min(matches)[1]
        cell_id = f"BC{self.behavior_serial:04d}"
        self.behavior_serial += 1
        self.behavior_representatives[cell_id] = copy.deepcopy(behavior)
        return cell_id

    @property
    def global_best(self):
        return self.by_id[self.global_best_id]

    @property
    def project_best(self):
        return self.by_id[self.project_best_id] if self.project_best_id is not None else None

    def _parent(self):
        if self.strategy in {"E0", "EG"} and self.exploration_root_id is None:
            return None
        return self.project_best

    def choose(self, step: int) -> dict:
        if self.pending is not None or step != len(self.decisions) or step >= self.steps:
            raise ValueError("phase-B step is not contiguous")
        parent = self._parent()
        exploring = self.strategy in {"E0", "EG"} and parent is None
        action = "explore" if exploring else "develop"
        evidence = {
            "same_checkpoint": True,
            "checkpoint_incumbent_validation_loss": self.start_incumbent_loss,
            "project_best_validation_loss": self.project_best["evaluation"]["loss"] if self.project_best else None,
        }
        instrumentation = None
        if self.strategy in {"E0", "EG"} and exploring:
            evidence["required_strategy_hypothesis"] = {
                "target_failure": "a concrete observed limitation or failure",
                "mechanism": "why the proposed change could address it",
                "expected_behavior_change": "an observable change in route construction",
                "falsifiable_prediction": "a search-time quality prediction",
            }
        if self.strategy == "EG" and exploring:
            summary_started = time.perf_counter()
            summary = history_summary(self.nodes, self.start_incumbent_loss)
            evidence["historical_search_summary"] = summary
            instrumentation = {
                "historical_search_summary_sha256": digest(summary),
                "history_summary_source_node_ids": [n["id"] for n in self.nodes],
                "history_summary_build_wall_seconds": time.perf_counter() - summary_started,
                "history_summary_model_calls": 0,
            }
        decision = {
            "step": step, "strategy": self.strategy, "action": action,
            "target": TAGS["tsp"][step % len(TAGS["tsp"])],
            "parent": _brief(parent), "reference": None,
            "allocation": {"investment_id": self.investment_id,
                           "lineage_id": self.lineage_id,
                           "project_best_id": self.project_best_id,
                           "exploration_root_id": self.exploration_root_id,
                           "start_incumbent_id": self.start_incumbent_id,
                           "start_branch_id": self.start_branch_id},
            "instrumentation": instrumentation,
            "evidence": evidence,
        }
        self.pending = copy.deepcopy(decision)
        self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def observe(self, node: dict, costs: dict | None = None) -> dict:
        if self.pending is None:
            raise ValueError("no pending phase-B decision")
        decision = self.pending
        if node.get("parent_id") != (decision["parent"]["id"] if decision["parent"] else None):
            raise ValueError("candidate parent differs from the frozen decision")
        ev = node.get("evaluation", {})
        valid, loss = bool(ev.get("valid")), ev.get("loss")
        before_global = self.global_best["evaluation"].get("loss")
        before_project = self.project_best["evaluation"].get("loss") if self.project_best else None
        parent = self.by_id.get(node.get("parent_id"))
        parent_loss = parent.get("evaluation", {}).get("loss") if parent else None
        node["behavior_cell_id"] = self._assign_behavior_cell(ev)
        self.behavior_cell_id[node["id"]] = node["behavior_cell_id"]
        identity = ev.get("program_identity", {})
        node.setdefault("program_fingerprint", identity.get("raw_code_sha256"))
        node.setdefault("structure_fingerprint", identity.get("structural_sha256"))
        self.nodes.append(copy.deepcopy(node))
        self.by_id[node["id"]] = node
        global_gain = before_global - loss if valid and before_global is not None else None
        project_gain = before_project - loss if valid and before_project is not None else None
        project_progress = bool(project_gain is not None and project_gain > 1e-4)
        qualifies_as_generated_project = (
            self.strategy not in {"E0", "EG"} or
            decision["action"] != "explore" or
            _valid_hypothesis(node.get("strategy_hypothesis")))
        if valid and (before_global is None or loss < before_global):
            self.global_best_id = node["id"]
        if self.strategy == "I":
            self.project_best_id = self.global_best_id
        elif self.strategy in {"E0", "EG"} and decision["action"] == "explore":
            if valid and qualifies_as_generated_project:
                self.investment_id = f"{self.strategy}:{self.start_incumbent_id}:root-{node['id']}"
                self.lineage_id = f"L:{self.investment_id}"
                self.exploration_root_id = node["id"]
                self.project_best_id = node["id"]
            elif valid:
                node["exploration_admission_failure"] = "missing_or_incomplete_strategy_hypothesis"
                self.nodes[-1]["exploration_admission_failure"] = node["exploration_admission_failure"]
                self.by_id[node["id"]]["exploration_admission_failure"] = node["exploration_admission_failure"]
        elif valid and project_progress:
            self.project_best_id = node["id"]
        event = {
            "step": decision["step"], "node_id": node["id"], "strategy": self.strategy,
            "action": decision["action"], "valid": valid, "loss": loss,
            "parent_id": node.get("parent_id"), "parent_gain": parent_loss - loss if valid and parent_loss is not None else None,
            "global_gain": global_gain, "project_gain": project_gain,
            "global_improvement": bool(global_gain is not None and global_gain > 1e-4),
            "project_progress": project_progress,
            "investment_id": self.investment_id, "lineage_id": self.lineage_id,
            "exploration_admitted": bool(decision["action"] == "explore" and valid and qualifies_as_generated_project),
            "strategy_hypothesis_valid": _valid_hypothesis(node.get("strategy_hypothesis")),
            "behavior_cell_id": node.get("behavior_cell_id"),
            "exploration_followup": self.exploration_root_id is not None and decision["step"] > 0,
            "costs": copy.deepcopy(costs or {}),
        }
        self.events.append(event)
        self.pending = None
        return event

    def snapshot(self) -> dict:
        return {"strategy": self.strategy, "steps": self.steps,
                "start_incumbent_id": self.start_incumbent_id, "start_branch_id": self.start_branch_id,
                "start_incumbent_loss": self.start_incumbent_loss, "global_best_id": self.global_best_id,
                "project_best_id": self.project_best_id, "investment_id": self.investment_id,
                "lineage_id": self.lineage_id, "exploration_root_id": self.exploration_root_id,
                "behavior_cell_id": self.behavior_cell_id,
                "behavior_representatives": self.behavior_representatives,
                "behavior_serial": self.behavior_serial, "decisions": self.decisions,
                "events": self.events, "nodes": self.nodes}


def restore_phase_b(checkpoint: dict, saved: dict) -> PhaseBState:
    data = saved["state"]
    state = PhaseBState(checkpoint, data["strategy"], data["steps"])
    for key, value in data.items():
        setattr(state, key, copy.deepcopy(value))
    state.by_id = {node["id"]: node for node in state.nodes}
    state.pending = None
    return state


def _append_history_prompt(prompt: str, decision: dict) -> str:
    if decision["strategy"] not in {"E0", "EG"} or decision["action"] != "explore":
        return prompt
    return (prompt + "\n\nPropose one distinct, testable direction using only information available "
            "in the request. Return strategy_hypothesis with target_failure, mechanism, "
            "expected_behavior_change, and falsifiable_prediction. Keep the executable "
            "candidate interface unchanged.")


def run_phase_b(job: dict, checkpoint: dict, snapshot: dict, directory: Path,
                binding: dict, parameters: dict, transport, *, mode: str = "live") -> dict:
    """Run one eight-proposal task. Test snapshots are never accepted by this function."""
    if mode not in {"live", "fixture"}:
        raise ValueError("mode must be live or fixture")
    if mode == "live" and not callable(getattr(transport, "set_wall_deadline", None)):
        raise ValueError("live transport must enforce the frozen wall deadline")
    if job["strategy"] not in STRATEGIES or snapshot.get("test") is not None:
        raise ValueError("invalid treatment or test data supplied to search runner")
    if checkpoint["continuation"]["block"] != snapshot["block"]:
        raise ValueError("checkpoint and search snapshot block differ")
    directory = Path(directory)
    config = {"schema": "chapter6-phase-b-run-v1", **job,
              "execution_mode": mode, "checkpoint_sha256": digest(checkpoint),
              "search_snapshot_sha256": digest(snapshot), "binding": binding,
              "parameters": parameters, "source": source_record(), "test_access": False}
    with run_lock(directory):
        save_json(directory / "config.json", config, immutable=True)
        result_path, checkpoint_path = directory / "search_result.json", directory / "checkpoint.json"
        if result_path.exists():
            result = read_json(result_path)
            if result.get("config") != config:
                raise ValueError("completed phase-B config differs")
            return result
        if checkpoint_path.exists():
            saved = read_json(checkpoint_path)
            if saved.get("config") != config:
                raise ValueError("phase-B checkpoint config differs")
            state, records = restore_phase_b(checkpoint, saved), saved.get("records", [])
        else:
            state, records = PhaseBState(checkpoint, job["strategy"], 8), []
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(), "records": records}, immutable=True)
        started = time.perf_counter()
        calls = DiagnosticDurableCalls(directory, config, transport)
        status, stop_details = "continuation_complete", {}
        for step in range(len(records), 8):
            decision = state.choose(step)
            slot = directory / "slots" / f"{step:03d}"
            save_json(slot / "decision.json", {"decision": decision}, immutable=True)
            plan = {"name": f"phase_b_{step}", "intent": "unavailable", "tags": [decision["target"]]}
            code, failure = "", None
            try:
                prompt = planner_prompt("tsp", {
                    "target": decision["target"], "action": decision["action"],
                    "parent": _prompt_node(decision["parent"]), "reference": None,
                    "evidence": decision["evidence"],
                }, step)
                prompt = _append_history_prompt(prompt, decision)
                _check_request_budget(calls, parameters, started, SYSTEM, prompt,
                                      parameters["planner_max_tokens"], transport)
                planner_response = calls.complete(
                    step, "planner", SYSTEM, prompt, parameters["planner_max_tokens"])
                _check_completed_call_usage(calls, parameters)
                parsed, failure = _parse_response(planner_response, "planner")
                if failure is None:
                    plan.update(parsed)
                    coder_prompt_text = coder_prompt("tsp", plan, {"target": decision["target"],
                        "action": decision["action"], "parent": _prompt_node(decision["parent"]),
                        "reference": None, "evidence": decision["evidence"]})
                    _check_request_budget(calls, parameters, started, SYSTEM, coder_prompt_text,
                                          parameters["coder_max_tokens"], transport)
                    coder_response = calls.complete(step, "coder", SYSTEM, coder_prompt_text,
                                                    parameters["coder_max_tokens"])
                    _check_completed_call_usage(calls, parameters)
                    coded, failure = _parse_response(coder_response, "coder")
                    if failure is None:
                        code = coded.get("code", "")
                        if not isinstance(code, str):
                            code, failure = "", {"kind": "proposal_parse_failure", "stage": "coder"}
            except BudgetStop as exc:
                status, stop_details = "budget_exhausted", {"reason": str(exc), "reserved_step": step}
                break
            except (IndeterminateCall, ProviderFailure) as exc:
                status, stop_details = "infrastructure_incomplete", {
                    "error_type": type(exc).__name__, "reserved_step": step,
                    "diagnostics": getattr(exc, "diagnostics", None), "no_automatic_retry": True}
                break
            hypothesis = plan.get("strategy_hypothesis") if job["strategy"] in {"E0", "EG"} else None
            node = {"id": max(state.by_id) + 1, "name": str(plan.get("name", "candidate"))[:80],
                    "intent": str(plan.get("intent", ""))[:800], "strategy_hypothesis": hypothesis,
                    "tags": plan.get("tags", []), "code": code, "source": "live_llm",
                    "parent_id": decision["parent"]["id"] if decision["parent"] else None,
                    "reference_id": None, "action": decision["action"],
                    "allocation": decision["allocation"], "proposal_failure": failure,
                    "evaluation": evaluate_search(code, snapshot)}
            identity = node["evaluation"].get("program_identity", {})
            node["program_fingerprint"] = identity.get("raw_code_sha256")
            node["structure_fingerprint"] = identity.get("structural_sha256")
            save_json(slot / "candidate.json", node, immutable=True)
            usage = calls.usage()
            prior = records[-1].get("usage_after", {"known_tokens": 0, "call_attempts": 0}) if records else {"known_tokens": 0, "call_attempts": 0}
            instrumentation = decision.get("instrumentation") or {}
            costs = {"requests_added": usage["call_attempts"] - prior.get("call_attempts", 0),
                     "tokens_added": usage.get("known_tokens") - prior.get("known_tokens", 0)
                                    if usage.get("usage_complete") else None,
                     "complete_responses": 0 if failure else 2,
                     "evaluation_seconds": node["evaluation"].get("wall_seconds"),
                     "history_summary_build_wall_seconds": instrumentation.get("history_summary_build_wall_seconds", 0),
                     "history_summary_model_calls": instrumentation.get("history_summary_model_calls", 0)}
            event = state.observe(node, costs)
            records.append({"decision": decision, "node": node, "event": event,
                            "costs": costs, "usage_after": usage})
            save_json(checkpoint_path, {"config": config, "state": state.snapshot(), "records": records}, immutable=False)
        selections = {}
        for prefix in (4, 8):
            if len(records) < prefix:
                selections[str(prefix)] = {"status": "missing_prefix", "completed_proposals": len(records)}
                continue
            prefix_nodes = list(checkpoint["nodes"]) + [record["node"] for record in records[:prefix]]
            valid = [node for node in prefix_nodes if node.get("evaluation", {}).get("valid")]
            best = min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"])) if valid else None
            selections[str(prefix)] = {"status": "frozen_on_validation", "selected_on": "validation",
                "strategy": job["strategy"], "prefix_proposals": prefix,
                "best_id": best["id"] if best else None, "code": best.get("code", "") if best else "",
                "validation_loss": best["evaluation"].get("loss") if best else None,
                "checkpoint_incumbent_loss": state.start_incumbent_loss}
        save_json(directory / "selection_candidates.json", selections, immutable=True)
        usage = calls.usage()
        result = {"status": status, "stop_details": stop_details, "config": config,
            "summary": {"strategy": job["strategy"], "completed_proposals": len(records),
                "known_tokens": usage.get("known_tokens"), "request_count": usage.get("call_attempts"),
                "usage_complete": usage.get("usage_complete"),
                "global_improvements": sum(e["global_improvement"] for e in state.events),
                "project_progress": sum(e["project_progress"] for e in state.events),
                "exploration_admissions": sum(e["exploration_admitted"] for e in state.events),
                "hypothesis_valid_count": sum(e["strategy_hypothesis_valid"] for e in state.events),
                "new_model_calls": 0 if mode == "fixture" else usage.get("call_attempts", 0),
                "wall_seconds": time.perf_counter() - started},
            "usage": usage, "selections": selections,
            "state_sha256": digest(state.snapshot()),
            "selection_candidates_sha256": file_sha(directory / "selection_candidates.json")}
        save_json(result_path, result, immutable=True)
        save_json(directory / "status.json", {"status": status, "completed_proposals": len(records)}, immutable=True)
        return result
