"""Synthetic only. These are not MiniMax strategy-effect experiments."""
import copy
import pytest
from chapter6_demo.agent_search.policies import DirectionController,POLICIES

CODES=['def priority(f):\n    return -f["distance"]','def priority(f):\n    return -f["distance"]+.1*f["regret"]','def priority(f):\n    return -f["distance"]+.1*f["return_distance"]']

def node(i,loss,code=None,parent=None,behavior=None,valid=True):
    return {'id':i,'code':code or CODES[i%3],'parent_id':parent,'evaluation':{'valid':valid,
           'loss':loss,'per_instance_loss':[loss],'behavior':behavior if behavior is not None else [[i,0,1]]}}

def initialized(policy,**kw):
    s=DirectionController(policy,seed=42,**kw)
    s.initialize([node(0,.1),node(1,.12),node(2,.14)])
    return s

def test_common_seeds_fixed_quality_envelope_and_capacity():
    for policy in POLICIES:
        s=initialized(policy)
        assert s.init_best==.1 and s.output()['best_id']==0
        assert len(s.active)==(2 if policy in ('FB','TS','AD') else 0)
        assert all(e['remaining']==2 for e in s.active.values())

@pytest.mark.parametrize('policy,expected',[('SP','D'),('WR','E')])
def test_single_path_and_restart_extremes(policy,expected):
    s=initialized(policy)
    for i in range(10):
        d=s.choose(i/10)
        assert d['action']==expected
        assert (d['parent_id']==s.best_id) if policy=='SP' else d['parent_id'] is None
        s.observe(node(3+i,.15,parent=d['parent_id']))

def test_probabilities_reproducible_and_feedback_changes_probability():
    f=initialized('FB');t=initialized('TS');a=initialized('AD')
    assert f.choose(.0)['p_develop']==.5
    assert t.choose(.0)['p_develop']==.2
    assert a.choose(.0)['p_develop']==.5
    t2=initialized('TS');assert t2.choose(1.)['p_develop']==.8
    a2=initialized('AD')
    a2.history=[{'action':'D','success':True}]*8+[{'action':'E','success':False}]*8
    assert a2.choose(.5)['p_develop']==.8
    a3=initialized('AD');a3.history=[{'action':'E','success':True}]*8+[{'action':'D','success':False}]*8
    assert a3.choose(.5)['p_develop']==.2

def test_empty_pool_fallback_and_budget_stop():
    s=initialized('AD');s.active.clear()
    assert s.choose(.1,request_fits=False)['action']=='STOP' and s.pending is None
    s.rng.seed(1);d=s.choose(.1)
    assert d['intended_action']=='D' and d['action']=='E' and d['fallback']

def test_known_reproduction_does_not_renew_grant_and_invalid_attempt_charged():
    s=initialized('FB');s.rng.seed(1);d=s.choose(.1)
    entry=s.active[d['direction_id']];before=entry['remaining']
    p=s.nodes[d['parent_id']]
    n=copy.deepcopy(p);n.update(id=3,parent_id=d['parent_id'])
    e=s.observe(n)
    assert e['known_reproduction'] and not e['admitted'] and entry['remaining']==before-1
    s.rng.seed(1);d=s.choose(.2);entry=s.active[d['direction_id']];before=entry['remaining']
    s.observe(node(4,None,code='invalid',parent=d['parent_id'],valid=False))
    assert entry['remaining']==before-1

def test_same_direction_attempts_cannot_exceed_lifetime_cap():
    s=initialized('FB',maximum_attempts=4)
    for i in range(30):
        s.rng.seed(1);d=s.choose(.2)
        code=s.nodes[d['parent_id']]['code'] if d['parent_id'] is not None else CODES[0]
        # Known reproduction deliberately never unlocks additional credit.
        n=node(3+i,.1,code=code,parent=d['parent_id'],behavior=[[0,0,1]])
        s.observe(n)
    assert all(v['attempts']<=4 and v['granted']<=4 for v in s.ledgers.values())

def test_indeterminate_not_observed_and_duplicate_or_wrong_parent_rejected():
    s=initialized('FB');d=s.choose(.1);n=node(3,.09,parent=d['parent_id'])
    e=s.observe(n,provider_indeterminate=True)
    assert e['halt'] and not s.history and 3 not in s.nodes
    with pytest.raises(ValueError):s.choose(.2)
    with pytest.raises(ValueError):s.output()
    with pytest.raises(ValueError):s.observe(node(3,.09,parent=999))

def test_capacity_full_cannot_evict_initial_protection():
    s=initialized('FB',capacity=1)
    original=copy.deepcopy(s.active)
    d=s.choose(.1)
    assert d['action']=='E'
    e=s.observe(node(3,.09,code=CODES[2]))
    assert not e['admitted'] and s.active==original
