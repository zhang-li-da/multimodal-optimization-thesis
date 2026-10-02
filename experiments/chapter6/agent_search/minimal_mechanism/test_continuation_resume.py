import base64
import json
import zipfile
from pathlib import Path

import pytest

from chapter6_demo.v12_2.common import read_json, save_json, digest, file_sha
from experiments.chapter6.agent_search.minimal_mechanism import continuation_resume as m
from experiments.chapter6.agent_search.minimal_mechanism.test_phase_b_runner import _FixtureTransport

@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'verify_index', lambda: {})
    monkeypatch.setattr(m, 'git', lambda *args: '' if args[0] == 'status' else 'fixture-source')
    out = tmp_path / 'batch'
    registry = tmp_path / 'registry'
    monkeypatch.setattr(m, 'family_registry', lambda _: registry)
    # Portable real-data fixture from the published archive; no network or Test.
    prefix=tmp_path/'prefix';prefix.mkdir()
    with zipfile.ZipFile(m.HISTORY/'phase_b_continuation_20261002_r3.zip') as z:
        for name in z.namelist():
            if name.startswith(('data/','checkpoints/')) and not name.endswith('/'):
                path=prefix/name;path.parent.mkdir(exist_ok=True,parents=True);path.write_bytes(z.read(name))
    data=[{'path':p.relative_to(prefix).as_posix(),'sha256':file_sha(p)} for p in sorted((prefix/'data').glob('*.json'))]
    pm={'data':data};pm['manifest_sha256']=digest(pm);save_json(prefix/'manifest.json',pm)
    cps=[{'id':p.stem,'path':p.relative_to(prefix).as_posix(),'sha256':file_sha(p)} for p in sorted((prefix/'checkpoints').glob('*.json'))]
    save_json(prefix/'PREFIX_AUDIT.json',{'checkpoints':cps})
    manifest = m.prepare(out, prefix, registry)
    return out, manifest, registry


def test_actual_history_excluded_and_missing_checkpoints_are_terminal(prepared):
    out, manifest, registry = prepared
    ready = [j for j in manifest['jobs'] if j['eligibility'] == 'ready']
    assert len(ready) == 101
    assert manifest['limits']['new_requests'] == 1616
    assert not {'eg-b64-step24-r0','e0-b62-step24-r0','e0-b63-step24-r0'} & {j['job_id'] for j in ready}
    progress = m.audit(out)
    assert progress['status_counts'] == {'historical_attempt_preserved': 3, 'preparation_incomplete': 24, 'not_started': 101}
    assert progress['cost_including_history']['known_tokens'] == 323277
    assert progress['cost_including_history']['requests'] == 83
    assert progress['cost_including_history']['unknown_requests'] == 3
    assert progress['cost_including_history']['unknown_reservation'] > 0
    assert not progress['all_tasks_terminal']
    assert all(set(read_json(p)) == {'block','profile','probe','validation'} for p in (out/'data').glob('*.json'))
    assert m.verify(out)['manifest_sha256'] == manifest['manifest_sha256']


def test_new_output_cannot_reset_registry_or_cost(prepared, tmp_path):
    out, manifest, registry = prepared
    before = m.ledger(out, manifest)
    with pytest.raises(ValueError, match='already registered'):
        m.prepare(tmp_path/'replacement', tmp_path/'prefix', registry)
    assert before == m.ledger(out, manifest)
    assert not (tmp_path/'replacement').exists()


def test_changed_snapshot_rejected_before_transport(prepared):
    out, manifest, registry = prepared
    p = out/'data/search-b60.json'
    data = read_json(p);data['test'] = []
    save_json(p,data)
    with pytest.raises(ValueError, match='input changed'):
        m.dispatch(out, transport_factory=lambda *a: pytest.fail('must not construct live transport'))


def test_claim_without_terminal_never_runs_again(prepared):
    out, manifest, registry = prepared
    job = next(j for j in manifest['jobs'] if j['eligibility']=='ready')
    save_json(registry/'claims'/f"{job['job_id']}.json", {'was_started':True})
    result = m.dispatch(out, transport_factory=lambda *a: pytest.fail('reposted existing attempt'))
    assert result['halted']
    assert read_json(out/'runs'/job['job_id']/'terminal_status.json')['status']=='infrastructure_incomplete'
    with pytest.raises(ValueError,match='halted'):
        m.dispatch(out)


class FixtureTransport(_FixtureTransport):
    last_diagnostics = None
    def set_wall_deadline(self, value):
        self.deadline=value


def test_actual_runner_complete_then_529_halts_before_third_job(prepared):
    out, manifest, registry = prepared
    transports=[]
    class Failing(FixtureTransport):
        def send(self,request,persist):
            from chapter6_demo.v12_2.calls import ProviderFailure
            self.last_diagnostics={'http_status':529,'error_category':'provider_server'}
            error=ProviderFailure('fixture overload');error.diagnostics=self.last_diagnostics
            raise error
    def factory(*args):
        t=FixtureTransport() if not transports else Failing()
        transports.append(t)
        return t
    result=m.dispatch(out,transport_factory=factory)
    assert len(transports)==2
    assert result['status_counts']['continuation_complete']==1
    assert result['status_counts']['infrastructure_incomplete']==1
    assert result['status_counts']['not_started']==99
    assert result['halted']
    assert result['cost_including_history']['requests']==100
    assert result['cost_including_history']['unknown_requests']==4
    # Audit is repeatable and keeps the same persisted costs.
    assert m.audit(out)['cost_including_history']==result['cost_including_history']
    with pytest.raises(ValueError,match='halted'):
        m.dispatch(out,transport_factory=lambda *a:pytest.fail('must stay halted'))


def test_pause_is_written_before_audit_exception(prepared,monkeypatch):
    out,manifest,registry=prepared
    def runner(*args,**kwargs):
        raise RuntimeError('fixture execution failure')
    monkeypatch.setattr(m,'audit',lambda *a:(_ for _ in ()).throw(RuntimeError('audit crash')))
    with pytest.raises(RuntimeError,match='audit crash'):
        m.dispatch(out,runner=runner,transport_factory=lambda *a:FixtureTransport())
    assert (out/'halt.json').exists()


def test_full_task_budget_is_reserved_before_claim(prepared,monkeypatch):
    out,manifest,registry=prepared
    costs={'requests':2040,'known_tokens':0,'unknown_requests':0,'unknown_reservation':0}
    monkeypatch.setattr(m,'ledger',lambda *a,**kw:costs)
    result=m.dispatch(out,transport_factory=lambda *a:pytest.fail('over budget'))
    assert result['halted']
    assert not list((registry/'claims').glob('*.json'))


def test_raw_response_only_cost_is_recovered_without_network(prepared):
    out,manifest,registry=prepared
    call=out/'runs/fixture/calls/000-planner'
    save_json(call/'request.json',{'system':'s','prompt':'p','max_tokens':10})
    body={'model':'MiniMax-M3','choices':[{'message':{'content':'{}'}}],
          'usage':{'prompt_tokens':2,'completion_tokens':3}}
    envelope={'body_base64':base64.b64encode(json.dumps(body).encode()).decode(),
              'protocol':'openai','seconds':1,'request_id':'fixture'}
    save_json(call/'raw_response.json',{'envelope':envelope,'envelope_sha256':digest(envelope)})
    a=m.ledger(out,manifest)
    assert a['known_tokens']==323282 and a['unknown_requests']==3 and a['requests']==84
    assert a==m.ledger(out,manifest)


def test_different_registry_is_rejected(prepared,tmp_path):
    out,manifest,registry=prepared
    with pytest.raises(ValueError,match='single family registry'):
        m.prepare(tmp_path/'new',tmp_path/'prefix',tmp_path/'different-registry')


def test_transport_reserves_global_cost_before_send(prepared,monkeypatch):
    out,manifest,registry=prepared
    from types import SimpleNamespace
    monkeypatch.setattr(m.RecoveryTransport,'__init__',lambda self,p,mo,run,timeout:setattr(self,'directory',run))
    monkeypatch.setattr(m.RecoveryTransport,'send',lambda *a:pytest.fail('overbudget request was sent'))
    monkeypatch.setattr(m,'ledger',lambda *a,**kw:{'requests':2048,'known_tokens':0,'unknown_reservation':0})
    t=m.GuardedTransport(out,manifest,out/'runs/fixture')
    with pytest.raises(ValueError,match='global budget'):
        t.send({'step':0,'stage':'planner','system':'s','prompt':'p','max_tokens':16384},lambda e:None)
    assert (out/'runs/fixture/calls/000-planner/not_dispatched.json').exists()
