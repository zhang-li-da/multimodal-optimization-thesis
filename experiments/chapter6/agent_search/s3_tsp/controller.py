"""Prospective direction-aware search controllers for the Chapter 6 TSP study."""
from __future__ import annotations

import copy
import random
from typing import Any

from chapter6_demo import benchmarks
from chapter6_demo.agent_search.directions import describe
from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import BEHAVIOR_RADIUS
from chapter6_demo.v12_2.common import canonical, digest


POLICIES = ("SP", "WR", "FB", "TS", "AD")


def plain(value):
    import json
    return json.loads(canonical(value))


def brief(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if node is None:
        return None
    ev = node["evaluation"]
    return {
        "id": node["id"], "intent": node.get("intent", "")[:800],
        "tags": node.get("tags", []), "code": node.get("code", ""),
        "loss": ev.get("loss"), "valid": ev.get("valid", False),
        "family_loss": ev.get("family_loss", {}),
        "failure_type": ev.get("failure_type"),
        "trajectory_values": ev.get("trajectory_values", [])[:2],
    }


class SearchState:
    """Reproducible outer-search state; task evaluation and model calls stay external."""

    def __init__(self, policy: str, seed: int, *, steps: int = 32,
                 capacity: int = 6, grant: int = 2,
                 maximum_direction_attempts: int = 8,
                 quality_tolerance: float = .035, gain_epsilon: float = 1e-4,
                 protection: bool = False, behavior_radius: float = BEHAVIOR_RADIUS,
                 adaptive_unit_steps: int = 2):
        if policy not in POLICIES:
            raise ValueError(f"Unknown S3 policy: {policy}")
        if min(steps, capacity, grant, maximum_direction_attempts, adaptive_unit_steps) <= 0:
            raise ValueError("S3 budgets and capacities must be positive")
        if grant > maximum_direction_attempts:
            raise ValueError("A branch grant cannot exceed its cumulative lineage cap")
        self.policy = policy
        self.seed = seed
        self.rng = random.Random(seed)
        self.steps = steps
        self.capacity = capacity
        self.grant = grant
        self.maximum_direction_attempts = maximum_direction_attempts
        self.quality_tolerance = quality_tolerance
        self.gain_epsilon = gain_epsilon
        self.protection = protection and policy in ("FB", "TS", "AD")
        self.behavior_radius = behavior_radius
        self.adaptive_unit_steps = adaptive_unit_steps
        self.nodes: list[dict[str, Any]] = []
        self.by_id: dict[int, dict[str, Any]] = {}
        self.best_id: int | None = None
        self.init_best: float | None = None
        self.direction_members: dict[str, list[int]] = {}
        self.direction_lineage: dict[str, str] = {}
        self.node_direction: dict[int, str] = {}
        self.pool: dict[str, dict[str, Any]] = {}
        self.ledgers: dict[str, dict[str, int]] = {}
        self.events: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.adaptive_units: list[dict[str, Any]] = []
        self.adaptive_unit: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self.serial = 0
        self.cumulative_tokens = 0
        self.cumulative_tokens_complete = True

    @property
    def best(self):
        return self.by_id[self.best_id] if self.best_id is not None else None

    def initialize(self, seeds: list[dict[str, Any]]) -> None:
        if self.nodes:
            raise ValueError("S3 state can only be initialized once")
        if not seeds or not all(n["evaluation"].get("valid") for n in seeds):
            raise ValueError("All shared seeds must be valid")
        if len({n["id"] for n in seeds}) != len(seeds):
            raise ValueError("Duplicate seed id")
        for source in seeds:
            node = copy.deepcopy(source)
            self.nodes.append(node)
            self.by_id[node["id"]] = node
        self.best_id = min(self.by_id, key=lambda i: (self.by_id[i]["evaluation"]["loss"], i))
        self.init_best = self.best["evaluation"]["loss"]
        for node in seeds:
            self._register_direction(node, parent_id=None)

    def _behavior(self, node):
        value = node.get("evaluation", {}).get("behavior")
        return value if isinstance(value, list) and value else None

    @staticmethod
    def _observational_key(node):
        ev = node["evaluation"]
        return digest({"behavior": ev.get("behavior"),
                       "per_instance_loss": ev.get("per_instance_loss")})

    def _known(self, node):
        if not node["evaluation"].get("valid"):
            return False
        key = self._observational_key(node)
        return any(self._observational_key(old) == key for old in self.nodes
                   if old["id"] != node["id"] and old["evaluation"].get("valid"))

    def _competitive(self, node):
        return bool(node["evaluation"].get("valid") and self.init_best is not None
                    and node["evaluation"]["loss"] <= self.init_best + self.quality_tolerance)

    def _parent_gain(self, node):
        parent = self.by_id.get(node.get("parent_id"))
        if not parent or not parent["evaluation"].get("valid") or not node["evaluation"].get("valid"):
            return None
        return parent["evaluation"]["loss"] - node["evaluation"]["loss"]

    def _direction_match(self, node):
        behavior = self._behavior(node)
        if behavior is None:
            return None, None
        matches = []
        for direction, members in self.direction_members.items():
            for member_id in members:
                other = self._behavior(self.by_id[member_id])
                if other is None:
                    continue
                distance = benchmarks.behavior_distance(behavior, other)
                if distance <= self.behavior_radius:
                    matches.append((distance, direction, member_id))
        if not matches:
            return None, None
        distance, direction, _ = min(matches, key=lambda row: (row[0], row[1]))
        return direction, distance

    def _register_direction(self, node, parent_id):
        direction, distance = self._direction_match(node)
        is_new = direction is None
        if is_new:
            direction = f"D{self.serial:04d}"
            parent_direction = self.node_direction.get(parent_id)
            lineage = (self.direction_lineage[parent_direction] if parent_direction is not None
                       else f"L{self.serial:04d}")
            self.serial += 1
            self.direction_members[direction] = []
            self.direction_lineage[direction] = lineage
        self.direction_members[direction].append(node["id"])
        self.node_direction[node["id"]] = direction
        self.ledgers.setdefault(self.direction_lineage[direction], {
            "grant_awarded": 0, "proposal_slots_scheduled": 0,
            "valid_program_evaluations": 0, "local_improvements": 0,
            "global_improvements": 0, "branch_admissions": 0,
        })
        return direction, is_new, distance

    def _direction_representative(self, direction):
        members = self.direction_members.get(direction, [])
        if not members:
            return None
        return min(members, key=lambda i: (self.by_id[i]["evaluation"]["loss"], i))

    def _admit(self, node, direction, reason, gain):
        lineage = self.direction_lineage[direction]
        ledger = self.ledgers[lineage]
        entry = self.pool.get(direction)
        evicted_direction = None
        if entry is None:
            if len(self.pool) >= self.capacity:
                replaceable = [value for value in self.pool.values()
                               if value["protection_remaining"] == 0]
                if not replaceable:
                    return {"entry_created": False, "grant_awarded": 0,
                            "reason": "pool_full", "evicted_direction_id": None}
                victim = max(replaceable, key=lambda value: (value["loss"], -value["created_order"]))
                evicted_direction = victim["direction_id"]
                self.pool.pop(evicted_direction)
            entry = {
                "direction_id": direction, "lineage_id": lineage,
                "node_id": node["id"], "remaining": 0,
                "protection_remaining": 0, "attempts": 0,
                "loss": node["evaluation"]["loss"],
                "created_order": self.serial, "last_gain": max(0.0, gain),
                "last_admission_reason": reason,
            }
            self.serial += 1
            self.pool[direction] = entry
            ledger["branch_admissions"] += 1
            created = True
        else:
            created = False
            current = self.by_id.get(entry["node_id"])
            if current is None or node["evaluation"]["loss"] < current["evaluation"]["loss"]:
                entry["node_id"] = node["id"]
                entry["loss"] = node["evaluation"]["loss"]
                entry["last_gain"] = max(0.0, gain)
            entry["last_admission_reason"] = reason
        extra = 0
        if self.protection:
            extra = min(self.grant, self.maximum_direction_attempts - ledger["grant_awarded"])
            if extra > 0:
                ledger["grant_awarded"] += extra
                entry["remaining"] += extra
                entry["protection_remaining"] += extra
        return {"entry_created": created, "grant_awarded": extra,
                "reason": reason if extra or created else "lineage_budget_exhausted",
                "evicted_direction_id": evicted_direction}

    def _available_protection(self):
        if not self.protection:
            return []
        result = []
        for entry in self.pool.values():
            ledger = self.ledgers[entry["lineage_id"]]
            if entry["protection_remaining"] > 0 and ledger["proposal_slots_scheduled"] < self.maximum_direction_attempts:
                result.append(entry)
        return sorted(result, key=lambda e: (e["created_order"], e["direction_id"]))

    def _reference(self, parent):
        if parent is None:
            return None
        others = [n for n in self.nodes if n["id"] != parent["id"] and n["evaluation"].get("valid")]
        if not others:
            return None
        return max(others, key=lambda n: (abs(n["evaluation"]["loss"] - parent["evaluation"]["loss"]), -n["id"]))

    def _ad_reward_means(self):
        means = {}
        for action in ("explore", "develop"):
            values = [u["reward_per_1000_tokens"] for u in self.adaptive_units
                      if u["action"] == action and isinstance(u["reward_per_1000_tokens"], (int, float))]
            means[action] = sum(values) / len(values) if values else None
        return means

    def _ad_probability(self):
        means = self._ad_reward_means()
        explore, develop = means["explore"], means["develop"]
        if explore is None and develop is None:
            return .5, means
        if explore is None:
            return .5, means
        if develop is None:
            return .5, means
        scale = .001 + abs(explore) + abs(develop)
        normalized = (develop - explore) / scale
        probability = .5 + .25 * max(-1.0, min(1.0, normalized))
        return probability, means

    def _begin_ad_unit(self, step):
        observed = {unit["action"] for unit in self.adaptive_units}
        if self.adaptive_unit is not None:
            return self.adaptive_unit
        if not observed:
            draw = self.rng.random()
            action = "develop" if draw < .5 else "explore"
        elif "explore" not in observed:
            action = "explore"
            draw = None
        elif "develop" not in observed:
            action = "develop"
            draw = None
        else:
            probability, _ = self._ad_probability()
            draw = self.rng.random()
            action = "develop" if draw < probability else "explore"
        unit_id = len(self.adaptive_units)
        self.adaptive_unit = {
            "unit_id": unit_id, "action": action, "steps_used": 0,
            "start_step": step, "best_before": self.best["evaluation"]["loss"],
            "tokens_before": self.cumulative_tokens,
            "ordinary_tokens": 0,
            "ordinary_usage_complete": True,
            "ordinary_global_improvement": 0.0,
            "selection_draw": draw,
        }
        return self.adaptive_unit

    def _p_develop(self, step):
        if self.policy == "SP":
            return 1.0, None
        if self.policy == "WR":
            return 0.0, None
        if self.policy == "FB":
            return .5, None
        fraction = step / max(1, self.steps - 1)
        if self.policy == "TS":
            return .15 + .70 * fraction, None
        return self._ad_probability()

    def choose(self, step: int):
        if self.pending is not None:
            raise ValueError("Observe the previous proposal before choosing again")
        if step != len(self.decisions) or step >= self.steps:
            raise ValueError("S3 step does not follow the decision history")
        p_develop, rates = self._p_develop(step)
        protected = self._available_protection()
        forced_branch = protected[0] if protected and self.policy in ("FB", "TS", "AD") else None
        draw = None
        adaptive_unit_id = None
        if forced_branch is not None:
            intended = "develop"
            action = "develop"
            parent = self.by_id[forced_branch["node_id"]]
            branch = forced_branch
        elif self.policy == "SP":
            intended = action = "develop"
            parent = self.best
            branch = None
        elif self.policy == "WR":
            intended = action = "explore"
            parent = None
            branch = None
        elif self.policy == "AD":
            unit = self._begin_ad_unit(step)
            intended = action = unit["action"]
            adaptive_unit_id = unit["unit_id"]
            parent = self.best if action == "develop" else None
            branch = None
        else:
            draw = self.rng.random()
            intended = "develop" if draw < p_develop else "explore"
            action = intended
            parent = self.best if action == "develop" else None
            branch = None
        reference = self._reference(parent)
        target = TAGS["tsp"][step % len(TAGS["tsp"])]
        allocation = {
            "branch_parent_id": branch["node_id"] if branch else None,
            "direction_id": branch["direction_id"] if branch else None,
            "lineage_id": branch["lineage_id"] if branch else None,
            "protected": branch is not None,
            "protection_remaining_before": branch["protection_remaining"] if branch else None,
            "created_order": branch["created_order"] if branch else None,
        }
        available = self._available_protection()
        decision = {
            "step": step, "target": target, "policy": self.policy,
            "action": action, "intended_action": intended,
            "parent": brief(parent), "reference": brief(reference),
            "allocation": allocation, "adaptive_unit_id": adaptive_unit_id,
            "evidence": {
                "p_develop": p_develop, "rng_draw": draw,
                "adaptive_reward_means_per_1000_tokens": rates,
                "available_protected_direction_ids": [e["direction_id"] for e in available],
                "multi_branch_protection_slot": len(available) >= 2,
                "protected_slot_forced": branch is not None,
                "pool_size": len(self.pool),
                "budget_fraction": step / max(1, self.steps - 1),
            },
        }
        self.pending = copy.deepcopy(decision)
        self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def observe(self, node, costs=None):
        if self.pending is None:
            raise ValueError("No pending S3 decision")
        pending = self.pending
        expected_parent = pending["parent"]["id"] if pending["parent"] else None
        if node.get("parent_id") != expected_parent:
            raise ValueError("Candidate parent differs from the immutable decision")
        if plain(node.get("allocation", {})) != plain(pending["allocation"]):
            raise ValueError("Candidate allocation differs from the immutable decision")
        costs = copy.deepcopy(costs or {})
        tokens_added = costs.get("tokens_added")
        if type(tokens_added) is int and tokens_added >= 0:
            self.cumulative_tokens += tokens_added
        else:
            self.cumulative_tokens_complete = False
        branch_id = pending["allocation"]["direction_id"]
        branch = self.pool.get(branch_id) if branch_id is not None else None
        if branch_id is not None and (branch is None or branch["protection_remaining"] <= 0):
            raise ValueError("Selected protected branch has no remaining grant")
        global_before = self.best["evaluation"]["loss"] if self.best else None
        protected_parent_loss_before = branch["loss"] if branch is not None else None
        protected_parent_was_behind_global = bool(
            branch is not None and global_before is not None
            and protected_parent_loss_before > global_before + self.gain_epsilon
        )
        if branch is not None:
            branch["remaining"] -= 1
            branch["protection_remaining"] -= 1
            branch["attempts"] += 1
            ledger = self.ledgers[branch["lineage_id"]]
            ledger["proposal_slots_scheduled"] += 1
        ev = node["evaluation"]
        valid = bool(ev.get("valid"))
        known = self._known(node) if valid else False
        parent_gain = self._parent_gain(node)
        local_improvement = bool(valid and parent_gain is not None and parent_gain > self.gain_epsilon)
        global_improvement = bool(valid and (global_before is None or ev["loss"] < global_before - self.gain_epsilon))
        competitive = self._competitive(node)
        old_direction, distance = self._direction_match(node) if valid else (None, None)
        new_direction = bool(valid and old_direction is None)
        direction = "unknown"
        direction_gain = None
        if valid:
            prior_rep = self._direction_representative(old_direction) if old_direction else None
            prior_loss = self.by_id[prior_rep]["evaluation"]["loss"] if prior_rep is not None else None
            direction, _, _ = self._register_direction(node, node.get("parent_id"))
            self.node_direction[node["id"]] = direction
            direction_gain = prior_loss - ev["loss"] if prior_loss is not None else None
        self.nodes.append(copy.deepcopy(node))
        self.by_id[node["id"]] = copy.deepcopy(node)
        self.ledgers.setdefault(self.direction_lineage.get(direction, ""), {
            "grant_awarded": 0, "proposal_slots_scheduled": 0,
            "valid_program_evaluations": 0, "local_improvements": 0,
            "global_improvements": 0, "branch_admissions": 0,
        }) if valid else None
        if valid and ev["loss"] < self.best["evaluation"]["loss"]:
            self.best_id = node["id"]
        if valid:
            lineage = self.direction_lineage[direction]
            ledger = self.ledgers[lineage]
            ledger["valid_program_evaluations"] += 1
            ledger["local_improvements"] += int(local_improvement)
            ledger["global_improvements"] += int(global_improvement)
        admission = {"entry_created": False, "grant_awarded": 0,
                     "reason": "not_eligible", "evicted_direction_id": None}
        direction_improvement = direction_gain is not None and direction_gain > self.gain_epsilon
        novel_qualified = bool(valid and competitive and not known and
                               (new_direction or direction_improvement) and
                               self.policy in ("FB", "TS", "AD"))
        if novel_qualified:
            reason = "new_direction_trial" if new_direction else "direction_progression"
            admission = self._admit(node, direction, reason,
                                    direction_gain if direction_gain is not None else 0.0)
        classification = (
            "invalid_program" if not valid else
            "known_rule_reproduction" if known else
            "competitive_new_direction" if competitive and new_direction else
            "competitive_direction_progression" if competitive and direction_improvement else
            "low_quality_new_direction" if new_direction and not competitive else
            "within_direction_nonimprovement" if direction != "unknown" else
            "other_valid_candidate"
        )
        event = {
            "node_id": node["id"], "parent_id": node.get("parent_id"),
            "direction_id": direction, "lineage_id": self.direction_lineage.get(direction),
            "action": pending["action"], "intended_action": pending["intended_action"],
            "valid": valid, "known_reproduction": known,
            "competitive": competitive, "parent_gain": parent_gain,
            "direction_gain": direction_gain, "local_improvement": local_improvement,
            "direction_improvement": bool(direction_improvement),
            "global_improvement": global_improvement, "new_direction": new_direction,
            "direction_distance": distance, "classification": classification,
            "branch_parent_id": pending["allocation"]["branch_parent_id"],
            "scheduled_direction_id": pending["allocation"]["direction_id"],
            "scheduled_lineage_id": pending["allocation"]["lineage_id"],
            "protected_development": bool(pending["allocation"]["protected"]),
            "protected_parent_loss_before": protected_parent_loss_before,
            "protected_parent_was_behind_global": protected_parent_was_behind_global,
            "global_best_loss_before": global_before,
            "global_best_loss_after": self.best["evaluation"]["loss"] if self.best else None,
            "branch_entry_created": admission["entry_created"],
            "pool_evicted_direction_id": admission["evicted_direction_id"],
            "protection_grant_awarded": admission["grant_awarded"],
            "admission_reason": admission["reason"],
            "branch_remaining_after": branch["protection_remaining"] if branch else None,
            "multi_branch_protection_slot": pending["evidence"]["multi_branch_protection_slot"],
            "pool_size_after": len(self.pool), "loss": ev.get("loss"),
            "failure_type": ev.get("failure_type"), "costs": costs,
            "adaptive_unit_id": pending["adaptive_unit_id"],
            "structural_descriptor": describe(node.get("code", "")) if valid else None,
        }
        self.events.append(event)
        self._finish_adaptive_unit(pending, event)
        self.pending = None
        return event

    def _finish_adaptive_unit(self, decision, event):
        if self.policy != "AD" or decision.get("adaptive_unit_id") is None:
            return
        if self.adaptive_unit is None or self.adaptive_unit["unit_id"] != decision["adaptive_unit_id"]:
            raise ValueError("Adaptive unit history is inconsistent")
        # Protected slots are forced branch-development opportunities.  They
        # remain part of the global budget, but cannot change the length or
        # reward of an adaptive ordinary-action unit.
        if decision["allocation"].get("protected"):
            return
        self.adaptive_unit["steps_used"] += 1
        tokens_added = event.get("costs", {}).get("tokens_added")
        if type(tokens_added) is int and tokens_added >= 0:
            self.adaptive_unit["ordinary_tokens"] += tokens_added
        else:
            self.adaptive_unit["ordinary_usage_complete"] = False
        before = event.get("global_best_loss_before")
        after = event.get("global_best_loss_after")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            self.adaptive_unit["ordinary_global_improvement"] += max(0.0, before - after)
        if self.adaptive_unit["steps_used"] < self.adaptive_unit_steps:
            return
        unit = self.adaptive_unit
        tokens = unit["ordinary_tokens"] if unit["ordinary_usage_complete"] else None
        improvement = unit["ordinary_global_improvement"]
        reward = improvement / (tokens / 1000.0) if tokens else None
        completed = {
            "unit_id": unit["unit_id"], "action": unit["action"],
            "start_step": unit["start_step"], "end_step": decision["step"],
            "proposal_slots": unit["steps_used"], "global_best_improvement": improvement,
            "tokens": tokens, "tokens_before_global": unit["tokens_before"],
            "ordinary_usage_complete": unit["ordinary_usage_complete"],
            "reward_per_1000_tokens": reward,
            "selection_draw": unit["selection_draw"],
        }
        self.adaptive_units.append(completed)
        event["adaptive_unit_completed"] = copy.deepcopy(completed)
        self.adaptive_unit = None

    def protected_ancestor(self, node_id):
        seen = set()
        current = node_id
        while current is not None and current not in seen:
            seen.add(current)
            event = next((e for e in self.events if e["node_id"] == current), None)
            if event and event["protected_development"]:
                return True
            current = self.by_id.get(current, {}).get("parent_id")
        return False

    def snapshot(self):
        return plain({
            "policy": self.policy, "seed": self.seed, "steps": self.steps,
            "capacity": self.capacity, "grant": self.grant,
            "maximum_direction_attempts": self.maximum_direction_attempts,
            "quality_tolerance": self.quality_tolerance,
            "gain_epsilon": self.gain_epsilon, "protection": self.protection,
            "nodes": self.nodes, "best_id": self.best_id, "init_best": self.init_best,
            "direction_members": self.direction_members,
            "direction_lineage": self.direction_lineage,
            "node_direction": self.node_direction, "pool": self.pool,
            "ledgers": self.ledgers, "events": self.events,
            "decisions": self.decisions, "adaptive_units": self.adaptive_units,
            "adaptive_unit": self.adaptive_unit, "pending": self.pending,
            "serial": self.serial, "cumulative_tokens": self.cumulative_tokens,
            "cumulative_tokens_complete": self.cumulative_tokens_complete,
            "rng": self.rng.getstate(),
        })

    def summary(self):
        protected = [e for e in self.events if e["protected_development"]]
        valid = [e for e in self.events if e["valid"]]
        return {
            "policy": self.policy, "protection": self.protection,
            "initial_best_validation_loss": self.init_best,
            "best_validation_loss": self.best["evaluation"]["loss"] if self.best else None,
            "completed_proposals": len(self.events),
            "valid_generated": len(valid),
            "model_request_attempts": sum(e["costs"].get("requests_added", 0) for e in self.events),
            "complete_model_responses": sum(e["costs"].get("complete_responses", 0) for e in self.events),
            "truncated_model_responses": sum(e["costs"].get("truncations", 0) for e in self.events),
            "new_direction_candidates": sum(e["new_direction"] for e in self.events),
            "competitive_new_direction_candidates": sum(e["competitive"] and e["new_direction"] for e in self.events),
            "branch_entries_created": sum(e["branch_entry_created"] for e in self.events),
            "pool_evictions": sum(e["pool_evicted_direction_id"] is not None for e in self.events),
            "protection_grants_awarded": sum(e["protection_grant_awarded"] for e in self.events),
            "protected_slots_scheduled": len(protected),
            "protected_slots_with_complete_output": sum(e["costs"].get("complete_responses", 0) >= 2 for e in protected),
            "protected_slots_with_valid_program": sum(e["valid"] for e in protected),
            "protected_local_improvements": sum(e["protected_development"] and e["local_improvement"] for e in self.events),
            "protected_direction_improvements": sum(e["protected_development"] and e["direction_improvement"] for e in self.events),
            "protected_global_improvements": sum(e["protected_development"] and e["global_improvement"] for e in self.events),
            "protected_slots_on_lagging_parent": sum(e["protected_development"] and e["protected_parent_was_behind_global"] for e in self.events),
            "multi_branch_protection_slots": sum(e["multi_branch_protection_slot"] for e in self.events),
            "protected_best_ancestor": self.protected_ancestor(self.best_id) if self.best_id is not None else False,
            "adaptive_units": self.adaptive_units,
            "adaptive_partial_unit": copy.deepcopy(self.adaptive_unit),
            "directions": len(self.direction_members),
            "pool_size": len(self.pool),
            "lineage_ledgers": self.ledgers,
            "known_reproductions": sum(e["known_reproduction"] for e in self.events),
        }


def restore_state(checkpoint):
    """Replay immutable choices and observations to recover a durable run."""
    config = checkpoint["config"]
    state = SearchState(
        config["policy"], config["search_seed"], steps=config["steps"],
        capacity=config["capacity"], grant=config["grant"],
        maximum_direction_attempts=config["maximum_direction_attempts"],
        quality_tolerance=config["quality_tolerance"],
        gain_epsilon=config["gain_epsilon"], protection=config["protection"],
        behavior_radius=config["behavior_radius"],
        adaptive_unit_steps=config["adaptive_unit_steps"],
    )
    state.initialize(checkpoint["seeds"])
    for record in checkpoint["records"]:
        decision = state.choose(record["decision"]["step"])
        if plain(decision) != plain(record["decision"]):
            raise ValueError("Replay decision differs from immutable checkpoint")
        event = state.observe(record["node"], record["costs"])
        if plain(event) != plain(record["event"]):
            raise ValueError("Replay event differs from immutable checkpoint")
    return state
