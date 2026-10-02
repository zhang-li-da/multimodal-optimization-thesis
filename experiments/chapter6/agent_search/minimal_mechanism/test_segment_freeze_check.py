import copy
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from chapter6_demo.v12_2.common import read_json, save_json
from experiments.chapter6.agent_search.minimal_mechanism import segment_freeze_check as gate
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_study import continuation_jobs, PROTOCOL_PATH


def test_archived_halt_cannot_release_unstarted_jobs_or_read_test(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / 'results/phase-b-continuation-segment-20261003/raw-segments.zip'
    with zipfile.ZipFile(source) as archive:
        archive.extractall(tmp_path)
    from chapter6_demo import benchmarks
    monkeypatch.setattr(benchmarks, '_instances_cached',
                        lambda *a, **k: pytest.fail('no benchmark split may be materialized'))
    result = gate.inspect_freeze(tmp_path)
    assert result['ready_for_test_review'] is False
    assert len(result['blockers']) == 97
    assert {row['status'] for row in result['blockers']} == {'not_started'}
    assert result['test_access'] is result['test_gate_released'] is False
    assert not (tmp_path / 'test_gate.json').exists()


@pytest.fixture
def terminal_matrix(tmp_path, monkeypatch):
    jobs = continuation_jobs(read_json(PROTOCOL_PATH))
    save_json(tmp_path / 'manifest.json', {'jobs': jobs, 'manifest_sha256': 'synthetic'})
    rows = []
    for job in jobs:
        save_json(tmp_path / 'runs' / job['job_id'] / 'terminal_status.json',
                  {'status': 'budget_exhausted', 'reason': 'synthetic task reservation failure'})
        rows.append({'job_id': job['job_id'], 'status': 'budget_exhausted',
                     'completed_proposals': 0, 'requests': 0,
                     'archived_cost': {'requests': 0}})
        cp = {'status': 'ready', 'selected_on': 'validation',
              'continuation': {'block': job['data_block']},
              'incumbent': {'id': 0, 'loss': .1}, 'branch': {'id': 1, 'loss': .12},
              'nodes': [{'id': 0, 'code': 'def priority(f): return -f["distance"]',
                         'evaluation': {'valid': True, 'loss': .1}},
                        {'id': 1, 'code': 'def priority(f): return f["regret"]',
                         'evaluation': {'valid': True, 'loss': .12}}]}
        save_json(tmp_path / 'checkpoints' / (job['checkpoint_id'] + '.json'), cp)
    monkeypatch.setattr(gate, 'verify_layers', lambda root: 0)
    def checked(root):
        return {'all_tasks_terminal': True, 'in_flight_requests': 0, 'rows': rows,
                'status_counts': dict(Counter(r['status'] for r in rows)),
                'cost_including_history': {'requests': 16, 'known_tokens': 50000,
                                           'unknown_requests': 0, 'unknown_reservation': 0},
                'terminal_unknown_requests': 0}
    monkeypatch.setattr(gate, 'audit', checked)
    return tmp_path, jobs, rows


def add_inherited_candidate(fixture):
    root, jobs, rows = fixture
    job, row = jobs[0], rows[0]
    save_json(root / 'runs' / job['job_id'] / 'terminal_status.json',
              {'status': 'continuation_complete', 'parent_segment_read_only': True})
    parent = root / 'parent_snapshot'
    save_json(parent / 'manifest.json', {})
    run = parent / 'runs' / job['job_id']
    save_json(run / 'terminal_status.json', {'status': 'continuation_complete'})
    selections = {str(h): {'status': 'frozen_on_validation', 'selected_on': 'validation',
                          'code': 'def priority(f): return -f["distance"]',
                          'validation_loss': .1, 'prefix_proposals': h,
                          'strategy': job['strategy']} for h in (4, 8)}
    save_json(run / 'selection_candidates.json', selections)
    save_json(run / 'checkpoint.json', {'synthetic': True})
    save_json(run / 'search_result.json', {'synthetic': True})
    row.update(status='continuation_complete', completed_proposals=8, requests=16)
    return run, selections


def test_full_matrix_preserves_missing_and_resolves_original_candidate_layer(terminal_matrix):
    root, jobs, rows = terminal_matrix
    run, _ = add_inherited_candidate(terminal_matrix)
    result = gate.inspect_freeze(root)
    assert result['ready_for_test_review'] is True
    assert result['test_access'] is result['test_gate_released'] is False
    assert result['candidate_count'] == 34  # two horizons and 32 starting references
    assert len(result['missing_horizons']) == 254
    assert all(not m['counted_as_zero_benefit'] for m in result['missing_horizons'])
    selections = [c for c in result['candidates'] if c['kind'] == 'continuation_prefix']
    assert {c['horizon'] for c in selections} == {4, 8}
    assert all(c['source_path'].startswith('parent_snapshot/runs/') for c in selections)
    assert result['preflight_sha256'] == gate.digest({k: v for k, v in result.items() if k != 'preflight_sha256'})


@pytest.mark.parametrize('change', ['drop', 'duplicate', 'identity'])
def test_matrix_cannot_remove_or_relabel_outcomes(terminal_matrix, change):
    root, jobs, _ = terminal_matrix
    manifest = read_json(root / 'manifest.json')
    if change == 'drop':
        manifest['jobs'].pop()
    elif change == 'duplicate':
        manifest['jobs'][-1] = copy.deepcopy(manifest['jobs'][0])
    else:
        manifest['jobs'][0]['job_id'] = 'replacement'
    save_json(root / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='matrix'):
        gate.inspect_freeze(root)


def test_terminal_sent_unknown_does_not_confuse_inflight_state(terminal_matrix):
    root, jobs, rows = terminal_matrix
    run = root / 'runs' / jobs[0]['job_id']
    (run / 'terminal_status.json').unlink()
    save_json(run / 'config.json', {})
    save_json(run / 'calls/000-planner/state.json', {'status': 'sent_unknown'})
    result = gate.inspect_freeze(root)
    assert result['blockers'] == [{'job_id': jobs[0]['job_id'], 'status': 'running'}]
    save_json(run / 'terminal_status.json', {'status': 'sent_unknown', 'reason': 'transport_timeout'})
    rows[0].update(status='sent_unknown', requests=1)
    assert gate.inspect_freeze(root)['ready_for_test_review']


@pytest.mark.parametrize('corruption, message', [
    ('future', 'completed proposal horizon'), ('missing', 'missing its frozen candidate'),
    ('source', 'validation-only'), ('strategy', 'horizon or strategy'),
    ('binding', 'terminal result binding'), ('reason', 'available checkpoint'),
])
def test_freeze_rejects_untraceable_or_inconsistent_candidates(terminal_matrix, corruption, message):
    root, jobs, rows = terminal_matrix
    run, selections = add_inherited_candidate(terminal_matrix)
    if corruption == 'future':
        rows[0]['completed_proposals'] = 3
    elif corruption == 'missing':
        selections['8'] = {'status': 'missing_prefix'}
    elif corruption == 'source':
        selections['4']['selected_on'] = 'test'
    elif corruption == 'strategy':
        selections['4']['strategy'] = 'other'
    elif corruption == 'binding':
        (run / 'search_result.json').unlink()
    elif corruption == 'reason':
        save_json(run / 'terminal_status.json', {'status': 'preparation_incomplete'})
    save_json(run / 'selection_candidates.json', selections)
    with pytest.raises(ValueError, match=message):
        gate.inspect_freeze(root)
