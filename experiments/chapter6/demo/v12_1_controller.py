"""Offline v1.2.1 controller; the frozen r2 runner never imports this module."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .benchmarks import TAGS
from .discovery import BEHAVIOR_RADIUS, QUALITY_TOLERANCE, SearchState
from .v12_controller import BRANCH_CAPACITY, BRANCH_CHILDREN, V12_METHODS

V121_METHODS = V12_METHODS
ORDINARY_SCHEDULER = "shared_niche"
WORKING_MEMORY_POLICY = "disabled"
FIXED_DEVELOPMENT_ORDER = "fifo_until_exhaustion"


def v121_source_fingerprint():
    root = Path(__file__).resolve().parents[3]
    names = ["chapter6_demo/__init__.py"]
    names += [f"experiments/chapter6/demo/{name}.py" for name in (
        "programs", "classification", "benchmarks", "discovery", "providers",
        "v12_controller", "v12_1_controller")]
    names += ["experiments/chapter6/v12/test_v12.py"]
    names += [f"experiments/chapter6/v12_1/{name}" for name in (
        "preregistration.md", "NUMERIC_REPLAY.md", "test_mechanism.py",
        "test_numeric.py", "audit_r2.py", "verify_numeric.py", "report.py")]
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


class V121SearchState(SearchState):
    """Shared ordinary niche policy; only the B selection rule differs.

    The old controller remains unchanged for historical replay. Family scores
    are computed from observed live-candidate events, not from the ordinary
    scheduler's evidence (which contains no family scores). They are a small
    tag-level heuristic, not a conditional transition graph or calibrated
    probability of future success.
    """

    def __init__(self, task, method, seed):
        if method not in V121_METHODS:
            raise ValueError("Unknown v1.2.1 controller.")
        super().__init__(task, method, seed, quality_protection=False,
                         restart_correction=False)
        self.branch_pool = []
        self.branch_serial = 0
        self.success_depths = {}
        self.decision_history = []
        self._pending = None

    def observe(self, node):
        if any(n["id"] == node["id"] for n in self.nodes):
            raise ValueError("Duplicate node cannot consume or renew a grant.")
        allocation = node.get("allocation", {})
        charged_id = allocation.get("branch_parent_id")
        if self._pending is not None and allocation != self._pending:
            raise ValueError("Candidate allocation differs from the chosen slot.")
        branch = next((b for b in self.branch_pool if b["node_id"] == charged_id), None)
        if charged_id is not None and (
                branch is None or branch["remaining"] <= 0 or
                node.get("parent_id") != charged_id or self.method == "niche"):
            raise ValueError("Invalid or exhausted branch allocation.")
        attempt_depth = self.success_depths[charged_id] + 1 if branch else None
        if branch:
            branch["remaining"] -= 1
            branch["attempts"] += 1

        ev = node["evaluation"]
        known = ev["valid"] and any(
            n["evaluation"]["valid"] and
            n["evaluation"]["behavior"] == ev["behavior"] and
            n["evaluation"]["per_instance_loss"] == ev["per_instance_loss"]
            for n in self.nodes)
        best_before = self.best
        super().observe(node)
        # SearchState keeps trajectory diagnostics in each node. A separate W
        # pool is unnecessary here and is never a source of parents/references.
        self.W.clear()
        self._pending = None
        event = self.events[-1]
        if node.get("source") != "live_llm":
            return

        if not ev["valid"]:
            classification = "invalid_program"
        elif known:
            classification = "known_rule_reproduction"
        elif best_before is not None and ev["loss"] > best_before + QUALITY_TOLERANCE[self.task]:
            classification = ("low_quality_novel_behavior" if event["distance_to_previous"] is None
                              or event["distance_to_previous"] > BEHAVIOR_RADIUS else "low_quality_repeat")
        elif event["parent_improved"] and event["competitive_local_development"]:
            classification = "competitive_local_improvement"
        elif event["distance_to_previous"] is not None and event["distance_to_previous"] > BEHAVIOR_RADIUS:
            classification = "competitive_novel_behavior_without_parent_gain"
        elif event["terminal_collision"]:
            classification = "unproductive_repeat"
        else:
            classification = "other_valid_candidate"

        eligible = classification == "competitive_local_improvement" and self.method != "niche"
        candidate_depth = None
        evicted = None
        if eligible:
            candidate_depth = self.success_depths.get(node.get("parent_id"), 0) + 1
            entry = {"node_id": node["id"], "parent_id": node.get("parent_id"),
                     "tag": node.get("allocated_tag") or node["tags"][0],
                     "loss": ev["loss"], "parent_gain": event["parent_improvement_margin"],
                     "remaining": BRANCH_CHILDREN, "attempts": 0,
                     "depth": candidate_depth, "created_order": self.branch_serial}
            self.branch_serial += 1
            self.branch_pool.append(entry)
            if len(self.branch_pool) > BRANCH_CAPACITY:
                expired = [b for b in self.branch_pool if b["remaining"] <= 0]
                victim = min(expired or self.branch_pool, key=lambda b: (
                    b["parent_gain"] / (1 + b["attempts"]), -b["created_order"]))
                evicted = victim["node_id"]
                self.branch_pool.remove(victim)
        admitted = eligible and evicted != node["id"]
        if admitted:
            # Keep successful ancestry after entries expire or are evicted.
            self.success_depths[node["id"]] = candidate_depth
        event.update(
            branch_classification=classification, branch_eligible=eligible,
            branch_admitted=admitted, branch_evicted_id=evicted,
            branch_parent_id=charged_id, branch_parent_development=charged_id is not None,
            branch_attempt_depth=attempt_depth,
            branch_success_depth=candidate_depth if admitted else None,
            branch_pool_size=len(self.branch_pool))

    def family_statistics(self):
        """Beta(1,1) smoothed eligible-improvement fraction, with shared fallback.

        Invalid outputs and known-rule reproductions count as no new eligible
        improvement. Fewer than two observations of a tag uses the pooled
        statistic. Both arms compute and log exactly the same statistics.
        """
        records = [e for e in self.M if e.get("branch_classification")]
        gains = sum(e["branch_classification"] == "competitive_local_improvement" for e in records)
        pooled = (1 + gains) / (2 + len(records))
        result = {}
        for tag in TAGS[self.task]:
            tagged = [e for e in records if e.get("allocated_tag") == tag]
            successes = sum(e["branch_classification"] == "competitive_local_improvement" for e in tagged)
            result[tag] = {"observations": len(tagged), "eligible_improvements": successes,
                           "fallback": len(tagged) < 2,
                           "priority": pooled if len(tagged) < 2 else (1 + successes) / (2 + len(tagged))}
        return result

    def _available(self):
        return sorted((b for b in self.branch_pool if b["remaining"] > 0),
                      key=lambda b: (b["created_order"], b["node_id"]))

    @staticmethod
    def _score(branch, statistics):
        return statistics[branch["tag"]]["priority"] + 0.25 * branch["parent_gain"] / (1 + branch["attempts"])

    def choose(self, step):
        if self._pending is not None:
            raise ValueError("Observe the pending proposal before choosing again.")
        original = self.method
        self.method = "niche"
        try:
            # Even reserved slots take the same RNG draws; only B choice can
            # change the result for identical histories and RNG states.
            selection = SearchState.choose(self, step)
        finally:
            self.method = original
        available = self._available()
        statistics = self.family_statistics()
        audit = {"decision_step": step, "ordinary_scheduler": ORDINARY_SCHEDULER,
                 "working_memory_policy": WORKING_MEMORY_POLICY,
                 "ordinary_decision": {"target": selection["target"], "action": selection["action"],
                    "parent_id": selection["parent"]["id"] if selection["parent"] else None,
                    "reference_id": selection["reference"]["id"] if selection["reference"] else None},
                 "available_branch_ids": [b["node_id"] for b in available],
                 "branch_scores": {str(b["node_id"]): self._score(b, statistics) for b in available},
                 "family_statistics": statistics}
        branch = None
        if available and step % 2 == 1 and self.method != "niche":
            branch = available[0] if self.method == "niche_fixed_dev" else max(
                available, key=lambda b: (self._score(b, statistics), -b["created_order"], -b["node_id"]))
            selection.update(target=branch["tag"], action="refine", reference=None,
                             parent=next(n for n in self.nodes if n["id"] == branch["node_id"]))
            audit.update(branch_parent_id=branch["node_id"],
                         branch_remaining_before=branch["remaining"],
                         branch_success_depth_before=branch["depth"])
        selection["evidence"] = {"decision_step": step}
        selection["audit"] = audit
        selection["branch_development_scheduled"] = branch is not None
        self.decision_history.append(audit)
        self._pending = audit
        return selection

    def summary(self):
        generated = [e for e in self.events if e.get("branch_classification")]
        developed = [e for e in generated if e["branch_parent_development"]]
        return {"branch_pool_capacity": BRANCH_CAPACITY, "branch_children_per_entry": BRANCH_CHILDREN,
                "branch_admissions": sum(e["branch_admitted"] for e in generated),
                "branch_development_attempts": len(developed),
                "valid_branch_children": sum(e["valid"] for e in developed),
                "parent_improving_branch_children": sum(e["parent_improved"] for e in developed),
                "branch_development_successful_extensions": sum(e["branch_admitted"] for e in developed),
                "branch_development_max_attempt_depth": max((e["branch_attempt_depth"] for e in developed), default=0),
                "branch_development_max_success_depth": max(self.success_depths.values(), default=0),
                "multi_branch_slots": sum("branch_parent_id" in d and len(d["available_branch_ids"]) >= 2
                                          for d in self.decision_history),
                "ordinary_scheduler": ORDINARY_SCHEDULER,
                "working_memory_policy": WORKING_MEMORY_POLICY,
                "fixed_development_order": FIXED_DEVELOPMENT_ORDER}
