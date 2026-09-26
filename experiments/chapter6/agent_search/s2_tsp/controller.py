"""Prospective S2 controllers for the TSP protection experiment.

The controller owns only outer search decisions.  It never evaluates code or
calls a model.  Every branch grant is represented by a ledger so that an
entry cannot regain budget by changing its label or by re-entering the pool.
"""
from __future__ import annotations

import copy
import math
import random
from typing import Any

from chapter6_demo.benchmarks import TAGS
from chapter6_demo.discovery import BEHAVIOR_RADIUS
from chapter6_demo.agent_search.directions import describe
from chapter6_demo.v12_2.common import canonical, digest


POLICIES = ("SP", "WR", "FB", "TS", "AD")
PROTECTION_ARMS = ("fb_unprotected", "fb_protected")


def plain(value):
    import json
    return json.loads(canonical(value))


def brief(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if node is None:
        return None
    ev = node["evaluation"]
    return {
        "id": node["id"],
        "intent": node.get("intent", "")[:800],
        "tags": node.get("tags", []),
        "code": node.get("code", ""),
        "loss": ev.get("loss"),
        "valid": ev.get("valid", False),
        "family_loss": ev.get("family_loss", {}),
        "failure_type": ev.get("failure_type"),
        "trajectory_values": ev.get("trajectory_values", [])[:2],
    }


class S2State:
    """One reproducible outer search state for one policy/data/seed job."""

    def __init__(self, controller: str, seed: int, *, steps: int = 8,
                 capacity: int = 4, grant: int = 2,
                 maximum_attempts: int = 8, quality_tolerance: float = .035,
                 gain_epsilon: float = 1e-4, protection: bool = False):
        if controller not in PROTECTION_ARMS and controller not in POLICIES:
            raise ValueError(f"Unknown S2 controller: {controller}")
        self.controller = controller
        self.seed = seed
        self.rng = random.Random(seed)
        self.steps = steps
        self.capacity = capacity
        self.grant = grant
        self.maximum_attempts = maximum_attempts
        self.quality_tolerance = quality_tolerance
        self.gain_epsilon = gain_epsilon
        self.protection = protection
        self.nodes: list[dict[str, Any]] = []
        self.by_id: dict[int, dict[str, Any]] = {}
        self.best_id: int | None = None
        self.init_best: float | None = None
        self.pool: list[dict[str, Any]] = []
        self.ledgers: dict[str, dict[str, int]] = {}
        self.events: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.pending: dict[str, Any] | None = None
        self.serial = 0
        self.ancestry: dict[int, int | None] = {}

    @property
    def best(self):
        return self.by_id[self.best_id] if self.best_id is not None else None

    def initialize(self, seeds: list[dict[str, Any]]) -> None:
        if self.nodes:
            raise ValueError("S2 state can only be initialized once")
        if not seeds or not all(n["evaluation"].get("valid") for n in seeds):
            raise ValueError("All shared seeds must be valid")
        for node in seeds:
            if node["id"] in self.by_id:
                raise ValueError("Duplicate seed id")
            copy_node = copy.deepcopy(node)
            self.nodes.append(copy_node)
            self.by_id[copy_node["id"]] = copy_node
            self.ancestry[copy_node["id"]] = copy_node.get("parent_id")
        self.best_id = min(self.by_id, key=lambda i: (self.by_id[i]["evaluation"]["loss"], i))
        self.init_best = self.by_id[self.best_id]["evaluation"]["loss"]

    def _direction(self, node):
        return describe(node.get("code", "")).get("direction_id", "unknown")

    @staticmethod
    def _behavior_key(node):
        ev = node["evaluation"]
        return digest({"behavior": ev.get("behavior"),
                       "per_instance_loss": ev.get("per_instance_loss")})

    def _known(self, node):
        if not node["evaluation"].get("valid"):
            return False
        key = self._behavior_key(node)
        return any(self._behavior_key(old) == key for old in self.nodes
                   if old["evaluation"].get("valid"))

    def _competitive(self, node):
        return bool(node["evaluation"].get("valid") and self.init_best is not None
                    and node["evaluation"]["loss"] <= self.init_best + self.quality_tolerance)

    def _parent_gain(self, node):
        parent = self.by_id.get(node.get("parent_id"))
        if not parent or not parent["evaluation"].get("valid") or not node["evaluation"].get("valid"):
            return None
        return parent["evaluation"]["loss"] - node["evaluation"]["loss"]

    def _available(self):
        return sorted((b for b in self.pool if b["remaining"] > 0 and
                       self.ledgers[b["direction_id"]]["attempts"] < self.maximum_attempts),
                      key=lambda b: (b["created_order"], b["node_id"]))

    def _rates(self):
        rows = self.events[-8:]
        result = {}
        for action in ("explore", "develop"):
            selected = [r for r in rows if r.get("action") == action]
            successes = sum(bool(r.get("local_improvement")) for r in selected)
            result[action] = {"attempts": len(selected), "successes": successes,
                              "rate": (1 + successes) / (2 + len(selected))}
        return result

    def _ordinary_parent(self):
        valid = [n for n in self.nodes if n["evaluation"].get("valid")]
        return min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"])) if valid else None

    def _reference(self, parent):
        if parent is None:
            return None
        others = [n for n in self.nodes if n["id"] != parent["id"] and n["evaluation"].get("valid")]
        if not others:
            return None
        # A deterministic complementary reference; it is diagnostic and is
        # held constant across policies except for the selected parent.
        return max(others, key=lambda n: (abs(n["evaluation"]["loss"] - parent["evaluation"]["loss"]), -n["id"]))

    def _p_develop(self, step):
        if self.controller in ("fb_unprotected", "fb_protected", "FB"):
            return .5
        if self.controller == "SP":
            return 1.0
        if self.controller == "WR":
            return 0.0
        fraction = step / max(1, self.steps - 1)
        if self.controller == "TS":
            return .2 + .6 * fraction
        rates = self._rates()
        return max(.2, min(.8, .5 + .6 * (rates["develop"]["rate"] - rates["explore"]["rate"])))

    def choose(self, step: int):
        if self.pending is not None:
            raise ValueError("Observe the previous proposal before choosing again")
        if step != len(self.decisions):
            raise ValueError("S2 step does not follow the decision history")
        tags = TAGS["tsp"]
        p_develop = self._p_develop(step)
        draw = self.rng.random()
        intended = "develop" if draw < p_develop else "explore"
        available = self._available()
        multi = len(available) >= 2
        protected_slot = False
        branch = None
        action = intended
        parent = None
        if self.controller == "WR":
            action = "explore"
        elif self.controller == "SP":
            action = "develop"
            parent = self._ordinary_parent()
        elif intended == "develop":
            if self.protection and available:
                # P1 deliberately uses FIFO.  The later scheduling experiment
                # changes only this selector, so protection is identifiable.
                branch = available[0]
                parent = self.by_id[branch["node_id"]]
                protected_slot = True
            else:
                # The unprotected arm still spends the same proposal budget,
                # but has no protected branch parent to continue.
                parent = self._ordinary_parent()
                action = "develop_unprotected"
        if action.startswith("develop") and parent is None and self.controller != "WR":
            parent = self._ordinary_parent()
        target = (branch["tag"] if branch else tags[step % len(tags)])
        reference = self._reference(parent)
        allocation = {
            "branch_parent_id": branch["node_id"] if branch else None,
            "direction_id": branch["direction_id"] if branch else None,
            "protected": protected_slot,
            "remaining_before": branch["remaining"] if branch else None,
            "created_order": branch["created_order"] if branch else None,
        }
        decision = {
            "step": step,
            "target": target,
            "action": action,
            "intended_action": intended,
            "parent": brief(parent),
            "reference": brief(reference),
            "allocation": allocation,
            "evidence": {
                "p_develop": p_develop,
                "rng_draw": draw,
                "available_branch_ids": [b["node_id"] for b in available],
                "multi_branch_slot": multi,
                "protected_slot": protected_slot,
                "rates": self._rates(),
                "budget_fraction": step / max(1, self.steps - 1),
            },
        }
        self.pending = copy.deepcopy(decision)
        self.decisions.append(copy.deepcopy(decision))
        return copy.deepcopy(decision)

    def _admit(self, node, direction, tag, gain):
        ledger = self.ledgers.setdefault(direction, {"attempts": 0, "granted": 0, "admissions": 0})
        if ledger["attempts"] >= self.maximum_attempts or ledger["granted"] >= self.maximum_attempts:
            return False
        if not self.protection:
            ledger["admissions"] += 1
            return False
        active = [b for b in self.pool if b["remaining"] > 0]
        if len(active) >= self.capacity:
            replaceable = [b for b in active if b["initial_remaining"] == 0]
            if not replaceable:
                return False
            victim = min(replaceable, key=lambda b: (b["gain"] / (1 + b["attempts"]), b["created_order"]))
            victim["remaining"] = 0
        extra = min(self.grant, self.maximum_attempts - ledger["granted"])
        if extra <= 0:
            return False
        entry = {
            "node_id": node["id"], "parent_id": node.get("parent_id"),
            "direction_id": direction, "tag": tag, "loss": node["evaluation"]["loss"],
            "gain": max(0.0, gain), "remaining": extra, "initial_remaining": extra,
            "attempts": 0, "created_order": self.serial,
        }
        self.serial += 1
        self.pool.append(entry)
        ledger["granted"] += extra
        ledger["admissions"] += 1
        return True

    def observe(self, node):
        if self.pending is None:
            raise ValueError("No pending S2 decision")
        pending = self.pending
        if node.get("parent_id") != (pending["parent"]["id"] if pending["parent"] else None):
            raise ValueError("Candidate parent differs from the immutable decision")
        allocation = node.get("allocation", {})
        if plain(allocation) != plain(pending["allocation"]):
            raise ValueError("Candidate allocation differs from the immutable decision")
        branch_id = pending["allocation"]["branch_parent_id"]
        branch = next((b for b in self.pool if b["node_id"] == branch_id), None) if branch_id is not None else None
        if branch_id is not None and (branch is None or branch["remaining"] <= 0):
            raise ValueError("Selected branch has no remaining grant")
        if branch is not None:
            branch["remaining"] -= 1
            branch["initial_remaining"] = max(0, branch["initial_remaining"] - 1)
            branch["attempts"] += 1
            self.ledgers[branch["direction_id"]]["attempts"] += 1
        ev = node["evaluation"]
        valid = bool(ev.get("valid"))
        known = self._known(node) if valid else False
        parent_gain = self._parent_gain(node)
        local_improvement = bool(valid and parent_gain is not None and parent_gain > self.gain_epsilon)
        global_before = self.best["evaluation"]["loss"] if self.best else None
        global_improvement = bool(valid and (global_before is None or ev["loss"] < global_before - self.gain_epsilon))
        competitive = self._competitive(node)
        direction = self._direction(node) if valid else "unknown"
        novel_development = bool(valid and competitive and local_improvement and not known and direction != "unknown")
        if valid and (self.best is None or ev["loss"] < self.best["evaluation"]["loss"]):
            self.best_id = node["id"]
        admitted = False
        if novel_development:
            tag = node.get("allocated_tag") or (node.get("tags") or ["unknown"])[0]
            admitted = self._admit(node, direction, tag, parent_gain or 0.0)
        self.nodes.append(copy.deepcopy(node))
        self.by_id[node["id"]] = copy.deepcopy(node)
        self.ancestry[node["id"]] = node.get("parent_id")
        classification = (
            "invalid_program" if not valid else
            "known_rule_reproduction" if known else
            "competitive_local_improvement" if novel_development else
            "low_quality_novel_behavior" if not competitive else
            "unproductive_repeat" if ev.get("behavior") is not None and any(
                self._behavior_key(old) == self._behavior_key(node) for old in self.nodes[:-1]
                if old["evaluation"].get("valid")) else "other_valid_candidate"
        )
        event = {
            "node_id": node["id"], "parent_id": node.get("parent_id"),
            "direction_id": direction, "action": pending["action"],
            "valid": valid, "known_reproduction": known,
            "competitive": competitive, "parent_gain": parent_gain,
            "local_improvement": local_improvement, "global_improvement": global_improvement,
            "classification": classification, "branch_parent_id": branch_id,
            "protected_development": bool(pending["allocation"]["protected"]),
            "branch_admitted": admitted, "branch_remaining_after": branch["remaining"] if branch else None,
            "multi_branch_slot": pending["evidence"]["multi_branch_slot"],
            "pool_size_after": len(self._available()),
            "loss": ev.get("loss"), "failure_type": ev.get("failure_type"),
        }
        self.events.append(event)
        self.pending = None
        return event

    def protected_ancestor(self, node_id):
        seen = set()
        current = node_id
        while current is not None and current not in seen:
            seen.add(current)
            event = next((e for e in self.events if e["node_id"] == current), None)
            if event and event["protected_development"]:
                return True
            current = self.ancestry.get(current)
        return False

    def snapshot(self):
        return plain({
            "controller": self.controller, "seed": self.seed, "steps": self.steps,
            "capacity": self.capacity, "grant": self.grant,
            "maximum_attempts": self.maximum_attempts,
            "quality_tolerance": self.quality_tolerance,
            "gain_epsilon": self.gain_epsilon, "protection": self.protection,
            "nodes": self.nodes, "best_id": self.best_id, "init_best": self.init_best,
            "pool": self.pool, "ledgers": self.ledgers, "events": self.events,
            "decisions": self.decisions, "pending": self.pending, "serial": self.serial,
            "ancestry": self.ancestry, "rng": self.rng.getstate(),
        })

    def summary(self):
        developed = [e for e in self.events if e["protected_development"]]
        novel = [e for e in self.events if e["classification"] == "competitive_local_improvement"]
        best_from_protected = self.protected_ancestor(self.best_id) if self.best_id is not None else False
        return {
            "controller": self.controller, "protection": self.protection,
            "initial_best_validation_loss": self.init_best,
            "best_validation_loss": self.best["evaluation"]["loss"] if self.best else None,
            "valid_generated": sum(e["valid"] for e in self.events),
            "branch_admissions": sum(e["branch_admitted"] for e in self.events),
            "competitive_local_improvements": len(novel),
            "protected_slots_scheduled": sum(bool(d["allocation"]["protected"]) for d in self.decisions),
            "protected_slots_realized": len(developed),
            "multi_branch_slots": sum(e["multi_branch_slot"] for e in self.events),
            "protected_branch_parent_improvements": sum(e["protected_development"] and e["local_improvement"] for e in self.events),
            "protected_best_ancestor": best_from_protected,
            "known_reproductions": sum(e["known_reproduction"] for e in self.events),
            "known_reproduction_rate": sum(e["known_reproduction"] for e in self.events) / max(1, len(self.events)),
            "max_pool_size": max((e["pool_size_after"] for e in self.events), default=0),
        }


def restore_state(checkpoint):
    """Replay immutable decisions and candidates to recover after interruption."""
    cfg = checkpoint["config"]
    state = S2State(cfg["controller"], cfg["search_seed"], steps=cfg["steps"],
                    capacity=cfg["capacity"], grant=cfg["grant"],
                    maximum_attempts=cfg["maximum_direction_attempts"],
                    quality_tolerance=cfg["quality_tolerance"],
                    gain_epsilon=cfg["gain_epsilon"], protection=cfg["protection"])
    state.initialize(checkpoint["seeds"])
    for record in checkpoint["records"]:
        decision = state.choose(record["decision"]["step"])
        if plain(decision) != plain(record["decision"]):
            raise ValueError("Replay decision differs from immutable checkpoint")
        state.observe(record["node"])
        if plain(state.events[-1]) != plain(record["event"]):
            raise ValueError("Replay event differs from immutable checkpoint")
    return state

