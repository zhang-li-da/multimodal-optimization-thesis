"""Bounded branch development controller for the Chapter 6 v1.2 study."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .benchmarks import TAGS
from .discovery import BEHAVIOR_RADIUS, QUALITY_TOLERANCE, SearchState

V12_METHODS = ("niche", "niche_fixed_dev", "relational_branch")
BRANCH_CAPACITY = 3
BRANCH_CHILDREN = 2


def v12_source_fingerprint():
    root=Path(__file__).resolve().parents[3]
    names=(
        "chapter6_demo/__init__.py",
        "experiments/chapter6/demo/programs.py",
        "experiments/chapter6/demo/benchmarks.py",
        "experiments/chapter6/demo/discovery.py",
        "experiments/chapter6/demo/v12_controller.py",
        "experiments/chapter6/demo/providers.py",
        "experiments/chapter6/v12/preregistration.md",
        "experiments/chapter6/v12/run_v12.py",
        "experiments/chapter6/v12/test_v12.py",
    )
    digest=hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root/name).read_bytes().replace(b"\r\n",b"\n"))
    return digest.hexdigest()


class V12SearchState(SearchState):
    """Separate the report archive from a small, expiring parent-development pool."""

    def __init__(self,task,method,seed,quality_protection=None,restart_correction=None):
        if method not in V12_METHODS:
            raise ValueError("Unknown v1.2 controller.")
        super().__init__(task,method,seed,quality_protection,restart_correction)
        self.branch_pool=[]
        self.branch_decisions=[]
        self.branch_serial=0
        self.branch_cursor=0

    def observe(self,node):
        allocation=node.get("allocation",{})
        charged_parent=allocation.get("branch_parent_id")
        if charged_parent is not None:
            branch=next((item for item in self.branch_pool if item["node_id"]==charged_parent),None)
            if branch is not None:
                branch["remaining"]-=1
                branch["attempts"]+=1

        previous=[n for n in self.nodes if n["evaluation"]["valid"]]
        ev=node["evaluation"]
        best_before=self.best
        signature_match=next((n for n in previous
            if n["evaluation"]["behavior"]==ev.get("behavior")
            and n["evaluation"]["per_instance_loss"]==ev.get("per_instance_loss")),None) if ev["valid"] else None
        super().observe(node)
        event=self.events[-1]
        if node.get("source")!="live_llm":
            return

        if not ev["valid"]:
            classification="invalid_program"
        elif signature_match:
            classification="known_rule_reproduction"
        elif best_before is not None and ev["loss"]>best_before+QUALITY_TOLERANCE[self.task]:
            classification="low_quality_novel_behavior" if event["distance_to_previous"] is None or event["distance_to_previous"]>BEHAVIOR_RADIUS else "low_quality_repeat"
        elif event["parent_improved"] and event["competitive_local_development"]:
            classification="competitive_local_improvement"
        elif event["distance_to_previous"] is not None and event["distance_to_previous"]>BEHAVIOR_RADIUS:
            classification="competitive_novel_behavior_without_parent_gain"
        elif event["terminal_collision"]:
            classification="unproductive_repeat"
        else:
            classification="other_valid_candidate"

        admitted=False
        if classification=="competitive_local_improvement":
            parent_depth=0
            parent_id=node.get("parent_id")
            if parent_id is not None:
                parent_branch=next((b for b in self.branch_pool if b["node_id"]==parent_id),None)
                if parent_branch:
                    parent_depth=parent_branch["depth"]
            tag=node.get("allocated_tag") or (node.get("tags") or [TAGS[self.task][0]])[0]
            branch={"node_id":node["id"],"parent_id":parent_id,"tag":tag,
                "loss":ev["loss"],"parent_gain":event["parent_improvement_margin"],
                "remaining":BRANCH_CHILDREN,"attempts":0,"depth":parent_depth+1,
                "created_order":self.branch_serial}
            self.branch_serial+=1
            self.branch_pool.append(branch)
            admitted=True
            if len(self.branch_pool)>BRANCH_CAPACITY:
                removable=[b for b in self.branch_pool if b["remaining"]<=0]
                victim=min(removable or self.branch_pool,
                    key=lambda b:(b["parent_gain"]/(1+b["attempts"]),-b["created_order"]))
                self.branch_pool.remove(victim)

        event.update(branch_classification=classification,branch_admitted=admitted,
            branch_parent_id=charged_parent,branch_parent_development=charged_parent is not None,
            branch_pool_size=len(self.branch_pool),branch_depth=self._depth_for(node.get("id")))
        self.branch_decisions.append({k:event[k] for k in (
            "node_id","branch_classification","branch_admitted","branch_parent_id",
            "branch_parent_development","branch_pool_size","branch_depth")})

    def _depth_for(self,node_id):
        branch=next((item for item in self.branch_pool if item["node_id"]==node_id),None)
        return branch["depth"] if branch else 0

    def _branch_choice(self,step,tag_statistics):
        available=[b for b in self.branch_pool if b["remaining"]>0]
        if not available or step%2==0 or self.method=="niche":
            return None
        if self.method=="niche_fixed_dev":
            ordered=sorted(available,key=lambda b:(b["created_order"],b["node_id"]))
            branch=ordered[self.branch_cursor%len(ordered)]
            self.branch_cursor+=1
            return branch
        def priority(branch):
            family=tag_statistics.get(branch["tag"],{}).get("priority",0.0)
            local=branch["parent_gain"]/(1+branch["attempts"])
            return (family+0.25*local,-branch["created_order"])
        return max(available,key=priority)

    def choose(self,step):
        original=self.method
        self.method="niche" if original in ("niche","niche_fixed_dev") else "relational"
        try:
            selection=super().choose(step)
        finally:
            self.method=original
        base_evidence=selection["evidence"]
        tag_statistics=base_evidence.get("tag_statistics",{})
        branch=self._branch_choice(step,tag_statistics)
        audit={"decision_step":step}
        if branch is not None:
            branch_node=next(n for n in self.nodes if n["id"]==branch["node_id"])
            selection.update(target=branch["tag"],action="refine",parent=branch_node,reference=None)
            audit.update(branch_parent_id=branch["node_id"],
                branch_remaining_before=branch["remaining"],branch_depth=branch["depth"])
        selection["evidence"]={"decision_step":step}
        selection["audit"]=audit
        selection["branch_development_scheduled"]=branch is not None
        return selection

    def summary(self):
        classes={}
        for event in self.events:
            label=event.get("branch_classification")
            if label:
                classes[label]=classes.get(label,0)+1
        developed=[e for e in self.events if e.get("branch_parent_development")]
        return {"branch_pool_capacity":BRANCH_CAPACITY,"branch_children_per_entry":BRANCH_CHILDREN,
            "branch_admissions":sum(e.get("branch_admitted",False) for e in self.events),
            "branch_development_attempts":len(developed),
            "branch_development_successes":sum(e.get("valid",False) for e in developed),
            "branch_development_parent_improvements":sum(e.get("parent_improved",False) for e in developed),
            "branch_development_max_depth":max((e.get("branch_depth",0) for e in self.events),default=0),
            "branch_pool_remaining":sum(max(0,b["remaining"]) for b in self.branch_pool),
            "branch_classifications":classes}
