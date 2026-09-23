"""Checks of scientific invariants, evaluator validity and real policy effects."""
import itertools
import math

import pytest

from chapter6_demo.programs import Program, ProgramError
from chapter6_demo.benchmarks import (SEEDS, evaluate, behavior_distance, instances,
    optimum_tsp, _matrix, split_fingerprint)
from chapter6_demo.discovery import SearchState, report_archive, planner_prompt


def test_held_karp_against_exhaustive_tours():
    pts=((0.,0.),(.8,.2),(.1,.9),(.7,.6),(1.2,.9),(1.3,.1))
    d=_matrix(pts)
    exact=min(sum(d[r[i]][r[(i+1)%len(r)]] for i in range(len(r)))
              for p in itertools.permutations(range(1,len(pts))) for r in [(0,)+p])
    assert optimum_tsp(pts)==pytest.approx(exact)


@pytest.mark.parametrize("task",["tsp","binpack"])
def test_renaming_refactoring_and_monotone_transform_preserve_behavior(task):
    key="distance" if task=="tsp" else "gap"
    programs=[f'def priority(f):\n    return -f["{key}"]\n',
              f'def priority(f):\n    renamed = f["{key}"]\n    return 5 - 3 * renamed\n']
    first,second=[evaluate(code,task,split="probe",with_probes=False) for code in programs]
    assert first["valid"] and second["valid"]
    assert behavior_distance(first["behavior"],second["behavior"])==0
    assert first["per_instance_loss"]==second["per_instance_loss"]


@pytest.mark.parametrize("task",["tsp","binpack"])
def test_execution_has_no_candidate_seed_noise_and_valid_solutions(task):
    first=evaluate(SEEDS[task][0][2],task,split="probe",with_probes=False)
    second=evaluate(SEEDS[task][0][2],task,split="probe",with_probes=False)
    assert first["valid"] and first["behavior"]==second["behavior"]
    assert first["per_instance_loss"]==second["per_instance_loss"]
    for instance,solution in zip(instances(task,"probe"),first["solutions"]):
        if task=="tsp":
            assert sorted(solution)==list(range(len(instance["points"])))
        else:
            loads={}
            for item,bin_id in zip(instance["items"],solution):
                loads[bin_id]=loads.get(bin_id,0)+item
            assert max(loads.values())<=1+1e-10


@pytest.mark.parametrize("code",[
    "import os\ndef priority(f):\n    return 1",
    "def priority(f):\n    return f.__class__",
    "def priority(f):\n    return __import__('os').getcwd()",
    "def priority(f):\n    while True:\n        pass\n    return 1",
    "def priority(f):\n    return 2 ** 10000",
    "def priority(f):\n    return f['secret']",
])
def test_generated_code_cannot_escape_bounded_interpreter(code):
    with pytest.raises(ProgramError):
        Program(code,"tsp")


def test_splits_are_disjoint():
    for task in ("tsp","binpack"):
        seen=set()
        for split in ("probe","validation","test"):
            ids={i["id"] for i in instances(task,split)}
            assert not ids & seen
            seen |= ids
        assert len({split_fingerprint(task,s) for s in ("probe","validation","test")})==3


def node(idx,behavior,loss=.1,tags=None):
    return {"id":idx,"parent_id":None,"reference_id":None,"intent":"test","tags":tags or ["local_distance"],
        "code":"def priority(f):\n    return 0","action":"refine","source":"unit_test",
        "evaluation":{"valid":True,"loss":loss,"per_instance_loss":[loss],"behavior":[behavior],
           "trajectory_behavior":[behavior],"trajectory_values":[[1,loss]],"failure_type":None,
           "features_called":10,"local_checks":2,"cpu_seconds":.01,"wall_seconds":.01}}


def test_repeat_metric_is_across_distinct_intents_and_invalids_are_not_modes():
    state=SearchState("tsp","relational",0)
    state.observe(node(0,[1,0,1],tags=["local_distance"]))
    state.observe(node(1,[1,0,1],tags=["regret"]))
    assert state.events[-1]["different_intent_collision"]
    assert len(state.A)==1
    bad=node(2,[],tags=["cluster"])
    bad["evaluation"].update(valid=False,loss=None,failure_type="ProgramError")
    state.observe(bad)
    assert len(state.A)==1 and state.M[-1]["failure_type"]=="ProgramError"


def test_quality_gate_removes_old_low_quality_modes_after_improvement():
    items=[node(0,[1,0,0],.2),node(1,[0,1,0],.01),node(2,[0,0,1],.015)]
    assert {n["id"] for n in report_archive(items,"tsp")}=={1,2}


def test_terminal_evidence_changes_scheduling_for_same_elite_archive():
    s1=SearchState("tsp","relational",0)
    for idx,tag in enumerate(["local_distance","return_aware","regret","cluster","lookahead","progress","nonlinear","hybrid"]):
        behavior=[int(j==idx) for j in range(8)]
        s1.observe(node(idx,behavior,tags=[tag]))
    import copy
    s2=copy.deepcopy(s1)
    original=s1.choose(1)["target"]
    for i in range(3):
        s2.M.append({"tags":[original],"valid":True,"useful_gain":False,"terminal_collision":True,
                     "behavior_hash":"same"})
    assert [n["id"] for n in s1.A]==[n["id"] for n in s2.A]
    assert s2.choose(1)["target"]!=original


def test_test_data_is_not_in_planner_context():
    state=SearchState("tsp","relational",0)
    state.observe(node(0,[1,0,1]))
    prompt=planner_prompt("tsp",state.choose(0),0)
    assert "97100" not in prompt and "test_loss" not in prompt
