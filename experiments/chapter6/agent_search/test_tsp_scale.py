import copy
import math
import pytest
from chapter6_demo.agent_search import tsp_scale as t
from chapter6_demo.benchmarks import SEEDS


def prepared(n=12,seed=7001):
    item=t.instance('uniform',seed,n);item['reference']=t.reference(item)
    return item,t.prepare(item)


@pytest.mark.parametrize('n',[6,14,50,100])
def test_feasible_references_bounded_exec_and_accounting(n):
    item,p=prepared(n)
    result=t.execute(SEEDS['tsp'][0][2],p)
    assert sorted(result['route'])==list(range(n))
    assert result['feature_calls']==n*(n-1)//2
    assert result['local_checks']<=10*n
    assert result['value']<=result['pre_local_value']+1e-10
    assert math.isclose(result['loss'],result['value']/item['reference']['value']-1.)
    assert result['reference_kind']=='feasible_heuristic_upper_bound_not_optimum'


def test_negative_signed_gap_retained_not_clipped():
    item=t.instance('uniform',9008,14);dist=t.matrix(item['points'])
    route=list(range(14))
    item['reference']={'route':route,'value':t.tour_length(route,dist),'kind':'deliberately_weak_fixture_upper_bound'}
    r=t.execute(SEEDS['tsp'][0][2],t.prepare(item))
    assert r['loss']<0


def test_reference_path_certificate_and_test_split_denial():
    item,p=prepared()
    wrong=copy.deepcopy(item);wrong['reference']['value']+=.1
    with pytest.raises(ValueError,match='certificate'):t.prepare(wrong)
    with pytest.raises(ValueError,match='forbidden'):t.Evaluator({'probe':[item],'validation':[item],'test':[item]})
    e=t.Evaluator({'probe':[item],'validation':[item]})
    assert e.evaluate(SEEDS['tsp'][0][2])['valid']
    assert not e.evaluate('import os')['valid']


def test_size_guard_and_features_do_not_include_reference():
    with pytest.raises(ValueError):t.instance('uniform',1,10000)
    item,p=prepared()
    code='def priority(f):'+chr(10)+'    return f["reference"]'
    assert not t.Evaluator({'test':[item]},allowed_splits=('test',)).evaluate(code)['valid']
