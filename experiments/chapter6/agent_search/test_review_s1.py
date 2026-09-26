from chapter6_demo.agent_search.directions import describe,lineage
from chapter6_demo.agent_search.review_s1 import numeric_compare

import pytest
from chapter6_demo.agent_search.review_s1 import audit_run,grant_ledger
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import read_json,save_json
from chapter6_demo.v12_2.fixtures import FakeTransport
from chapter6_demo.v12_2.runner import run_search
from chapter6_demo.v12_2.test_runner import job,small_snapshot,PARAMETERS,synthetic_evaluator

def test_direction_not_based_on_variable_names_or_tags():
    a=describe('def priority(f):\n    value = -f["distance"]\n    return value + 0.1*f["regret"]')
    b=describe('def priority(f):\n    score = -f["distance"]\n    return score + 0.1*f["regret"]')
    assert a['direction_id']==b['direction_id']
    assert a['alpha_normalized_ast_sha256']==b['alpha_normalized_ast_sha256']
    c=describe('def priority(f):\n    return -f["distance"] + 0.1*f["return_distance"]')
    assert a['direction_id']!=c['direction_id']
    assert not a['semantic_mode_verified']

def test_unknown_is_not_forced_into_mode():
    assert describe('import os')['direction_id']=='unknown'

def test_lineage_is_separate_from_code_signature():
    nodes=[{'id':0},{'id':1,'parent_id':0},{'id':2},{'id':3,'parent_id':1}]
    assert lineage(nodes)=={0:0,1:0,2:2,3:0}

def test_numerical_and_path_tolerances():
    a={'valid':True,'loss':.3,'solutions':[[0,1,2]],'behavior':[[0,1,0]]}
    b={**a,'loss':.3+2.22e-16}
    assert not numeric_compare(a,b)[0]
    assert numeric_compare(a,{**b,'loss':.31})[0]
    assert numeric_compare(a,{**a,'solutions':[[0,2,1]]})[0]


@pytest.mark.parametrize('malformed', [(), (1,)])
def test_both_prompts_and_persisted_candidates_replayed_without_model_calls(tmp_path,malformed):
    j={**job(steps=3),'search_seed_label':0}
    client=FakeTransport(malformed_planner_steps=malformed)
    run=tmp_path/'runs'/j['job_id']
    save_json(tmp_path/'data/search-b3.json',small_snapshot())
    with offline_only():
        run_search(j,small_snapshot(),run,{'fixture':True},PARAMETERS,client)
        count=len(client.requests)
        records,calls,descriptors,checks=audit_run(tmp_path,j,numeric=True)
    assert len(records)==3 and len(calls)==6-len(malformed)
    assert checks['planner_and_coder_prompt_replay']
    assert checks['response_candidate_correspondence']
    assert all(r['passed'] for r in checks['numerical'])
    assert len(client.requests)==count
    # The fake service omits finish_reason. Completeness is NOT inferred from
    # a candidate being executable, and the audit does not drop that candidate.
    assert not any(c['s0_complete'] for c in calls)
    assert any(r['valid'] for r in records)


def test_coder_prompt_tampering_is_detected_even_with_consistent_raw_hashes(tmp_path):
    j={**job(steps=1),'search_seed_label':0}
    run=tmp_path/'runs'/j['job_id']
    with offline_only():
        run_search(j,small_snapshot(),run,{'fixture':True},PARAMETERS,FakeTransport())
        folder=run/'calls/000-coder'
        from chapter6_demo.v12_2.common import digest
        request=read_json(folder/'request.json');request['prompt']='different plan'
        sha=digest(request)
        save_json(folder/'request.json',request)
        for name in ('raw_response.json','response.json','state.json'):
            value=read_json(folder/name);value['request_sha256']=sha;save_json(folder/name,value)
        with pytest.raises(AssertionError,match='coder request differs'):
            audit_run(tmp_path,j)


def test_grant_ledger_retains_unused_grants_and_counts_every_charged_attempt(tmp_path):
    j={**job(steps=8),'search_seed_label':0}
    run=tmp_path/'runs'/j['job_id']
    with offline_only():
        result=run_search(j,small_snapshot(),run,{'fixture':True},PARAMETERS,FakeTransport(),
                          evaluator=synthetic_evaluator)
        grants=grant_ledger(tmp_path,j)
    assert len(grants)==result['summary']['branch_admissions']
    assert sum(r['attempts_received'] for r in grants)==result['summary']['branch_development_attempts']
    assert any(r['unused_at_horizon'] for r in grants)
    assert all(r['attempts_received']+r['unused_at_horizon']+r['unused_at_eviction']==r['initial_grant'] for r in grants)
