"""Fixed probe / validation / hidden-test instances and executable evaluators."""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import time
from functools import lru_cache

from .programs import Program, ProgramError, FEATURES
from .classification import CLASS_TAGS, CLASS_DESCRIPTION, CLASS_SEEDS

VERSION = "heuristic-benchmark-2-frozen-pilot"
INDEPENDENT_PROFILE = "chapter6-v11-independent-v1"
V12_TSP_PROFILE = "chapter6-v12-tsp14-v1"
TAGS = {
    "tsp": ["local_distance", "return_aware", "regret", "cluster", "lookahead", "progress", "nonlinear", "hybrid"],
    "binpack": ["tight_fit", "loose_fit", "exact_fill", "balance", "distribution", "item_size", "nonlinear", "hybrid"],
    "classification": CLASS_TAGS,
}
DESCRIPTIONS = {
    "classification": CLASS_DESCRIPTION,
    "tsp": "Generate a sequential TSP construction heuristic. Starting at city 0, the candidate with the HIGHEST priority is visited next. A fixed budget of 24 deterministic 2-opt delta tests then refines every tour for ALL methods. All distances are normalized by instance mean distance. f keys: distance=current-to-candidate, return_distance=candidate-to-start, nearest_remaining=nearest other unvisited distance, mean_remaining=mean distance to others, regret=second-nearest minus nearest among other unvisited nodes, progress=visited fraction, cluster_density=fraction of other unvisited cities within mean distance, spread=std of candidate-to-unvisited distances. Higher priority wins; ties use city index. No route indices or reference optima are available to your function.",
    "binpack": "Generate an ONLINE 1D bin-packing placement priority with bin capacity 1. For the arriving item, pick the existing FEASIBLE bin with the HIGHEST priority; a new bin opens only if no existing bin fits. f keys: item=size, remaining=capacity before insertion, gap=capacity after insertion, fill=1-remaining, mean_gap/min_gap/std_gap=statistics across feasible bins after insertion, fraction_fitting=feasible count divided by open-bin count, position=bin index/open-bin count. Ties use bin index. There is no access to future items. Goal: minimize bin count relative to the volume lower bound.",
}
SEEDS = {
    "classification": CLASS_SEEDS,
    "tsp": [
        ("nearest_neighbor", ["local_distance"], 'def priority(f):\n    return -f["distance"]\n'),
        ("return_aware", ["local_distance","return_aware"], 'def priority(f):\n    return -f["distance"] + 0.25 * f["return_distance"]\n'),
        ("regret_aware", ["local_distance","regret"], 'def priority(f):\n    return -f["distance"] + 0.3 * f["regret"]\n'),
    ],
    "binpack": [
        ("best_fit", ["tight_fit"], 'def priority(f):\n    return -f["gap"]\n'),
        ("first_fit", ["balance"], 'def priority(f):\n    return -f["position"]\n'),
        ("worst_fit", ["loose_fit"], 'def priority(f):\n    return f["gap"]\n'),
    ],
}


def _make_instance(task, family, seed, size):
    rng = random.Random(seed)
    if task == "tsp":
        if family == "uniform":
            points = [(rng.random(),rng.random()) for _ in range(size)]
        elif family == "clustered":
            centers = [(0.15,0.2),(0.8,0.25),(0.5,0.85)]
            points = [(centers[i%3][0]+rng.gauss(0,0.06), centers[i%3][1]+rng.gauss(0,0.06)) for i in range(size)]
        else:
            points = [(i%4 + rng.uniform(-0.12,0.12), i//4 + rng.uniform(-0.12,0.12)) for i in range(size)]
        rng.shuffle(points)
        return {"id":f"{family}-{seed}","family":family,"points":points}
    if family == "uniform":
        items = [rng.uniform(0.08,0.8) for _ in range(size)]
    elif family == "bimodal":
        items = [rng.uniform(0.1,0.3) if rng.random()<0.58 else rng.uniform(0.5,0.7) for _ in range(size)]
    else:
        # Adversarial complementary sizes, shuffled; no optimal packing is
        # revealed to the policy. The volume bound is only an evaluation aid.
        items = [rng.choice([0.18,0.22,0.28,0.32,0.48,0.52,0.68]) + rng.uniform(-0.012,0.012) for _ in range(size)]
    return {"id":f"{family}-{seed}","family":family,"items":items}


def instances(task, split):
    profile=os.getenv("CHAPTER6_BENCHMARK_PROFILE", "")
    block=int(os.getenv("CHAPTER6_DATA_BLOCK", "0"))
    return _instances_cached(task, split, profile, block)


@lru_cache(maxsize=128)
def _instances_cached(task, split, profile, block):
    if split not in ("probe","validation","test"):
        raise ValueError("Unknown split.")
    if task == "classification":
        from .classification import cases
        return cases(split)
    if profile == INDEPENDENT_PROFILE:
        if task not in ("tsp","binpack"):
            raise ValueError("Independent v1.1 profile is defined for TSP and binpack only.")
        families=("uniform","clustered","grid") if task=="tsp" else ("uniform","bimodal","complementary")
        offsets={"probe":371700,"validation":544300,"test":797100}
        counts={"probe":3,"validation":8,"test":12}
        if split not in offsets:
            raise ValueError("Unknown split.")
        size=12 if task=="tsp" else 64
        base=offsets[split]+block*10_000
        return tuple(_make_instance(task,family,base+fi*100+j,size)
                     for fi,family in enumerate(families) for j in range(counts[split]))
    if profile == V12_TSP_PROFILE:
        if task != "tsp":
            raise ValueError("The v1.2 mechanism profile is defined for TSP only.")
        families=("uniform","clustered","grid")
        offsets={"probe":918000,"validation":934000,"test":962000}
        counts={"probe":4,"validation":12,"test":20}
        if split not in offsets:
            raise ValueError("Unknown split.")
        base=offsets[split]+block*20_000
        return tuple(_make_instance(task,family,base+fi*200+j,14)
                     for fi,family in enumerate(families) for j in range(counts[split]))
    # All generation rules and seeds are fixed before the live experiment.
    family_names = ("uniform","clustered","grid") if task == "tsp" else ("uniform","bimodal","complementary")
    offset = {"probe":21700,"validation":44300,"test":97100}[split]
    count = {"probe":2,"validation":4,"test":8}[split]
    size = (10 if split == "probe" else 12) if task == "tsp" else (32 if split == "probe" else 64)
    return tuple(_make_instance(task, family, offset+fi*100+j, size)
                 for fi,family in enumerate(family_names) for j in range(count))


instances.cache_clear = _instances_cached.cache_clear


def split_fingerprint(task, split):
    return hashlib.sha256(json.dumps(instances(task,split),sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=128)
def _matrix(points):
    return tuple(tuple(math.hypot(a[0]-b[0],a[1]-b[1]) for b in points) for a in points)


@lru_cache(maxsize=128)
def optimum_tsp(points):
    """Held-Karp exact objective; reference generation is outside search cost."""
    dist = _matrix(points)
    n = len(points)
    dp = {(1 << (j-1),j):dist[0][j] for j in range(1,n)}
    for mask in range(1,1<<(n-1)):
        for last in range(1,n):
            bit = 1 << (last-1)
            if not mask & bit or mask == bit:
                continue
            prev = mask ^ bit
            dp[(mask,last)] = min(dp[(prev,k)] + dist[k][last]
                                 for k in range(1,n) if prev & (1<<(k-1)))
    full=(1<<(n-1))-1
    return min(dp[(full,j)]+dist[j][0] for j in range(1,n))


def tour_edges(route):
    return frozenset(tuple(sorted((route[i],route[(i+1)%len(route)]))) for i in range(len(route)))


def _tour_vector(route):
    edges=tour_edges(route)
    return [int((i,j) in edges) for i in range(len(route)) for j in range(i+1,len(route))]


def tsp_execute(program, instance):
    points=tuple(tuple(x) for x in instance["points"])
    dist=_matrix(points)
    n=len(points)
    scale=sum(sum(row) for row in dist)/(n*(n-1))
    route=[0]
    remaining=list(range(1,n))
    calls=0
    checkpoints=[]
    while remaining:
        values=[]
        for candidate in remaining:
            others=sorted(dist[candidate][j]/scale for j in remaining if j!=candidate)
            near=others[0] if others else 0.0
            f={"distance":dist[route[-1]][candidate]/scale,
               "return_distance":dist[candidate][0]/scale,
               "nearest_remaining":near,"mean_remaining":statistics.fmean(others) if others else 0.0,
               "regret":(others[1]-near) if len(others)>1 else 0.0,
               "progress":len(route)/n,
               "cluster_density":sum(x<=1 for x in others)/max(1,len(others)),
               "spread":statistics.pstdev(others) if others else 0.0}
            values.append(program(f))
            calls+=1
        chosen=max(range(len(remaining)),key=lambda i:(values[i],-remaining[i]))
        route.append(remaining.pop(chosen))
    length=lambda r:sum(dist[r[i]][r[(i+1)%n]] for i in range(n))
    before=length(route)
    pre_vector=_tour_vector(route)
    checkpoints.append(before)
    moves=0
    # Fixed, counted local exploitation for every candidate and comparator.
    for i in range(1,n-1):
        for j in range(i+1,n):
            if moves>=24:
                break
            moves+=1
            a,b,c,d=route[i-1],route[i],route[j],route[(j+1)%n]
            if dist[a][c]+dist[b][d] < dist[a][b]+dist[c][d]-1e-12:
                route[i:j+1]=reversed(route[i:j+1])
                checkpoints.append(length(route))
        if moves>=24:
            break
    value=length(route)
    optimal=optimum_tsp(points)
    return {"loss":max(0.0,value/optimal-1),"value":value,"reference":optimal,
            "features_called":calls,"local_checks":moves,"behavior":_tour_vector(route),
            "trajectory_behavior":pre_vector,"trajectory_values":checkpoints+[value],
            "solution":route}


def binpack_execute(program, instance):
    bins=[]
    assignment=[]
    calls=0
    checkpoints=[]
    for item in instance["items"]:
        feasible=[i for i,remaining in enumerate(bins) if remaining+1e-12>=item]
        if not feasible:
            chosen=len(bins)
            bins.append(1.0)
        else:
            gaps=[max(0.0,bins[i]-item) for i in feasible]
            mean=statistics.fmean(gaps)
            std=statistics.pstdev(gaps)
            scores=[]
            for i,gap in zip(feasible,gaps):
                f={"item":item,"remaining":bins[i],"gap":gap,"fill":1-bins[i],
                   "mean_gap":mean,"min_gap":min(gaps),"std_gap":std,
                   "fraction_fitting":len(feasible)/max(1,len(bins)),"position":i/max(1,len(bins))}
                scores.append(program(f))
                calls+=1
            chosen=feasible[max(range(len(feasible)),key=lambda j:(scores[j],-feasible[j]))]
        bins[chosen]-=item
        assignment.append(chosen)
        if len(assignment)%8==0:
            checkpoints.append(len(bins))
    bound=math.ceil(sum(instance["items"])-1e-12)
    # Co-bin relation ignores arbitrary bin labels; first 24 items bound the
    # descriptor dimension and expose sequential decisions on fixed probes.
    count=min(24,len(assignment))
    behavior=[int(assignment[i]==assignment[j]) for i in range(count) for j in range(i+1,count)]
    prefix=[int(assignment[i]==assignment[j]) for i in range(min(12,count)) for j in range(i+1,min(12,count))]
    return {"loss":len(bins)/bound-1,"value":len(bins),"reference":bound,
            "features_called":calls,"local_checks":0,"behavior":behavior,
            "trajectory_behavior":prefix,"trajectory_values":checkpoints,
            "solution":assignment}


def execute(program, instance):
    return tsp_execute(program,instance) if program.task=="tsp" else binpack_execute(program,instance)


def set_distance(a,b):
    if len(a)!=len(b):
        raise ValueError("Behavior vectors require the same evaluator and dimension.")
    union=sum(bool(x or y) for x,y in zip(a,b))
    return 0.0 if union==0 else sum(x!=y for x,y in zip(a,b))/union


def behavior_distance(left,right):
    if len(left)!=len(right):
        raise ValueError("Behavior descriptors require aligned probe instances.")
    return statistics.fmean(set_distance(a,b) for a,b in zip(left,right)) if left else 0.0


def evaluate(code,task,split="validation",with_probes=True):
    if task == "classification":
        from .classification import evaluate_classification
        return evaluate_classification(code,split,with_probes)
    start=time.perf_counter()
    cpu=time.process_time()
    calls=local_checks=attempted=0
    program=None
    try:
        program=Program(code,task)
        outcomes=[]
        for instance in instances(task,split):
            attempted+=1
            out=execute(program,instance)
            calls+=out["features_called"]
            local_checks+=out["local_checks"]
            outcomes.append(out)
        probes=[]
        if with_probes:
            for instance in instances(task,"probe"):
                attempted+=1
                out=execute(program,instance)
                calls+=out["features_called"]
                local_checks+=out["local_checks"]
                probes.append(out)
        groups={}
        for instance,out in zip(instances(task,split),outcomes):
            groups.setdefault(instance["family"],[]).append(out["loss"])
        result={"valid":True,"loss":statistics.fmean(x["loss"] for x in outcomes),
                "per_instance_loss":[x["loss"] for x in outcomes],
                "per_instance_value":[x["value"] for x in outcomes],
                "family_loss":{k:statistics.fmean(v) for k,v in groups.items()},
                "behavior":[x["behavior"] for x in (probes or outcomes)],
                "trajectory_behavior":[x["trajectory_behavior"] for x in (probes or outcomes)],
                "trajectory_values":[x["trajectory_values"] for x in (probes or outcomes)],
                "program_hash":program.hash,"ast_nodes":program.ast_nodes,
                "solutions":[x["solution"] for x in outcomes],
                "failure_type":None}
    except (ProgramError,ValueError,ArithmeticError,KeyError) as exc:
        result={"valid":False,"loss":None,"behavior":[],"trajectory_behavior":[],
                "failure_type":type(exc).__name__,"error":str(exc)[:200]}
    result.update(split=split,features_called=program.calls if program is not None else 0,local_checks=local_checks,
                  instance_evaluations=attempted,
                  wall_seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu)
    return result


def descriptor_hash(value):
    return hashlib.sha256(json.dumps(value,separators=(",",":")).encode()).hexdigest()[:16]
