from pathlib import Path
import pytest
from chapter6_demo.v12_2.common import read_json, save_json, file_sha
from experiments.chapter6.agent_search.minimal_mechanism import continuation_resume as base
from experiments.chapter6.agent_search.minimal_mechanism import continuation_segment as segment
from experiments.chapter6.agent_search.minimal_mechanism.test_continuation_resume import prepared


@pytest.fixture
def halted(prepared, monkeypatch):
    parent, manifest, registry = prepared
    monkeypatch.setattr(segment, 'git', base.git)
    ready = [j for j in manifest['jobs'] if j['eligibility'] == 'ready']
    for i, job in enumerate(ready[:2]):
        run = parent / 'runs' / job['job_id']
        save_json(registry / 'claims' / (job['job_id'] + '.json'), {'job_id': job['job_id']})
        save_json(run / 'calls/000-planner/request.json', {'system': 's', 'prompt': 'p', 'max_tokens': 100})
        save_json(run / 'calls/000-planner/state.json', {'status': 'response_persisted' if i == 0 else 'sent_unknown'})
        if i == 0:
            save_json(run / 'calls/000-planner/response.json', {'input_tokens': 2, 'output_tokens': 3, 'usage_complete': True})
        save_json(run / 'terminal_status.json', {'status': 'budget_exhausted' if i == 0 else 'infrastructure_incomplete'})
    base.halt(parent, ready[1]['job_id'], 'fixture timeout')
    return parent, manifest, registry, ready


def test_segment_preserves_parent_unknown_cost_order_and_skips_attempts(halted, tmp_path):
    parent, pm, registry, ready = halted
    hashes = {p.relative_to(parent).as_posix(): file_sha(p) for p in parent.rglob('*') if p.is_file()}
    costs = base.ledger(parent, pm)
    out = tmp_path / 'segment'
    m = segment.prepare(parent, out, registry)
    assert m['limits']['new_jobs'] == 99
    assert base.ledger(out, m) == costs
    assert costs['unknown_requests'] == 4
    assert [j['job_id'] for j in m['jobs']] == [j['job_id'] for j in pm['jobs']]
    assert {p.relative_to(parent).as_posix(): file_sha(p) for p in parent.rglob('*') if p.is_file()} == hashes
    assert base.verify(out)['manifest_sha256'] == m['manifest_sha256']
    seen = []
    def runner(job, *a, **kw):
        seen.append(job['job_id'])
        raise RuntimeError('fixture stop, no network')
    result = base.dispatch(out, runner=runner, transport_factory=lambda *a: None)
    assert seen == [ready[2]['job_id']]
    assert result['halted']
    assert base.ledger(out, m) == costs


@pytest.mark.parametrize('evidence', ['claim', 'config', 'request'])
def test_orphan_attempt_is_not_released(halted, tmp_path, evidence):
    parent, pm, registry, ready = halted
    job = ready[2]['job_id']
    p = (registry / 'claims' / (job + '.json') if evidence == 'claim' else
         parent / 'runs' / job / ('config.json' if evidence == 'config' else 'calls/000-planner/request.json'))
    save_json(p, {'evidence': True})
    with pytest.raises(ValueError, match='unresolved prior attempt'):
        segment.prepare(parent, tmp_path / 'segment', registry)
    assert not (tmp_path / 'segment').exists()


def test_parent_snapshot_tampering_fails_before_live_call(halted, tmp_path):
    parent, pm, registry, ready = halted
    out = tmp_path / 'segment'
    segment.prepare(parent, out, registry)
    save_json(out / 'parent_snapshot/halt.json', {'changed': True})
    with pytest.raises(ValueError, match='parent snapshot evidence changed'):
        base.dispatch(out, transport_factory=lambda *a: pytest.fail('network forbidden'))


def test_prelaunch_binding_additive_and_cannot_be_reissued(halted, tmp_path):
    parent, pm, registry, ready = halted
    out = tmp_path / 'segment'
    segment.prepare(parent, out, registry)
    before = file_sha(out / 'manifest.json')
    binding = segment.bind_prelaunch(out)
    assert binding['zero_new_calls_at_binding']
    assert file_sha(out / 'manifest.json') == before
    assert base.verify(out)['execution_binding_sha256'] == binding['binding_sha256']
    with pytest.raises(ValueError, match='already frozen'):
        segment.bind_prelaunch(out)


def test_binding_refuses_any_started_task(halted, tmp_path):
    parent, pm, registry, ready = halted
    out = tmp_path / 'segment'
    segment.prepare(parent, out, registry)
    save_json(registry / 'claims' / (ready[2]['job_id'] + '.json'), {'claimed': True})
    with pytest.raises(ValueError, match='after a task started'):
        segment.bind_prelaunch(out)
