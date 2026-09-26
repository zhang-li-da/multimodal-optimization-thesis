from chapter6_demo.agent_search.directions import describe,lineage
from chapter6_demo.agent_search.review_s1 import numeric_compare

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
