import json
import pytest
from chapter6_demo.agent_search import s0_r3 as r3
from chapter6_demo.s0_output_calibration.test_calibration import FakeTransport,no_network
from chapter6_demo.s0_output_calibration import calibration as s0
from chapter6_demo.v12_2.common import digest,read_json,save_json


def study(path):
    m={'schema':'s0-r3-manifest','protocol':read_json(r3.PROTOCOL),'source_commit':'fixture','jobs':r3.jobs()}
    m['manifest_sha256']=digest(m);save_json(path/'manifest.json',m)


def test_new_contexts_and_call_budget():
    old={digest(j['fixture']) for j in s0.acceptance_jobs()+s0.e2e_jobs()+s0.calibration_jobs(read_json(s0.PROTOCOL))}
    new={digest(j['fixture']) for j in r3.jobs()}
    assert not old&new and len(r3.jobs())==48
    assert {j['config_id'] for j in r3.jobs()}=={'fixed_headroom'}


def test_fixed_config_acceptance_and_actual_plan_handoff(tmp_path):
    study(tmp_path);t=FakeTransport()
    res=r3.run(tmp_path,transport=t,runtime=False)
    assert res['ready_for_s1'] and len(t.requests)==48
    assert {r['max_tokens'] for r in t.requests if r['stage']=='planner'}=={16384}
    assert {r['max_tokens'] for r in t.requests if r['stage']=='coder'}=={8192}
    assert json.loads(t.requests[-1]['prompt'])['plan']['name'].startswith('actual_plan_')
    assert r3.run(tmp_path,transport=t,runtime=False)==res and len(t.requests)==48


def test_fixed_failure_gate_cannot_reselect_or_retry(tmp_path):
    study(tmp_path);t=FakeTransport(fail=8)
    res=r3.run(tmp_path,transport=t,runtime=False)
    assert not res['ready_for_s1'] and res['status']=='infrastructure_halted'
    r3.run(tmp_path,transport=t,runtime=False)
    assert len(t.requests)==9


@pytest.mark.parametrize('index',[0,18,36,37,47])
def test_resume_persisted_response(tmp_path,index):
    study(tmp_path);t=FakeTransport(interrupt=index)
    with pytest.raises(KeyboardInterrupt): r3.run(tmp_path,transport=t,runtime=False)
    t.interrupt=None
    assert r3.run(tmp_path,transport=t,runtime=False)['ready_for_s1']
    assert len(t.requests)==48
