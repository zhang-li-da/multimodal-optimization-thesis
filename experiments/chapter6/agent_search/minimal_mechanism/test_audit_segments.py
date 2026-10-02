import zipfile
from pathlib import Path

import pytest
from chapter6_demo.v12_2.common import save_json
from experiments.chapter6.agent_search.minimal_mechanism.audit_segments import audit, evidence_run, verify_layers, report_zh, archive


@pytest.fixture
def archived(tmp_path):
    archive = Path(__file__).parents[1] / 'results/phase-b-continuation-segment-20261003/raw-segments.zip'
    with zipfile.ZipFile(archive) as z:
        z.extractall(tmp_path)
    return tmp_path


def test_real_archive_replays_and_keeps_missing_pairs_and_costs(archived):
    result = audit(archived)
    assert result['replayed_decisions'] == 28
    assert result['status_counts']['not_started'] == 97
    assert result['pairs'] == []
    assert result['cost_including_history'] == {
        'requests': 139, 'known_tokens': 557801,
        'unknown_requests': 5, 'unknown_reservation': 102422}
    assert result['in_flight_requests'] == 0
    assert result['terminal_unknown_requests'] == 5
    assert result['test_access'] is False
    historical = next(r for r in result['rows'] if r['job_id'] == 'eg-b64-step24-r0')
    assert historical['total_task_cost']['requests'] == 41
    assert historical['requests'] == 0
    complete = [r for r in result['rows'] if r['status'] == 'continuation_complete']
    assert all(r['integer_key_state_hash_matches'] for r in complete)
    assert any(not r['serialized_state_hash_matches'] for r in complete)


def test_terminal_lookup_follows_all_parent_layers(tmp_path):
    for layer in [tmp_path, tmp_path / 'parent_snapshot', tmp_path / 'parent_snapshot/parent_snapshot']:
        save_json(layer / 'manifest.json', {})
        save_json(layer / 'runs/job/terminal_status.json', {'status': 'continuation_complete',
                  'parent_segment_read_only': layer != tmp_path / 'parent_snapshot/parent_snapshot'})
    layer, run = evidence_run(tmp_path, 'job')
    assert layer == tmp_path / 'parent_snapshot/parent_snapshot'
    assert run == layer / 'runs/job'


def test_archived_audit_uses_snapshot_not_live_registry(archived):
    # The original global registry may have advanced since this archive.
    assert verify_layers(archived) == 48


def test_parent_added_file_invalidates_archive(archived):
    save_json(archived / 'parent_snapshot/unrecorded.json', {'extra': True})
    with pytest.raises(AssertionError):
        verify_layers(archived)


def test_archive_cannot_claim_live_dispatch_is_complete(archived, tmp_path, monkeypatch):
    import experiments.chapter6.agent_search.minimal_mechanism.audit_segments as module
    monkeypatch.setattr(module, 'audit', lambda root: {'halted': False, 'all_tasks_terminal': False})
    output = tmp_path.with_name(tmp_path.name + '-publication')
    with pytest.raises(ValueError, match='actively dispatching'):
        archive(archived, output)
    assert not output.exists()


def test_report_distinguishes_partial_run_from_effectiveness(archived):
    report = report_zh(audit(archived))
    assert '完成 0 条完整轨迹' in report
    assert '14 次请求' in report
    assert '73,202 已知 token' in report
    assert '不能证明 B 优于 I' in report
