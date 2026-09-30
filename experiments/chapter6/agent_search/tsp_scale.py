"""Scalable P-space TSP adapter. Not imported by historical/S1 experiments.

Reference tours are feasible heuristic upper bounds, NOT certified optima.
Candidate loss is signed relative-reference gap; negative values are retained.
All generated code executes only through the existing bounded interpreter.
"""
from __future__ import annotations
import math
import random
import time
import statistics
import numpy as np

from chapter6_demo.programs import Program,ProgramError
from chapter6_demo.v12_2.common import digest

VERSION='tsp-scale-priority-1'


def instance(family,seed,n):
    if n<4 or n>500:raise ValueError('Supported size 4..500')
    rng=random.Random(seed)
    if family=='uniform':points=[(rng.random(),rng.random()) for _ in range(n)]
    elif family=='clustered':
        centers=[(.15,.2),(.8,.25),(.5,.85)]
        points=[(centers[i%3][0]+rng.gauss(0,.06),centers[i%3][1]+rng.gauss(0,.06)) for i in range(n)]
    elif family=='grid':
        width=math.ceil(math.sqrt(n))
        points=[((i%width+rng.uniform(-.12,.12))/width,(i//width+rng.uniform(-.12,.12))/width) for i in range(n)]
    else:raise ValueError('Unknown instance family')
    rng.shuffle(points)
    return {'id':f'{VERSION}-{family}-n{n}-{seed}','family':family,'size':n,'points':points}


def matrix(points):
    array=np.asarray(points,dtype=np.float64)
    if array.ndim!=2 or array.shape[1]!=2 or not np.isfinite(array).all():raise ValueError('Invalid coordinates')
    delta=array[:,None,:]-array[None,:,:]
    return np.sqrt(np.sum(delta*delta,axis=2))


def tour_length(route,dist):
    if sorted(route)!=list(range(len(dist))):raise ValueError('Not a Hamiltonian cycle')
    return float(math.fsum(float(dist[route[i],route[(i+1)%len(route)]]) for i in range(len(route))))


def two_opt(route,dist,checks):
    """Fixed lexicographic first-improvement sweeps, charged per delta test."""
    route=list(route);n=len(route);used=0;improvements=0
    while used<checks:
        improved=False
        for i in range(1,n-1):
            for j in range(i+1,n):
                if used>=checks:return route,used,improvements
                used+=1
                a,b,c,d=route[i-1],route[i],route[j],route[(j+1)%n]
                if dist[a,c]+dist[b,d]<dist[a,b]+dist[c,d]-1e-12:
                    route[i:j+1]=reversed(route[i:j+1]);improved=True;improvements+=1
        if not improved:break
    return route,used,improvements


def reference(item,starts=4,checks_per_city=200):
    dist=matrix(item['points']);n=len(dist);best=None;used=0
    for start in sorted(set(int(k*n/starts) for k in range(starts))):
        remaining=set(range(n));remaining.remove(start);route=[start]
        while remaining:
            nxt=min(remaining,key=lambda j:(dist[route[-1],j],j));route.append(nxt);remaining.remove(nxt)
        route,count,_=two_opt(route,dist,checks_per_city*n);used+=count
        value=tour_length(route,dist)
        if best is None or (value,route)<(best['value'],best['route']):best={'value':value,'route':route}
    result={**best,'kind':'feasible_heuristic_upper_bound_not_optimum','method':'4-start-NN-plus-bounded-2opt',
            'starts':starts,'checks_per_city':checks_per_city,'actual_delta_tests':used}
    result['sha256']=digest(result)
    return result


def prepare(item):
    dist=matrix(item['points']);n=len(dist)
    scale=float(np.sum(dist))/(n*(n-1))
    if not scale>0:raise ValueError('Degenerate zero-distance instance')
    ref=item.get('reference')
    if ref is None:raise ValueError('Archive a feasible reference before evaluation')
    if not math.isclose(tour_length(ref['route'],dist),ref['value'],rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Reference certificate mismatch')
    return {'item':item,'dist':dist,'scaled':dist/scale,'n':n}


def execute(code,prepared,local_checks_per_city=10,max_seconds=30.):
    started=time.perf_counter();program=Program(code,'tsp')
    n=prepared['n'];dist=prepared['dist'];scaled=prepared['scaled']
    remaining=list(range(1,n));route=[0];calls=0
    while remaining:
        if time.perf_counter()-started>max_seconds:raise ProgramError('Instance wall-time limit')
        ids=np.asarray(remaining,dtype=int);k=len(ids)
        if k>1:
            others=scaled[np.ix_(ids,ids)].copy()
            total=others.sum(axis=1);squares=(others*others).sum(axis=1)
            means=total/(k-1);std=np.sqrt(np.maximum(0.,squares/(k-1)-means*means))
            density=((others<=1.).sum(axis=1)-1)/(k-1)
            np.fill_diagonal(others,np.inf);ordered=np.sort(others,axis=1)
            nearest=ordered[:,0];regret=ordered[:,1]-nearest if k>2 else np.zeros(k)
        else:nearest=means=regret=density=std=np.zeros(k)
        scores=[]
        for i,candidate in enumerate(remaining):
            f={'distance':float(scaled[route[-1],candidate]),'return_distance':float(scaled[candidate,0]),
               'nearest_remaining':float(nearest[i]),'mean_remaining':float(means[i]),
               'regret':float(regret[i]),'progress':len(route)/n,
               'cluster_density':float(density[i]),'spread':float(std[i])}
            scores.append(program(f));calls+=1
        choice=max(range(k),key=lambda i:(scores[i],-remaining[i]));route.append(remaining.pop(choice))
    before=tour_length(route,dist);before_route=list(route)
    checks=24 if local_checks_per_city==0 else local_checks_per_city*n
    route,used,improvements=two_opt(route,dist,checks)
    after=tour_length(route,dist);ref=prepared['item']['reference']['value']
    edges=sorted(tuple(sorted((route[i],route[(i+1)%n]))) for i in range(n))
    return {'valid':True,'loss':after/ref-1.,'pre_local_loss':before/ref-1.,
        'value':after,'pre_local_value':before,'reference':ref,
        'reference_kind':prepared['item']['reference']['kind'],
        'route':route,'pre_local_route':before_route,'edges':edges,
        'feature_calls':calls,'local_checks':used,'local_improvements':improvements,
        'wall_seconds':time.perf_counter()-started}


class Evaluator:
    def __init__(self,snapshot,allowed_splits=('probe','validation'),local_checks_per_city=10):
        if any(k not in ('probe','validation','test','metadata') for k in snapshot):raise ValueError('Unknown snapshot keys')
        if set(snapshot)-{'metadata'}!=set(allowed_splits):raise ValueError('Snapshot has a forbidden/missing split')
        self.snapshot=snapshot;self.prepared={k:[prepare(i) for i in snapshot[k]] for k in allowed_splits}
        self.local_checks_per_city=local_checks_per_city

    def evaluate(self,code):
        start=time.perf_counter()
        rows={}
        try:
            for split,items in self.prepared.items():rows[split]=[execute(code,p,self.local_checks_per_city) for p in items]
            split='validation' if 'validation' in rows else 'test';data=rows[split]
            behavior=[r['edges'] for r in rows.get('probe',[])]
            family={}
            for item,row in zip(self.snapshot[split],data):
                family.setdefault(f"{item['family']}-n{item['size']}",[]).append(row['loss'])
            return {'valid':True,'loss':statistics.fmean(r['loss'] for r in data),
                'pre_local_loss':statistics.fmean(r['pre_local_loss'] for r in data),
                'per_instance_loss':[r['loss'] for r in data],
                'family_loss':{f:statistics.fmean(v) for f,v in family.items()},
                'behavior':behavior,'solutions':[r['route'] for r in data],
                'feature_calls':sum(r['feature_calls'] for rs in rows.values() for r in rs),
                'local_checks':sum(r['local_checks'] for rs in rows.values() for r in rs),
                'wall_seconds':time.perf_counter()-start,'reference_kind':'feasible_heuristic_upper_bound_not_optimum'}
        except (ProgramError,ValueError,OverflowError,RecursionError) as exc:
            return {'valid':False,'loss':None,'per_instance_loss':[],'behavior':[],
                'failure_type':type(exc).__name__,'error':str(exc)[:180],'wall_seconds':time.perf_counter()-start}
