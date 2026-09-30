"""Auditable search-state controller; it performs no model or evaluator calls."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _hypothesis(value: Any) -> dict[str, str]:
    """Keep falsifiable fields; cosmetic names never define project identity."""
    value = value if isinstance(value, dict) else {}
    return {
        "target_failure": _clean_text(value.get("target_failure", "")),
        "mechanism": _clean_text(value.get("mechanism", "")),
        "expected_behavior_change": _clean_text(value.get("expected_behavior_change",
                                                           value.get("expected_behavior", ""))),
        "falsifiable_prediction": _clean_text(value.get("falsifiable_prediction",
                                                         value.get("prediction", ""))),
    }


FIXED_BASELINE_PATTERN = ("I", "I", "B", "E0")
MIN_UNRESERVED_GLOBAL_SLOTS = 1


def fixed_baseline_action(step: int) -> str:
    """Phase-C fixed 32/16/16 allocation, repeated over four-slot blocks."""
    if step < 0:
        raise ValueError("step must be nonnegative")
    return FIXED_BASELINE_PATTERN[step % len(FIXED_BASELINE_PATTERN)]


@dataclass(frozen=True)
class ControllerConfig:
    proposal_budget: int = 64
    pool_capacity: int = 2
    trial_slots: int = 4
    renewal_slots: int = 2
    lineage_slot_cap: int = 8
    quality_tolerance: float = 0.035
    gain_epsilon: float = 1e-4
    max_no_progress: int = 2
    exploration_period: int = 8
    context_cards: int = 6

    def __post_init__(self):
        if min(self.proposal_budget, self.pool_capacity, self.trial_slots,
               self.renewal_slots, self.lineage_slot_cap, self.exploration_period) <= 0:
            raise ValueError("budgets and capacities must be positive")
        if self.trial_slots > self.lineage_slot_cap:
            raise ValueError("trial_slots cannot exceed lineage_slot_cap")
        if self.quality_tolerance < 0 or self.gain_epsilon < 0:
            raise ValueError("quality thresholds must be nonnegative")


class MinimalMechanismController:
    """A/B/M state and G/P/F rules over evaluator-produced candidate records.

    The adapter supplies program/structure fingerprints, behavior cells,
    validation losses, and real request/evaluation costs. No field is inferred
    from an agent's direction name alone.
    """

    ACTIONS = ("I", "E")

    def __init__(self, incumbent: dict[str, Any], *, config: ControllerConfig | None = None,
                 use_g: bool = True, use_p: bool = True, use_f: bool = True):
        self.config = config or ControllerConfig()
        self.use_g, self.use_p, self.use_f = bool(use_g), bool(use_p), bool(use_f)
        self.proposals_used = 0
        self.pending: dict[str, Any] | None = None
        self.incumbent_id = str(incumbent["node_id"])
        self.incumbent_loss = self._loss(incumbent)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.node_investment_id: dict[str, str | None] = {}
        self.node_lineage_id: dict[str, str | None] = {}
        self.node_behavior_cell_id: dict[str, str] = {}
        self.program_fingerprints: set[str] = set()
        self.direction_archive: dict[str, dict[str, Any]] = {}
        self.investments: dict[str, dict[str, Any]] = {}
        self.investment_pool: dict[str, dict[str, Any]] = {}
        self.lineages: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self.global_reward_ledger: list[dict[str, Any]] = []
        self.project_reward_ledger: list[dict[str, Any]] = []
        self.matured_action_rates: list[dict[str, Any]] = []
        self.action_rate_history: dict[str, list[float]] = {"I": [], "E": [], "B": []}
        self.serial = {"investment": 0, "lineage": 0, "decision": 0, "commitment": 0}
        self.last_explore_step = 0
        self._register_seed(incumbent)

    @staticmethod
    def _loss(record: dict[str, Any]) -> float:
        value = record.get("validation_loss", record.get("loss"))
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("candidate needs a finite search-time validation loss")
        return float(value)

    @staticmethod
    def _program_fp(record: dict[str, Any]) -> str:
        value = record.get("program_fingerprint")
        if not value:
            raise ValueError("candidate needs an evaluator-produced canonical program_fingerprint")
        return str(value)

    @staticmethod
    def _structure_fp(record: dict[str, Any]) -> str:
        value = record.get("structure_fingerprint")
        if not value:
            raise ValueError("candidate needs an evaluator-produced structure_fingerprint")
        return str(value)

    def _cell(self, record: dict[str, Any]) -> str:
        value = record.get("behavior_cell_id")
        if not value:
            raise ValueError("candidate needs a behavior_cell_id from the task evaluator")
        value = self.resolve_behavior_cell(str(value))
        return value

    def _register_seed(self, record: dict[str, Any]) -> None:
        node_id = str(record["node_id"])
        cell = self._cell(record)
        node = copy.deepcopy(record)
        node.update(node_id=node_id, validation_loss=self._loss(record), valid=True)
        self.nodes[node_id] = node
        self.program_fingerprints.add(self._program_fp(record))
        self.node_investment_id[node_id] = record.get("investment_id") if self.use_p else None
        self.node_lineage_id[node_id] = record.get("lineage_id") if self.use_p else None
        self.node_behavior_cell_id[node_id] = cell
        self.direction_archive[cell] = {
            "behavior_cell_id": cell, "representative_node_id": node_id,
            "best_validation_loss": node["validation_loss"], "member_node_ids": [node_id],
            "strategy_hypothesis": _hypothesis(record.get("strategy_hypothesis")),
            "program_fingerprint": self._program_fp(record),
            "structure_fingerprint": self._structure_fp(record),
            "failures": [], "first_search_step": -1,
        }

    def resolve_behavior_cell(self, cell_id: str) -> str:
        path = []
        while cell_id in self.aliases:
            path.append(cell_id)
            cell_id = self.aliases[cell_id]
        for alias in path:
            self.aliases[alias] = cell_id
        return cell_id

    def merge_behavior_cells(self, source: str, target: str, *, evidence: str) -> dict[str, Any]:
        """Merge archive labels only; investment identities and budgets survive."""
        source, target = self.resolve_behavior_cell(source), self.resolve_behavior_cell(target)
        if source == target:
            return {"merged": False, "source": source, "target": target}
        if source not in self.direction_archive or target not in self.direction_archive:
            raise KeyError("both behavior cells must exist in the direction archive")
        left, right = self.direction_archive[source], self.direction_archive[target]
        right["member_node_ids"].extend(left["member_node_ids"])
        if left["best_validation_loss"] < right["best_validation_loss"]:
            right["best_validation_loss"] = left["best_validation_loss"]
            right["representative_node_id"] = left["representative_node_id"]
        del self.direction_archive[source]
        self.aliases[source] = target
        for node_id in right["member_node_ids"]:
            self.node_behavior_cell_id[node_id] = target
        self.events.append({"event": "behavior_cells_merged", "source": source,
                            "target": target, "evidence": str(evidence),
                            "investment_ids_preserved": True})
        for project in self.investments.values():
            project["behavior_cells"] = list(dict.fromkeys(
                target if c == source else self.resolve_behavior_cell(c)
                for c in project["behavior_cells"]))
        return {"merged": True, "source": source, "target": target}

    def _new_lineage(self) -> str:
        value = f"L{self.serial['lineage']:04d}"
        self.serial["lineage"] += 1
        self.lineages[value] = {"lineage_id": value, "spent_proposals": 0,
                                "reserved_proposals": 0, "awarded_slots": 0,
                                "investment_ids": []}
        return value

    def _new_investment(self, outcome: dict[str, Any], lineage_id: str,
                        *, kind: str, initial_tokens: int | None) -> dict[str, Any]:
        investment_id = f"P{self.serial['investment']:04d}"
        self.serial["investment"] += 1
        cell = self._cell(outcome)
        project = {
            "investment_id": investment_id, "lineage_id": lineage_id,
            "status": "archived", "entry_kind": kind,
            "root_node_id": str(outcome["node_id"]), "best_node_id": str(outcome["node_id"]),
            "best_loss": self._loss(outcome), "best_structure_fingerprint": self._structure_fp(outcome),
            "strategy_hypothesis": _hypothesis(outcome.get("strategy_hypothesis")),
            "hypothesis_key": _hash(_hypothesis(outcome.get("strategy_hypothesis"))),
            "behavior_cells": [cell], "tranche_kind": None, "tranche_remaining": 0,
            "tranche_start_best": self._loss(outcome), "tranche_start_tokens": 0,
            "tranche_gain": 0.0, "tranche_cost_tokens": 0,
            "tranche_cost_complete": True, "tranche_missing_outcomes": 0,
            "last_mature_roi": None, "last_mature_global_gain": 0.0,
            "last_mature_e_roi": None, "last_mature_b_roi": None,
            "no_progress_streak": 0, "total_progress_events": 0,
            "tokens_total": 0, "tokens_complete": True,
            "global_gain_credit": 0.0, "created_step": self.proposals_used - 1,
            "trial_count": 0, "renewal_count": 0, "exit_reason": None,
            "diagnostic_only": False, "commitments": [], "active_commitment_index": None,
        }
        self.investments[investment_id] = project
        self.lineages[lineage_id]["investment_ids"].append(investment_id)
        self.node_investment_id[str(outcome["node_id"])] = investment_id
        self.node_lineage_id[str(outcome["node_id"])] = lineage_id
        project["tokens_total"] = initial_tokens or 0
        project["tokens_complete"] = initial_tokens is not None
        return project

    @property
    def reserved_proposals(self) -> int:
        return sum(int(p["tranche_remaining"]) for p in self.investment_pool.values())

    @property
    def unreserved_future_proposals(self) -> int:
        return max(0, self.config.proposal_budget - self.proposals_used - self.reserved_proposals)

    def _grant(self, project: dict[str, Any], kind: str) -> tuple[bool, str, int]:
        requested = self.config.trial_slots if kind == "trial" else self.config.renewal_slots
        lineage = self.lineages[project["lineage_id"]]
        future = self.config.proposal_budget - self.proposals_used
        lineage_left = self.config.lineage_slot_cap - lineage["spent_proposals"] - lineage["reserved_proposals"]
        pool_room = project["investment_id"] in self.investment_pool or len(self.investment_pool) < self.config.pool_capacity
        if not pool_room:
            return False, "active_pool_full", 0
        global_free = future - self.reserved_proposals
        if global_free - requested < MIN_UNRESERVED_GLOBAL_SLOTS:
            return False, "no_redeemable_global_budget", 0
        if lineage_left < requested:
            return False, "lineage_budget_exhausted", 0
        project["status"] = "active"
        project["tranche_kind"] = kind
        project["tranche_remaining"] = requested
        project["tranche_start_best"] = project["best_loss"]
        project["tranche_start_tokens"] = project["tokens_total"]
        project["tranche_gain"] = 0.0
        project["tranche_cost_tokens"] = project["tokens_total"] if kind == "trial" else 0
        project["tranche_cost_complete"] = project["tokens_complete"] if kind == "trial" else True
        project["tranche_missing_outcomes"] = 0
        project["last_mature_roi"] = None
        project["exit_reason"] = None
        commitment = {
            "commitment_id": f"K{self.serial['commitment']:05d}",
            "kind": kind, "awarded_slots": requested, "redeemed_slots": 0,
            "forfeited_slots": 0, "status": "active", "start_step": self.proposals_used,
        }
        self.serial["commitment"] += 1
        project["commitments"].append(commitment)
        project["active_commitment_index"] = len(project["commitments"]) - 1
        self.investment_pool[project["investment_id"]] = project
        lineage["reserved_proposals"] += requested
        lineage["awarded_slots"] += requested
        if kind == "trial":
            project["trial_count"] += 1
        else:
            project["renewal_count"] += 1
        self.events.append({"event": "budget_commitment", "investment_id": project["investment_id"],
                            "commitment_id": commitment["commitment_id"],
                            "lineage_id": project["lineage_id"], "kind": kind,
                            "slots": requested, "redeemable_future_slots": future,
                            "unreserved_floor_after_grant": global_free - requested,
                            "lineage_slots_left_before": lineage_left})
        return True, "funded", requested

    def _mature_tranche(self, project: dict[str, Any]) -> dict[str, Any]:
        missing = project["tranche_missing_outcomes"]
        gain = max(0.0, project["tranche_start_best"] - project["best_loss"])
        complete = project["tranche_cost_complete"] and missing == 0
        tokens = project["tranche_cost_tokens"]
        roi = gain / tokens if complete and tokens > 0 else None
        record = {
            "investment_id": project["investment_id"], "lineage_id": project["lineage_id"],
            "tranche_kind": project["tranche_kind"], "start_best_loss": project["tranche_start_best"],
            "end_best_loss": project["best_loss"], "local_gain": gain,
            "global_gain_credit": project.pop("tranche_global_gain", 0.0),
            "cost_tokens": tokens if complete else None, "cost_complete": complete,
            "missing_outcomes": missing, "mature": True, "local_gain_per_token": roi,
            "proposals": (self.config.trial_slots if project["tranche_kind"] == "trial"
                          else self.config.renewal_slots),
        }
        self.project_reward_ledger.append(record)
        project["last_mature_roi"] = roi
        project["last_mature_global_gain"] = record["global_gain_credit"]
        family = ("E" if project["entry_kind"] == "exploration"
                  and project["tranche_kind"] == "trial" else "B")
        record["action_family"] = family
        project[f"last_mature_{family.lower()}_roi"] = roi
        if roi is not None:
            self.action_rate_history[family].append(roi)
            self.matured_action_rates.append({"family": family, "roi": roi,
                                              "investment_id": project["investment_id"],
                                              "tranche_kind": project["tranche_kind"]})
        return record

    def _alternative_roi(self, investment_id: str) -> tuple[float | None, str]:
        values = list(self.action_rate_history["I"])
        for pid, project in self.investments.items():
            if pid == investment_id:
                continue
            values.extend(rate for rate in (project.get("last_mature_e_roi"),
                                             project.get("last_mature_b_roi"))
                          if rate is not None)
        if not values:
            return 0.0, "cold_start_zero_floor"
        return max(float(x) for x in values), "best_mature_alternative_roi"

    def _renewal_eligibility(self, project: dict[str, Any], maturity: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        alternative, source = self._alternative_roi(project["investment_id"])
        gap = project["best_loss"] - self.incumbent_loss
        progress = maturity["local_gain"] > self.config.gain_epsilon
        no_stagnation = project["no_progress_streak"] <= self.config.max_no_progress
        roi = maturity["local_gain_per_token"]
        roi_beats_cost = roi is not None and roi > alternative
        lineage = self.lineages[project["lineage_id"]]
        lineage_left = self.config.lineage_slot_cap - lineage["spent_proposals"] - lineage["reserved_proposals"]
        future_free = self.config.proposal_budget - self.proposals_used - self.reserved_proposals
        eligible = (progress and gap <= self.config.quality_tolerance and no_stagnation
                    and roi_beats_cost and lineage_left >= self.config.renewal_slots
                    and future_free >= (self.config.renewal_slots + MIN_UNRESERVED_GLOBAL_SLOTS))
        checks = {"project_progress": progress, "quality_gap": gap,
                  "quality_gap_ok": gap <= self.config.quality_tolerance,
                  "no_progress_streak": project["no_progress_streak"],
                  "no_stagnation": no_stagnation, "project_roi": roi,
                  "opportunity_cost_roi": alternative, "opportunity_cost_source": source,
                  "roi_beats_opportunity_cost": roi_beats_cost,
                  "lineage_slots_left": lineage_left,
                  "future_unreserved_slots": future_free,
                  "budget_redeemable": (lineage_left >= self.config.renewal_slots
                                         and future_free >= (self.config.renewal_slots
                                                             + MIN_UNRESERVED_GLOBAL_SLOTS))}
        if not progress:
            reason = "no_project_best_progress"
        elif gap > self.config.quality_tolerance:
            reason = "quality_gap_too_large"
        elif not no_stagnation:
            reason = "stagnation_limit"
        elif not roi_beats_cost:
            reason = "opportunity_cost_not_beaten_or_unmeasured"
        elif not checks["budget_redeemable"]:
            reason = "no_redeemable_budget"
        else:
            reason = "eligible"
        return eligible, reason, checks

    def _close_tranche(self, project: dict[str, Any]) -> dict[str, Any]:
        commitment = project["commitments"][project["active_commitment_index"]]
        if (commitment["redeemed_slots"] + commitment["forfeited_slots"]
                != commitment["awarded_slots"]):
            raise RuntimeError("cannot mature a tranche with unaccounted committed slots")
        commitment["status"] = "redeemed"
        record = self._mature_tranche(project)
        project["status"] = "archived"
        project["exit_reason"] = "trial_complete" if project["tranche_kind"] == "trial" else "renewal_complete"
        self.investment_pool.pop(project["investment_id"], None)
        project["tranche_remaining"] = 0
        eligible, reason, checks = self._renewal_eligibility(project, record)
        renewal = False
        if self.use_p and eligible:
            renewal, grant_reason, _ = self._grant(project, "renewal")
            if not renewal:
                reason = grant_reason
        self.events.append({"event": "renewal_decision", "investment_id": project["investment_id"],
                            "triggered": eligible, "renewed": renewal,
                            "reason": reason if not renewal else "eligible_and_funded",
                            "checks": checks, "actual_pool_changed": renewal})
        project["exit_reason"] = None if renewal else reason
        return {"maturity": record, "renewal_triggered": eligible,
                "renewed": renewal, "renewal_reason": reason, "checks": checks}

    def _try_reentry(self, project: dict[str, Any], gain: float, tokens: int | None) -> None:
        if project["investment_id"] in self.investment_pool or gain <= self.config.gain_epsilon:
            return
        alternative, source = self._alternative_roi(project["investment_id"])
        rate = gain / tokens if tokens and tokens > 0 else None
        lineage = self.lineages[project["lineage_id"]]
        lineage_left = self.config.lineage_slot_cap - lineage["spent_proposals"] - lineage["reserved_proposals"]
        future_free = self.config.proposal_budget - self.proposals_used - self.reserved_proposals
        gap = project["best_loss"] - self.incumbent_loss
        checks = {"new_project_best_progress": gain, "quality_gap": gap,
                  "quality_gap_ok": gap <= self.config.quality_tolerance,
                  "observed_progress_per_token": rate,
                  "opportunity_cost_roi": alternative, "opportunity_cost_source": source,
                  "beats_opportunity_cost": rate is not None and rate > alternative,
                  "lineage_slots_left": lineage_left, "future_unreserved_slots": future_free}
        eligible = (checks["quality_gap_ok"] and checks["beats_opportunity_cost"]
                    and lineage_left >= self.config.renewal_slots
                    and future_free >= (self.config.renewal_slots
                                        + MIN_UNRESERVED_GLOBAL_SLOTS)
                    and project["no_progress_streak"] <= self.config.max_no_progress)
        renewed, reason = False, "not_eligible"
        if eligible:
            reason = "eligible_but_component_disabled" if not self.use_p else "eligible"
            if self.use_p:
                renewed, reason, _ = self._grant(project, "renewal")
        self.events.append({"event": "renewal_decision", "investment_id": project["investment_id"],
                            "triggered": eligible, "renewed": renewed,
                            "reason": reason if not renewed else "new_search_time_progress_reentry",
                            "checks": checks, "actual_pool_changed": renewed,
                            "decision_type": "new_search_time_progress_reentry"})

    def _context(self, through_step: int, *, force: bool = False) -> dict[str, Any] | None:
        if not self.use_g and not force:
            return None
        cards = sorted(self.direction_archive.values(),
                       key=lambda d: (d["best_validation_loss"], d["behavior_cell_id"]))[:self.config.context_cards]
        if not cards:
            return None
        return {
            "schema": "direction-context-v1", "visibility": "search_and_validation_only",
            "through_search_step": through_step,
            "cards": [{"behavior_cell_id": c["behavior_cell_id"],
                       "strategy_hypothesis": copy.deepcopy(c["strategy_hypothesis"]),
                       "best_validation_loss": c["best_validation_loss"],
                       "program_fingerprint": c["program_fingerprint"],
                       "structure_fingerprint": c["structure_fingerprint"],
                       "failures": list(c["failures"]),
                       "member_count": len(c["member_node_ids"])} for c in cards],
            "note": "Use only these logged search-time facts; state a testable strategy hypothesis and executable candidate.",
            "summary_model_calls": 0,
            "summary_generation": "deterministic serialization; no separate model call",
        }

    def _options(self, *, p_available: bool = True) -> list[dict[str, Any]]:
        options = [{"kind": "I", "investment_id": None, "parent_id": self.incumbent_id}]
        options.append({"kind": "E", "investment_id": None, "parent_id": None})
        if not self.use_p:
            branch = self._best_archived_branch()
            if branch is not None:
                options.append({"kind": "B", "investment_id": None,
                                "parent_id": branch["node_id"]})
        if self.use_p:
            for investment_id, project in self.investment_pool.items():
                if project["tranche_remaining"] <= 0:
                    continue
                if not p_available and project["tranche_kind"] == "renewal":
                    continue
                options.append({"kind": "B", "investment_id": investment_id,
                                "parent_id": project["best_node_id"]})
        return options

    def _best_archived_branch(self) -> dict[str, Any] | None:
        incumbent_fp = self._program_fp(self.nodes[self.incumbent_id])
        candidates = []
        for cell, card in self.direction_archive.items():
            node = self.nodes.get(str(card["representative_node_id"]))
            if node is None or not node.get("valid", True):
                continue
            loss = self._loss(node)
            if (self._program_fp(node) == incumbent_fp
                    or loss > self.incumbent_loss + self.config.quality_tolerance):
                continue
            candidates.append((loss, cell, node))
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[2]

    def _fixed_option(self, options: list[dict[str, Any]]) -> dict[str, Any]:
        if not options:
            raise RuntimeError("no actions available")
        if self.reserved_proposals and self.unreserved_future_proposals == 0:
            return self._reserved_option(options)
        scheduled = fixed_baseline_action(self.proposals_used)
        if scheduled == "E0":
            scheduled = "E"
        available = [option for option in options if option["kind"] == scheduled]
        if scheduled == "B" and available:
            return min(available, key=lambda option: (
                self._loss(self.nodes[str(option["parent_id"])]),
                str(option.get("investment_id") or "")))
        if available:
            return available[0]
        return next(option for option in options if option["kind"] == "I")

    def _reserved_option(self, options: list[dict[str, Any]]) -> dict[str, Any]:
        committed = [option for option in options
                     if option["kind"] == "B"
                     and option.get("investment_id") in self.investment_pool
                     and self.investment_pool[option["investment_id"]]["tranche_remaining"] > 0]
        if not committed:
            raise RuntimeError("redeemable proposal reservation has no active project")
        return min(committed, key=lambda option: (
            self.investment_pool[option["investment_id"]]["created_step"],
            option["investment_id"]))

    def _full_option(self, options: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        if not options:
            raise RuntimeError("no actions available")
        branch = [o for o in options if o["kind"] == "B"]
        free_after_reservations = self.config.proposal_budget - self.proposals_used - self.reserved_proposals
        if branch and free_after_reservations <= 0:
            return self._reserved_option(options), False
        # A fixed exploration floor prevents a cold or weakly sampled arm from
        # being treated as a measured zero-yield action.
        if self.proposals_used - self.last_explore_step >= self.config.exploration_period:
            explore = next(o for o in options if o["kind"] == "E")
            return explore, False
        rates = []
        known = False
        for option in options:
            if option["kind"] == "I":
                values = self.action_rate_history["I"]
                rate = sum(values[-8:]) / len(values[-8:]) if values else None
            elif option["kind"] == "E":
                values = self.action_rate_history["E"]
                rate = sum(values[-8:]) / len(values[-8:]) if values else None
            else:
                project = self.investment_pool.get(option.get("investment_id"))
                rate = project.get("last_mature_b_roi") if project is not None else None
            if rate is not None:
                known = True
            rates.append((float(rate) if rate is not None else None, option))
        if not known:
            return self._fixed_option(options), False
        # Unmatured returns remain unknown. When any outcome matures, unknown
        # actions use a zero tie floor but are still protected by exploration.
        best_rate = max((r for r, _ in rates if r is not None), default=0.0)
        tied = [o for r, o in rates if r is not None and r == best_rate]
        chosen = tied[0] if tied else self._fixed_option(options)
        fixed = self._fixed_option(options)
        return chosen, chosen["kind"] != fixed["kind"] or chosen.get("investment_id") != fixed.get("investment_id")

    def choose(self, step: int, *, forced_action: str | None = None,
               frozen_branch: dict[str, Any] | None = None,
               parent_override: str | None = None) -> dict[str, Any]:
        if self.pending is not None:
            raise ValueError("observe the pending proposal before choosing again")
        if step != self.proposals_used:
            raise ValueError("step must equal the contiguous proposal count")
        if self.proposals_used >= self.config.proposal_budget:
            raise ValueError("proposal budget exhausted")
        if forced_action is not None:
            if forced_action not in {"I", "B", "E0", "EG"}:
                raise ValueError("forced_action must be I, B, E0, or EG")
            if forced_action == "B":
                if not frozen_branch or "node_id" not in frozen_branch:
                    raise ValueError("B diagnostic requires the frozen checkpoint branch")
                requested_investment = frozen_branch.get("investment_id")
                if requested_investment in self.investment_pool:
                    option = {"kind": "B", "investment_id": requested_investment,
                              "parent_id": self.investment_pool[requested_investment]["best_node_id"]}
                else:
                    option = {"kind": "B", "investment_id": f"DIAG:{frozen_branch.get('checkpoint_id', 'branch')}",
                              "parent_id": str(frozen_branch["node_id"])}
            elif forced_action in {"E0", "EG"}:
                option = {"kind": "E", "investment_id": None, "parent_id": None}
            else:
                option = {"kind": "I", "investment_id": None, "parent_id": self.incumbent_id}
            informed = forced_action == "EG"
            mode = forced_action
        else:
            options = self._options()
            if self.use_f:
                option, f_changed = self._full_option(options)
            else:
                option = self._fixed_option(options)
                fixed_compare = self._full_option(options)[0]
                f_changed = option["kind"] != fixed_compare["kind"] or option.get("investment_id") != fixed_compare.get("investment_id")
            informed = self.use_g and option["kind"] == "E"
            mode = "EG" if informed and option["kind"] == "E" else "E0" if option["kind"] == "E" else option["kind"]
        if option["kind"] == "B" and str(option["investment_id"]).startswith("DIAG:"):
            parent_id = option["parent_id"]
            parent_record = copy.deepcopy(frozen_branch)
            existing_diag_id = self.node_investment_id.get(str(parent_id))
            if existing_diag_id in self.investments and self.investments[existing_diag_id].get("diagnostic_only"):
                option["investment_id"] = existing_diag_id
                option["lineage_id"] = self.investments[existing_diag_id]["lineage_id"]
            elif parent_id not in self.nodes:
                parent_record["node_id"] = str(parent_id)
                parent_record["validation_loss"] = self._loss(parent_record)
                parent_record.setdefault("valid", True)
                parent_record.setdefault("behavior_cell_id", f"diag:{parent_id}")
                parent_record.setdefault("program_fingerprint", f"diag-program:{parent_id}")
                parent_record.setdefault("structure_fingerprint", f"diag-structure:{parent_id}")
                parent_record.setdefault("strategy_hypothesis", {})
                self.nodes[str(parent_id)] = parent_record
                lineage_id = self._new_lineage()
                project = self._new_investment(parent_record, lineage_id,
                                               kind="frozen_branch_diagnostic", initial_tokens=0)
                project["diagnostic_only"] = True
                self.node_investment_id[str(parent_id)] = project["investment_id"]
                self.node_lineage_id[str(parent_id)] = lineage_id
                self.node_behavior_cell_id[str(parent_id)] = self._cell(parent_record)
                option["investment_id"] = project["investment_id"]
                option["lineage_id"] = lineage_id
        if option["kind"] == "B" and option.get("investment_id") in self.investment_pool:
            if parent_override is not None:
                parent_override = str(parent_override)
                if self.node_investment_id.get(parent_override) != option["investment_id"]:
                    raise ValueError("parent_override must belong to the selected investment")
                option["parent_id"] = parent_override
            parent_id = option["parent_id"]
            parent_record = copy.deepcopy(self.nodes.get(str(parent_id)))
        elif option["kind"] == "B" and option.get("investment_id") in self.investments:
            parent_id = option["parent_id"]
            parent_record = copy.deepcopy(self.nodes.get(str(parent_id)))
        else:
            parent_id = option["parent_id"]
            parent_record = copy.deepcopy(self.nodes.get(str(parent_id))) if parent_id is not None else None
        if option["kind"] == "B" and option["investment_id"] in self.investment_pool:
            project = self.investment_pool[option["investment_id"]]
            project["tranche_remaining"] -= 1
            commitment = project["commitments"][project["active_commitment_index"]]
            commitment["redeemed_slots"] += 1
            lineage = self.lineages[project["lineage_id"]]
            lineage["reserved_proposals"] -= 1
            project["tranche_global_gain"] = project.get("tranche_global_gain", 0.0)
        context = self._context(step - 1, force=forced_action == "EG") if informed else None
        if option["kind"] == "E":
            parent_id = None
            parent_record = None
        decision_id = f"D{self.serial['decision']:05d}"
        self.serial["decision"] += 1
        p_mask_effect = {
            "one_step_masked_counterfactual_applies": bool(self.use_p and forced_action is None),
            "one_step_masked_outer_action_changed": False,
            "one_step_masked_parent_changed": False,
            "one_step_masked_investment_target_changed": False,
        }
        if self.use_p and forced_action is None:
            live_pool = self.investment_pool
            self.investment_pool = {}
            try:
                self.use_p = False
                p_off_options = self._options()
                if self.use_f:
                    p_off_option, _ = self._full_option(p_off_options)
                else:
                    p_off_option = self._fixed_option(p_off_options)
            finally:
                self.use_p = True
                self.investment_pool = live_pool
            p_mask_effect.update({
                "one_step_masked_outer_action_changed": option["kind"] != p_off_option["kind"],
                "one_step_masked_parent_changed": option.get("parent_id") != p_off_option.get("parent_id"),
                "one_step_masked_investment_target_changed": (
                    option.get("investment_id") != p_off_option.get("investment_id")),
            })
        g_triggered = bool(option["kind"] == "E" and context and context["cards"])
        fixed_option = self._fixed_option(self._options())
        f_changed_actual = (option["kind"], option.get("investment_id")) != (fixed_option["kind"], fixed_option.get("investment_id"))
        decision = {
            "decision_id": decision_id, "step": step, "mode": mode,
            "action": option["kind"], "parent_id": parent_id,
            "parent_behavior_cell_id": (self.node_behavior_cell_id.get(str(parent_id)) if parent_id is not None else None),
            "parent_program_fingerprint": (parent_record.get("program_fingerprint") if parent_record else None),
            "investment_id": option.get("investment_id"),
            "lineage_id": (self.investments.get(option.get("investment_id"), {}).get("lineage_id")
                           if option.get("investment_id") else None),
            "diagnostic_frozen_branch": copy.deepcopy(frozen_branch) if forced_action == "B" else None,
            "prompt_input": {"contract": "strategy-hypothesis-v1", "parent": parent_record,
                             "history": context, "history_visibility": "search_and_validation_only",
                             "summary_model_calls": 0 if context else 0},
            "component_effects": {
                "G": {"triggered": g_triggered,
                      "request_context_changed": bool(g_triggered and context is not None),
                      "outer_action_change_applicable": False},
                "P": p_mask_effect,
                "F": {"triggered": bool(self.use_f and len(self._options()) > 1),
                      "actual_action_changed": bool(self.use_f and f_changed_actual)},
            },
            "f_scheduler_counterfactual_changed": bool(f_changed) if forced_action is None else False,
        }
        if option["kind"] == "E":
            decision["prompt_input"]["strategy_hypothesis_output_schema"] = {
                "required": ["target_failure", "mechanism", "expected_behavior_change",
                             "falsifiable_prediction"],
                "candidate": "executable program with evaluator fingerprints and behavior evidence",
            }
        self.pending = copy.deepcopy(decision)
        return copy.deepcopy(decision)

    def observe(self, outcome: dict[str, Any] | None, costs: dict[str, Any] | None = None,
                *, terminal_status: str = "completed") -> dict[str, Any]:
        if self.pending is None:
            raise ValueError("no pending proposal")
        if terminal_status not in {"completed", "invalid", "sent_unknown", "service_failed"}:
            raise ValueError("unknown terminal status")
        decision = self.pending
        costs = copy.deepcopy(costs or {})
        tokens = costs.get("total_tokens")
        if tokens is None and isinstance(costs.get("input_tokens"), int) and isinstance(costs.get("output_tokens"), int):
            tokens = costs["input_tokens"] + costs["output_tokens"]
        if not isinstance(tokens, int) or tokens < 0:
            tokens = None
        summary_tokens = costs.get("summary_input_tokens", 0)
        summary_calls = costs.get("summary_calls", 0)
        if not isinstance(summary_tokens, int) or summary_tokens < 0 or not isinstance(summary_calls, int) or summary_calls < 0:
            raise ValueError("summary costs must be nonnegative integer counts")
        if decision["prompt_input"].get("history") and summary_calls != 0:
            raise ValueError("this controller uses deterministic context serialization, not a summarizer model")
        if (summary_tokens and isinstance(costs.get("input_tokens"), int)
                and summary_tokens > costs["input_tokens"]):
            raise ValueError("history tokens are already part of the actual prompt input token count")
        if decision["action"] == "B" and decision.get("investment_id") in self.investment_pool:
            project = self.investment_pool[decision["investment_id"]]
            project["tranche_cost_tokens"] += tokens or 0
            project["tranche_cost_complete"] = project["tranche_cost_complete"] and tokens is not None
            project["tokens_total"] += tokens or 0
            project["tokens_complete"] = project["tokens_complete"] and tokens is not None
            if terminal_status != "completed" or not outcome or not outcome.get("valid"):
                project["no_progress_streak"] += 1
                if terminal_status in {"sent_unknown", "service_failed"}:
                    project["tranche_missing_outcomes"] += 1
        action = decision["action"]
        before_global = self.incumbent_loss
        valid = bool(outcome and outcome.get("valid") and terminal_status == "completed")
        record = copy.deepcopy(outcome) if outcome else {"node_id": f"missing-{decision['decision_id']}"}
        record["node_id"] = str(record["node_id"])
        record["parent_id"] = decision["parent_id"]
        record["action"] = decision["mode"]
        record["valid"] = valid
        record["terminal_status"] = terminal_status
        if tokens is not None:
            record["charged_tokens"] = tokens
        self.proposals_used += 1
        global_gain = 0.0
        local_gain = 0.0
        duplicate = False
        lineage_precharged = False
        investment_id = decision.get("investment_id")
        lineage_id = decision.get("lineage_id")
        if valid:
            loss = self._loss(record)
            cell = self._cell(record)
            fp, structure = self._program_fp(record), self._structure_fp(record)
            duplicate = fp in self.program_fingerprints
            record["validation_loss"] = loss
            record["program_fingerprint"] = fp
            record["structure_fingerprint"] = structure
            record["behavior_cell_id"] = cell
            parent_investment = self.node_investment_id.get(str(decision["parent_id"])) if decision["parent_id"] is not None else None
            parent_lineage = self.node_lineage_id.get(str(decision["parent_id"])) if decision["parent_id"] is not None else None
            if action == "B" and decision.get("investment_id") in self.investments:
                investment_id = decision["investment_id"]
                lineage_id = self.investments[investment_id]["lineage_id"]
            elif action == "I":
                investment_id = parent_investment
                lineage_id = parent_lineage
            fork_project = None
            if investment_id and investment_id in self.investments:
                project = self.investments[investment_id]
                new_hypothesis = _hypothesis(record.get("strategy_hypothesis"))
                is_fork = (not duplicate and all(new_hypothesis.values())
                           and _hash(new_hypothesis) != project["hypothesis_key"]
                           and structure != project["best_structure_fingerprint"])
                if is_fork:
                    if action == "B" and investment_id in self.investment_pool:
                        project["tranche_cost_tokens"] = max(0, project["tranche_cost_tokens"] - (tokens or 0))
                        project["tokens_total"] = max(0, project["tokens_total"] - (tokens or 0))
                        project["no_progress_streak"] += 1
                        if tokens is None:
                            project["tranche_cost_complete"] = False
                            project["tokens_complete"] = False
                    fork_project = self._new_investment(
                        record, project["lineage_id"], kind="verified_strategy_fork",
                        initial_tokens=tokens)
                    investment_id = fork_project["investment_id"]
                    lineage_id = fork_project["lineage_id"]
                    fork_project["forked_from_investment_id"] = project["investment_id"]
                    if tokens is None:
                        fork_project["tokens_complete"] = False
                elif cell not in project["behavior_cells"]:
                    project["behavior_cells"].append(cell)
                if not is_fork and not duplicate and loss < project["best_loss"] - self.config.gain_epsilon:
                    local_gain = project["best_loss"] - loss
                    project["best_loss"], project["best_node_id"] = loss, record["node_id"]
                    project["best_structure_fingerprint"] = structure
                    project["no_progress_streak"] = 0
                    project["total_progress_events"] += 1
                    if investment_id in self.investment_pool:
                        project["tranche_gain"] = max(project["tranche_gain"],
                                                       project["tranche_start_best"] - loss)
                elif not is_fork:
                    project["no_progress_streak"] += 1
            if loss < self.incumbent_loss - self.config.gain_epsilon:
                global_gain = self.incumbent_loss - loss
                self.incumbent_id, self.incumbent_loss = record["node_id"], loss
            self.program_fingerprints.add(fp)
            self.nodes[record["node_id"]] = copy.deepcopy(record)
            self.node_investment_id[record["node_id"]] = investment_id
            self.node_lineage_id[record["node_id"]] = lineage_id
            self.node_behavior_cell_id[record["node_id"]] = cell
            self._update_archive(record, duplicate)
            if action == "E" and not duplicate and self.use_p:
                project = self._open_exploration_project(record, tokens)
                if project is not None:
                    investment_id, lineage_id = project["investment_id"], project["lineage_id"]
            if investment_id and investment_id in self.investments:
                project = self.investments[investment_id]
                if global_gain:
                    project["global_gain_credit"] += global_gain
                    if investment_id in self.investment_pool:
                        project["tranche_global_gain"] = project.get("tranche_global_gain", 0.0) + global_gain
            if action == "I" and investment_id and investment_id in self.investments and fork_project is None:
                project = self.investments[investment_id]
                project["tokens_total"] += tokens or 0
                project["tokens_complete"] = project["tokens_complete"] and tokens is not None
            if action == "B" and investment_id in self.investments and investment_id not in self.investment_pool:
                project = self.investments[investment_id]
                project["tokens_total"] += tokens or 0
                project["tokens_complete"] = project["tokens_complete"] and tokens is not None
            if fork_project is not None:
                self.lineages[fork_project["lineage_id"]]["spent_proposals"] += 1
                lineage_precharged = True
                funded, reason, _ = self._grant(fork_project, "trial")
                fork_project["admission_reason"] = reason
                if not funded:
                    fork_project["status"] = "archived"
                    fork_project["exit_reason"] = reason
        elif outcome and outcome.get("failure_type"):
            cell = self.resolve_behavior_cell(str(outcome.get("behavior_cell_id", "")))
            if cell in self.direction_archive:
                self.direction_archive[cell]["failures"].append({
                    "step": decision["step"], "failure_type": str(outcome["failure_type"]),
                    "program_fingerprint": outcome.get("program_fingerprint"),
                })
        if action == "E":
            self.last_explore_step = decision["step"]
        self.global_reward_ledger.append({
            "step": decision["step"], "decision_id": decision["decision_id"],
            "action": decision["mode"], "investment_id": investment_id,
            "reward": global_gain if valid else (None if terminal_status == "sent_unknown" else 0.0),
            "reward_observed": valid or terminal_status not in {"sent_unknown", "service_failed"},
            "before_loss": before_global, "after_loss": self.incumbent_loss if valid else None,
        })
        if not lineage_precharged:
            self._charge_lineage(decision, investment_id, lineage_id, action)
        if (valid and action == "I" and local_gain > self.config.gain_epsilon
                and investment_id in self.investments):
            self._try_reentry(self.investments[investment_id], local_gain, tokens)
        if valid and action == "I" and tokens:
            self.action_rate_history["I"].append(global_gain / tokens)
            self.matured_action_rates.append({"family": "I", "roi": global_gain / tokens,
                                              "step": decision["step"], "mature": True})
        event = {
            "decision_id": decision["decision_id"], "step": decision["step"],
            "action": decision["mode"], "parent_id": decision["parent_id"],
            "node_id": record["node_id"], "behavior_cell_id": record.get("behavior_cell_id"),
            "investment_id": investment_id, "lineage_id": lineage_id,
            "valid": valid, "terminal_status": terminal_status, "duplicate_program": duplicate,
            "local_project_gain": local_gain, "global_gain_credit": global_gain,
            "incumbent_loss_before": before_global, "incumbent_loss_after": self.incumbent_loss,
            "costs": {**costs, "total_tokens": tokens, "summary_calls": summary_calls,
                      "summary_input_tokens": summary_tokens},
            "delayed_reward_matured": False, "component_effects": decision["component_effects"],
        }
        self.events.append(event)
        self._settle_exhausted_projects()
        self.pending = None
        return copy.deepcopy(event)

    def _update_archive(self, record: dict[str, Any], duplicate: bool) -> None:
        cell = record["behavior_cell_id"]
        card = self.direction_archive.get(cell)
        if card is None:
            card = {"behavior_cell_id": cell, "representative_node_id": record["node_id"],
                    "best_validation_loss": record["validation_loss"], "member_node_ids": [],
                    "strategy_hypothesis": _hypothesis(record.get("strategy_hypothesis")),
                    "program_fingerprint": record["program_fingerprint"],
                    "structure_fingerprint": record["structure_fingerprint"],
                    "failures": [], "first_search_step": self.proposals_used - 1}
            self.direction_archive[cell] = card
        card["member_node_ids"].append(record["node_id"])
        if not duplicate and record["validation_loss"] < card["best_validation_loss"]:
            card["best_validation_loss"] = record["validation_loss"]
            card["representative_node_id"] = record["node_id"]
            card["program_fingerprint"] = record["program_fingerprint"]
            card["structure_fingerprint"] = record["structure_fingerprint"]
            card["strategy_hypothesis"] = _hypothesis(record.get("strategy_hypothesis"))

    def _open_exploration_project(self, record: dict[str, Any], tokens: int | None) -> dict[str, Any] | None:
        loss = record["validation_loss"]
        if loss > self.incumbent_loss + self.config.quality_tolerance:
            self.events.append({"event": "project_admission_decision",
                                "node_id": record["node_id"], "triggered": False,
                                "granted": False, "reason": "quality_gap_too_large"})
            return None
        structure = record["structure_fingerprint"]
        hkey = _hash(_hypothesis(record.get("strategy_hypothesis")))
        for project in self.investments.values():
            if (project["hypothesis_key"] == hkey
                    and project["best_structure_fingerprint"] == structure):
                self.events.append({"event": "project_admission_decision",
                                    "node_id": record["node_id"],
                                    "existing_investment_id": project["investment_id"],
                                    "triggered": False, "granted": False,
                                    "reason": "project_already_exists"})
                return None
        lineage = self._new_lineage()
        project = self._new_investment(record, lineage, kind="exploration", initial_tokens=tokens)
        lineage_record = self.lineages[lineage]
        lineage_record["spent_proposals"] += 1
        funded, reason, _ = self._grant(project, "trial")
        project["admission_reason"] = reason
        self.events.append({"event": "project_admission_decision",
                            "node_id": record["node_id"],
                            "investment_id": project["investment_id"],
                            "lineage_id": lineage, "triggered": True,
                            "granted": funded, "reason": reason})
        if not funded:
            project["status"] = "archived"
            project["exit_reason"] = reason
        self.node_investment_id[record["node_id"]] = project["investment_id"] if funded else None
        self.node_lineage_id[record["node_id"]] = lineage if funded else None
        return project if funded else None

    def _charge_lineage(self, decision: dict[str, Any], investment_id: str | None,
                        lineage_id: str | None, action: str) -> None:
        if not lineage_id or lineage_id not in self.lineages:
            return
        # The exploration root was charged when its new lineage was opened.
        if action == "E" and investment_id and self.investments.get(investment_id, {}).get("root_node_id") == decision.get("node_id"):
            return
        if action == "B" and investment_id in self.investments:
            self.lineages[lineage_id]["spent_proposals"] += 1
            return
        parent_id = str(decision.get("parent_id")) if decision.get("parent_id") is not None else None
        parent_lineage = self.node_lineage_id.get(parent_id) if parent_id else None
        if action == "I" and parent_lineage == lineage_id:
            self.lineages[lineage_id]["spent_proposals"] += 1

    def _settle_exhausted_projects(self) -> None:
        exhausted = [p for p in list(self.investment_pool.values()) if p["tranche_remaining"] == 0]
        for project in exhausted:
            closed = self._close_tranche(project)
            for event in reversed(self.events):
                if ("component_effects" in event and event["investment_id"] == project["investment_id"]
                        and not event.get("delayed_reward_matured")):
                    event["delayed_reward_matured"] = True
                    event["maturity"] = closed["maturity"]
                    break

    def fail_pending(self, *, terminal_status: str = "sent_unknown",
                     costs: dict[str, Any] | None = None) -> dict[str, Any]:
        if terminal_status not in {"sent_unknown", "service_failed"}:
            raise ValueError("fail_pending only records unresolved/failed service outcomes")
        return self.observe(None, costs, terminal_status=terminal_status)

    def forfeit_unredeemed_commitments(self, reason: str) -> list[dict[str, Any]]:
        """Close outstanding promises after a frozen early-stop condition."""
        if self.pending is not None:
            raise ValueError("resolve the pending proposal before forfeiting commitments")
        reason = str(reason).strip()
        if not reason:
            raise ValueError("forfeiture reason must be explicit")
        forfeitures = []
        for project in list(self.investment_pool.values()):
            remaining = int(project["tranche_remaining"])
            if remaining <= 0:
                continue
            commitment = project["commitments"][project["active_commitment_index"]]
            lineage = self.lineages[project["lineage_id"]]
            if lineage["reserved_proposals"] < remaining:
                raise RuntimeError("lineage reservation is smaller than the unredeemed commitment")
            commitment["forfeited_slots"] += remaining
            commitment["status"] = ("partially_redeemed_forfeited"
                                     if commitment["redeemed_slots"] else "forfeited")
            lineage["reserved_proposals"] -= remaining
            project["tranche_remaining"] = 0
            project["tranche_cost_complete"] = False
            project["status"] = "archived"
            project["exit_reason"] = "commitment_forfeited"
            project["forfeiture_reason"] = reason
            self.investment_pool.pop(project["investment_id"], None)
            record = {"event": "commitment_forfeiture",
                      "investment_id": project["investment_id"],
                      "lineage_id": project["lineage_id"],
                      "commitment_id": commitment["commitment_id"],
                      "redeemed_slots": commitment["redeemed_slots"],
                      "forfeited_slots": remaining,
                      "reason": reason, "maturity_created": False}
            self.events.append(record)
            forfeitures.append(copy.deepcopy(record))
        return forfeitures

    def summary(self) -> dict[str, Any]:
        proposal_events = [e for e in self.events if "component_effects" in e]
        renewal_events = [e for e in self.events if e.get("event") == "renewal_decision"]
        commitments = [commitment for project in self.investments.values()
                       for commitment in project["commitments"]]
        awarded_slots = sum(commitment["awarded_slots"] for commitment in commitments)
        redeemed_slots = sum(commitment["redeemed_slots"] for commitment in commitments)
        forfeited_slots = sum(commitment["forfeited_slots"] for commitment in commitments)
        unresolved_slots = awarded_slots - redeemed_slots - forfeited_slots
        lineage_reserved_slots = sum(int(lineage["reserved_proposals"])
                                     for lineage in self.lineages.values())
        active_reserved_slots = sum(int(project["tranche_remaining"])
                                    for project in self.investment_pool.values())
        commitment_conservation_holds = (
            unresolved_slots >= 0
            and unresolved_slots == self.reserved_proposals
            and self.reserved_proposals == active_reserved_slots
            and self.reserved_proposals == lineage_reserved_slots
        )
        return {
            "schema": "chapter6-minimal-mechanism-v1", "config": asdict(self.config),
            "component_flags": {"G": self.use_g, "P": self.use_p, "F": self.use_f},
            "proposals_used": self.proposals_used, "proposal_budget": self.config.proposal_budget,
            "reserved_proposals": self.reserved_proposals,
            "commitment_accounting": {"awarded_slots": awarded_slots,
                                       "redeemed_slots": redeemed_slots,
                                       "forfeited_slots": forfeited_slots,
                                       "unresolved_slots": unresolved_slots,
                                       "active_reserved_slots": active_reserved_slots,
                                       "lineage_reserved_slots": lineage_reserved_slots,
                                       "conservation_holds": commitment_conservation_holds},
            "incumbent_id": self.incumbent_id, "incumbent_loss": self.incumbent_loss,
            "behavior_cell_id_by_node": self.node_behavior_cell_id,
            "investment_id_by_node": self.node_investment_id,
            "lineage_id_by_node": self.node_lineage_id,
            "direction_archive": self.direction_archive,
            "active_investment_ids": sorted(self.investment_pool),
            "investments": self.investments, "lineages": self.lineages,
            "events": self.events, "global_reward_ledger": self.global_reward_ledger,
            "project_reward_ledger": self.project_reward_ledger,
            "matured_action_rates": self.matured_action_rates,
            "component_effect_counts": {
                "G": {"triggered": sum(bool(e["component_effects"]["G"].get("triggered")) for e in proposal_events),
                      "request_context_changed": sum(bool(e["component_effects"]["G"].get("request_context_changed")) for e in proposal_events),
                      "outer_action_changed": 0,
                      "outer_action_change_applicable": False},
                "P": {
                    "trial_admission_triggers": sum(
                        bool(e.get("triggered")) for e in self.events
                        if e.get("event") == "project_admission_decision"),
                    "trial_admissions": sum(commitment["kind"] == "trial" for commitment in commitments),
                    "trial_admission_grants": sum(
                        bool(e.get("granted")) for e in self.events
                        if e.get("event") == "project_admission_decision"),
                    "renewal_triggers": sum(bool(e.get("triggered")) for e in renewal_events),
                    "renewal_commitments": sum(commitment["kind"] == "renewal" for commitment in commitments),
                    "one_step_masked_counterfactuals": sum(
                        bool(e["component_effects"]["P"].get("one_step_masked_counterfactual_applies"))
                        for e in proposal_events),
                    "one_step_masked_action_or_parent_changed": sum(
                        bool(e["component_effects"]["P"].get("one_step_masked_outer_action_changed"))
                        or bool(e["component_effects"]["P"].get("one_step_masked_parent_changed"))
                        for e in proposal_events),
                    "one_step_masked_investment_target_changed": sum(
                        bool(e["component_effects"]["P"].get("one_step_masked_investment_target_changed"))
                        for e in proposal_events),
                },
                "F": {"triggered": sum(bool(e["component_effects"]["F"].get("triggered")) for e in proposal_events),
                      "actual_action_changed": sum(bool(e["component_effects"]["F"].get("actual_action_changed")) for e in proposal_events)},
            },
        }

    def snapshot(self) -> dict[str, Any]:
        if self.pending is not None:
            raise ValueError("snapshot only at proposal boundaries")
        return copy.deepcopy({key: value for key, value in self.__dict__.items()})
