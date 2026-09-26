import pytest
from chapter6_demo.agent_search.budget import Budget,byte_reservation


def test_reservation_stops_before_request_and_counts_failures():
    b=Budget(1000,2,60)
    assert b.reserve('a',100,400,0)['admitted']
    b.complete(50,300,20)
    assert b.reserve('b',100,600,20)['admitted'] is False
    assert b.requests==1
    assert b.reserve('b',100,400,20)['admitted']
    b.complete(50,300,40)
    assert not b.reserve('c',1,1,40)['admitted']
    assert b.summary()['total_tokens']==700


def test_unknown_usage_is_not_zero_no_automatic_retry():
    b=Budget(1000,10,60);b.reserve('a',100,400,0)
    b.complete(None,None,20,unknown=True)
    assert b.summary()['total_tokens'] is None and b.requests==1
    assert not b.reserve('b',1,1,20)['admitted']
    with pytest.raises(ValueError):b.reserve('a',1,1,20)


def test_worst_case_unavailable_is_not_falsely_claimed_hard():
    r=byte_reservation('sys','prompt',4096)
    assert r['certified'] is False
    b=Budget(10000,5,30);b.reserve('a',10,20,0)
    out=b.complete(12,20,2)
    assert out['halted']=='provider_exceeded_reservation'
    assert not b.summary()['token_budget_certified']


def test_wall_budget_and_pending_double_charge():
    b=Budget(1000,10,30)
    assert not b.reserve('a',1,1,31)['admitted']
    assert b.reserve('a',1,10,5)['remaining_wall_seconds']==25
    with pytest.raises(ValueError):b.reserve('b',1,10,6)
    assert b.summary()['total_tokens'] is None
