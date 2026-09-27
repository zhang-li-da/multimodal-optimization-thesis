"""Component-validation controller with separate direction memory and investment.

The implementation deliberately keeps the treatment factors small.  A valid
direction is remembered in ``direction_members``; only a direction with an
unused trial or a qualifying local improvement receives a development slot.
Zero-credit directions are never inserted into the active pool.
"""
from __future__ import annotations

import copy
from typing import Any

from chapter6_demo import benchmarks
from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import BEHAVIOR_RADIUS
from chapter6_demo.v12_2.common import canonical, digest

from ..s3_tsp_r3.controller import SearchState as _S3SearchState


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


class ComponentSearchState(_S3SearchState):
    """Fixed-slot E2 state with independently toggled P-S and P-E factors."""

    SLOT_TYPES = ("incumbent",) * 20 + ("explore",) * 6 + ("branch",) * 6

    def __init__(self, policy: str = "FB", seed: int = 0, *, steps: int = 32,
                 capacity: int = 2, grant: int = 1,
                 maximum_direction_attempts: int = 4,
                 quality_tolerance: float = .035, gain_epsilon: float = 1e-4,
                 protection: bool = False, scheduling_priority: bool | None = None,
                 eviction_protection: bool = False,
                 behavior_radius: float = BEHAVIOR_RADIUS,
                 adaptive_unit_steps: int = 2, **kwargs):
        if steps != 32:
            raise ValueError("E2 uses the frozen 20/6/6 slot table")
        factor_policy = policy if policy in {"P00", "P10", "P01", "P11"} else None
        if factor_policy is not None:
            scheduling_priority = factor_policy in {"P10", "P11"}
            eviction_protection = factor_policy in {"P01", "P11"}
            protection = scheduling_priority
        super().__init__("FB", seed, steps=steps, capacity=capacity, grant=grant,
                         maximum_direction_attempts=maximum_direction_attempts,
                         quality_tolerance=quality_tolerance,
                         gain_epsilon=gain_epsilon,
                         protection=bool(protection),
                         behavior_radius=behavior_radius,
                         adaptive_unit_steps=adaptive_unit_steps)
        self.scheduling_priority = bool(protection if scheduling_priority is None else scheduling_priority)
        self.eviction_protection = bool(eviction_protection)
        self.trial_slots = grant
        self.renewal_slots = grant
        self.factor_events: list[dict[str, Any]] = []

    def _pool_entry(self, direction):
        return self.pool.get(direction)

    def _admit(self, node, direction, reason, gain):
        """Admit only funded directions; behavior changes do not reset grants."""
        lineage = self.direction_lineage[direction]
        ledger = self.ledgers[lineage]
        entry = self.pool.get(direction)
        is_trial = reason == "new_direction_trial"
        is_renewal = reason == "direction_progression"
        if not (is_trial or is_renewal):
            return {"entry_created": False, "grant_awarded": 0,
                    "development_grant_awarded": 0, "reason": "no_investment_evidence",
                    "evicted_direction_id": None}
        if is_renewal and ledger["grant_awarded"] >= self.maximum_direction_attempts:
            return {"entry_created": False, "grant_awarded": 0,
                    "development_grant_awarded": 0, "reason": "lineage_budget_exhausted",
                    "evicted_direction_id": None}
        if entry is None:
            if len(self.pool) >= self.capacity:
                replaceable = list(self.pool.values())
                if self.eviction_protection:
                    replaceable = [x for x in replaceable if x["remaining"] <= 0]
                if not replaceable:
                    return {"entry_created": False, "grant_awarded": 0,
                            "development_grant_awarded": 0, "reason": "pool_full_protected",
                            "evicted_direction_id": None}
                victim = max(replaceable, key=lambda value: (value["loss"], -value["created_order"]))
                evicted_direction = victim["direction_id"]
                self.pool.pop(evicted_direction)
            else:
                evicted_direction = None
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
            evicted_direction = None
            created = False
            current = self.by_id.get(entry["node_id"])
            if current is None or node["evaluation"]["loss"] < current["evaluation"]["loss"]:
                entry["node_id"] = node["id"]
                entry["loss"] = node["evaluation"]["loss"]
                entry["last_gain"] = max(0.0, gain)
            entry["last_admission_reason"] = reason
        # A trial is awarded once. A renewal is awarded only after evidence;
        # the lineage ledger prevents a behavior mutation from resetting it.
        extra = self.trial_slots if is_trial and ledger["grant_awarded"] == 0 else self.renewal_slots if is_renewal else 0
        extra = min(extra, self.maximum_direction_attempts - ledger["grant_awarded"])
        if extra <= 0:
            return {"entry_created": created, "grant_awarded": 0,
                    "development_grant_awarded": 0, "reason": "no_remaining_trial_or_renewal",
                    "evicted_direction_id": evicted_direction}
        ledger["grant_awarded"] += extra
        entry["remaining"] += extra
        if self.scheduling_priority:
            entry["protection_remaining"] += extra
        return {"entry_created": created, "grant_awarded": extra if self.scheduling_priority else 0,
                "development_grant_awarded": extra, "reason": reason,
                "evicted_direction_id": evicted_direction}

    def _available_investments(self):
        return sorted((entry for entry in self.pool.values() if entry["remaining"] > 0),
                      key=lambda e: (e["created_order"], e["direction_id"]))

    def _available_priority(self):
        if not self.scheduling_priority:
            return []
        return sorted((e for e in self.pool.values() if e["protection_remaining"] > 0),
                      key=lambda e: (e["created_order"], e["direction_id"]))

    def choose(self, step: int):
        if self.pending is not None:
            raise ValueError("Observe the previous proposal before choosing again")
        if step != len(self.decisions) or step >= self.steps:
            raise ValueError("E2 step does not follow decision history")
        slot_type = self.SLOT_TYPES[step]
        branch = None
        if slot_type == "incumbent":
            action, intended, parent = "develop", "incumbent", self.best
        elif slot_type == "explore":
            action, intended, parent = "explore", "explore", None
        else:
            action, intended = "develop", "branch"
            choices = self._available_priority() or self._available_investments()
            branch = choices[0] if choices else None
            parent = self.by_id[branch["node_id"]] if branch else self.best
        target = TAGS["tsp"][step % len(TAGS["tsp"])]
        allocation = {
            "slot_type": slot_type,
            "branch_parent_id": branch["node_id"] if branch else None,
            "direction_id": branch["direction_id"] if branch else None,
            "lineage_id": branch["lineage_id"] if branch else None,
            "protected": bool(branch and self.scheduling_priority and branch in self._available_priority()),
            "protection_remaining_before": branch["protection_remaining"] if branch else None,
            "created_order": branch["created_order"] if branch else None,
        }
        available = self._available_investments()
        decision = {
            "step": step, "target": target, "policy": "E2",
            "action": action, "intended_action": intended,
            "parent": brief(parent), "reference": self._reference(parent),
            "allocation": allocation, "adaptive_unit_id": None,
            "evidence": {
                "slot_type": slot_type,
                "scheduling_priority": self.scheduling_priority,
                "eviction_protection": self.eviction_protection,
                "available_direction_ids": [e["direction_id"] for e in available],
                "priority_direction_ids": [e["direction_id"] for e in self._available_priority()],
                "multiple_investment_choices": len(available) >= 2,
                "multi_branch_protection_slot": len(self._available_priority()) >= 2,
                "pool_size": len(self.pool),
            },
        }
        self.pending = copy.deepcopy(decision)
        self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def observe(self, node, costs=None):
        event = super().observe(node, costs)
        event.update({
            "scheduling_priority": self.scheduling_priority,
            "eviction_protection": self.eviction_protection,
            "slot_type": event.get("action") if event.get("action") in ("explore", "develop") else None,
            "trial_or_renewal": event.get("admission_reason") in ("new_direction_trial", "direction_progression"),
            "zero_credit_pool_entry": False,
        })
        self.factor_events.append({
            "step": event["node_id"],
            "scheduling_priority": self.scheduling_priority,
            "eviction_protection": self.eviction_protection,
            "branch_development": event.get("branch_development", False),
            "protected_development": event.get("protected_development", False),
            "pool_evicted_direction_id": event.get("pool_evicted_direction_id"),
        })
        return event

    def summary(self):
        value = super().summary()
        value.update({
            "controller": "component_e2",
            "scheduling_priority": self.scheduling_priority,
            "eviction_protection": self.eviction_protection,
            "trial_slots": self.trial_slots,
            "renewal_slots": self.renewal_slots,
            "zero_credit_pool_entries": sum(e.get("zero_credit_pool_entry", False) for e in self.events),
            "factor_events": self.factor_events,
        })
        return value


def restore_state(checkpoint):
    config = checkpoint["config"]
    state = ComponentSearchState(
        config.get("policy", "FB"), config["search_seed"], steps=config["steps"],
        capacity=config["capacity"], grant=config["grant"],
        maximum_direction_attempts=config["maximum_direction_attempts"],
        quality_tolerance=config["quality_tolerance"], gain_epsilon=config["gain_epsilon"],
        protection=config.get("scheduling_priority", False),
        scheduling_priority=config.get("scheduling_priority", False),
        eviction_protection=config.get("eviction_protection", False),
        behavior_radius=config["behavior_radius"],
        adaptive_unit_steps=config.get("adaptive_unit_steps", 2),
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
