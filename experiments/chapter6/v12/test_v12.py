"""Mechanism gates for bounded branch development and prompt invariance."""
import json

from chapter6_demo.benchmarks import V12_TSP_PROFILE, instances, split_fingerprint
from chapter6_demo.discovery import planner_prompt
from chapter6_demo.v12_controller import V12SearchState


def _node(idx,loss,behavior,parent=None,source="live_llm",vector=None):
    vector=vector or [loss,loss]
    return {"id":idx,"name":f"n{idx}","intent":"test rule","tags":["return_aware"],
        "allocated_tag":"return_aware","code":"def priority(f):\n    return -f[\"distance\"]\n",
        "parent_id":parent,"reference_id":None,"action":"refine","source":source,
        "evaluation":{"valid":True,"loss":loss,"per_instance_loss":vector,
            "per_instance_value":[1]*len(vector),"family_loss":{"uniform":loss},
            "behavior":behavior,"trajectory_behavior":behavior,"trajectory_values":[],
            "program_hash":str(idx),"ast_nodes":2,"solutions":[],"failure_type":None,
            "features_called":2,"local_checks":0,"cpu_seconds":0.0,"wall_seconds":0.0}}


def _seeded(method):
    state=V12SearchState("tsp",method,0)
    state.observe(_node(0,.08,[[1,0,0]],source="handwritten_seed",vector=[.08,.08]))
    state.observe(_node(1,.12,[[0,1,0]],source="handwritten_seed",vector=[.12,.12]))
    return state


def test_competitive_parent_improvement_enters_B_and_gets_followup_parent_slot():
    state=_seeded("niche_fixed_dev")
    state.observe(_node(2,.11,[[0,1,0]],parent=1,vector=[.105,.115]))
    event=state.events[-1]
    assert event["branch_classification"]=="competitive_local_improvement"
    assert event["branch_admitted"] is True
    selection=state.choose(1)
    assert selection["branch_development_scheduled"] is True
    assert selection["parent"]["id"]==2
    assert selection["action"]=="refine"
    assert selection["audit"]["branch_remaining_before"]==2
    assert state.events[-1]["branch_depth"]==1
    plan=json.loads(planner_prompt("tsp",selection,1))
    assert "niche_fixed_dev" not in json.dumps(plan)
    assert "relational_branch" not in json.dumps(plan)


def test_known_rule_reproduction_and_low_quality_novelty_do_not_enter_B():
    state=_seeded("relational_branch")
    state.observe(_node(2,.12,[[0,1,0]],parent=1,vector=[.12,.12]))
    assert state.events[-1]["branch_classification"]=="known_rule_reproduction"
    assert not state.events[-1]["branch_admitted"]
    state.observe(_node(3,.30,[[0,0,1]],parent=1,vector=[.30,.30]))
    assert state.events[-1]["branch_classification"]=="low_quality_novel_behavior"
    assert not state.events[-1]["branch_admitted"]
    assert state.branch_pool==[]


def test_niche_reference_has_no_branch_pool_even_when_local_improvement_is_observed():
    state=_seeded("niche")
    state.observe(_node(2,.11,[[0,1,0]],parent=1,vector=[.105,.115]))
    assert state.events[-1]["branch_classification"]=="competitive_local_improvement"
    assert state.events[-1]["branch_admitted"] is False
    assert state.branch_pool==[]


def test_one_followup_proposal_consumes_one_fixed_branch_opportunity():
    state=_seeded("niche_fixed_dev")
    state.observe(_node(2,.11,[[0,1,0]],parent=1,vector=[.105,.115]))
    choice=state.choose(1)
    invalid={"id":3,"name":"invalid","intent":"invalid","tags":["return_aware"],
        "allocated_tag":"return_aware","code":"","parent_id":2,"reference_id":None,
        "action":"refine","source":"live_llm","allocation":choice["audit"],
        "evaluation":{"valid":False,"loss":1.0,"per_instance_loss":[],"behavior":[],
            "trajectory_behavior":[],"failure_type":"ProgramError","features_called":0,
            "local_checks":0,"cpu_seconds":0.0,"wall_seconds":0.0}}
    state.observe(invalid)
    branch=next(item for item in state.branch_pool if item["node_id"]==2)
    assert branch["remaining"]==1
    assert branch["attempts"]==1
    assert state.events[-1]["branch_parent_development"]


def test_relation_policy_changes_branch_choice_but_not_prompt_schema():
    fixed=_seeded("niche_fixed_dev")
    relational=_seeded("relational_branch")
    for state in (fixed,relational):
        state.observe(_node(2,.11,[[0,1,0]],parent=1,vector=[.105,.115]))
        selection=state.choose(1)
        assert selection["branch_development_scheduled"]
        parsed=json.loads(planner_prompt("tsp",selection,1))
        assert set(parsed)=={"task","task_contract","iteration","target_strategy_family","action",
            "parent","complementary_reference","allocation_evidence","allowed_tags",
            "common_seed_rules","recent_execution_feedback","quality_requirement","request"}
        assert parsed["allocation_evidence"]=={"decision_step":1}


def test_v12_data_splits_are_disjoint_and_use_fourteen_city_instances(monkeypatch):
    monkeypatch.setenv("CHAPTER6_BENCHMARK_PROFILE",V12_TSP_PROFILE)
    monkeypatch.setenv("CHAPTER6_DATA_BLOCK","0")
    instances.cache_clear()
    splits={name:instances("tsp",name) for name in ("probe","validation","test")}
    assert [len(splits[name]) for name in ("probe","validation","test")] == [12,36,60]
    assert all(len(instance["points"])==14 for values in splits.values() for instance in values)
    ids=[{instance["id"] for instance in values} for values in splits.values()]
    assert not (ids[0]&ids[1] or ids[0]&ids[2] or ids[1]&ids[2])
    assert len({split_fingerprint("tsp",name) for name in splits})==3
