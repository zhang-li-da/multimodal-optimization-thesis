"""Read-only candidate-freeze preflight for nested continuation segments.

This module never materializes Test, evaluates programs, or releases a Test gate.
It checks the entire original matrix, including failed and unavailable tasks,
and resolves frozen candidates from their original immutable evidence layers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json
from .audit_segments import audit, evidence_run, verify_layers


TERMINAL = {
    'continuation_complete', 'infrastructure_incomplete', 'budget_exhausted',
    'branch_unavailable', 'preparation_incomplete', 'sent_unknown',
    'provider_failed', 'historical_attempt_preserved',
}


def _matrix(manifest):
    jobs = manifest['jobs']
    expected = {(block, step, strategy, repetition)
                for block in range(60, 68) for step in (8, 24)
                for strategy in ('I', 'B', 'E0', 'EG') for repetition in (0, 1)}
    actual = {(j['data_block'], j['public_prefix_steps'], j['strategy'], j['repetition'])
              for j in jobs}
    if len(jobs) != 128 or len({j['job_id'] for j in jobs}) != 128 or actual != expected:
        raise ValueError('the full original 128-task matrix must be retained')
    for j in jobs:
        checkpoint = f"b{j['data_block']}-step{j['public_prefix_steps']:02d}"
        job_id = f"{j['strategy'].lower()}-{checkpoint}-r{j['repetition']}"
        if j['checkpoint_id'] != checkpoint or j['job_id'] != job_id or j['steps'] != 8:
            raise ValueError('task identity differs from the frozen matrix')
    return jobs


def inspect_freeze(root):
    """Return blockers or a complete search-only candidate/evidence manifest."""
    root = Path(root).resolve()
    manifest = read_json(root / 'manifest.json')
    jobs = _matrix(manifest)
    verify_layers(root)
    resolved, blocked = {}, []
    for job in jobs:
        layer, run = evidence_run(root, job['job_id'])
        marker = run / 'terminal_status.json'
        term = read_json(marker) if marker.exists() else {}
        status = term.get('status', 'running' if (run / 'config.json').exists() else 'not_started')
        resolved[job['job_id']] = (layer, run, term)
        if status not in TERMINAL:
            blocked.append({'job_id': job['job_id'], 'status': status})
    common = {'schema': 'chapter6-segment-candidate-freeze-preflight-v1',
              'study_manifest_sha256': manifest['manifest_sha256'],
              'planned_jobs': 128, 'test_access': False, 'new_model_calls': 0,
              'test_gate_released': False}
    if blocked:
        # A halted dispatcher does not make its unstarted tasks terminal.
        return {**common, 'ready_for_test_review': False, 'blockers': blocked,
                'reason': 'all original tasks must have explicit terminal outcomes'}

    checked = audit(root)
    if not checked['all_tasks_terminal'] or checked['in_flight_requests']:
        raise ValueError('an unfinished task or in-flight request remains')
    rows = {row['job_id']: row for row in checked['rows']}
    candidates, missing, evidence = [], [], {}

    def bind(path):
        path = path.resolve()
        if not path.is_relative_to(root):
            raise ValueError('candidate evidence escaped the study')
        name = path.relative_to(root).as_posix()
        evidence[name] = {'path': name, 'sha256': file_sha(path)}
        return name

    def candidate(value, path, **identity):
        code, loss = value.get('code'), value.get('validation_loss')
        if (value.get('selected_on') != 'validation' or not isinstance(code, str)
                or not code.strip() or isinstance(loss, bool)
                or not isinstance(loss, (int, float)) or not math.isfinite(loss)):
            raise ValueError('candidate is not a finite validation-only selection')
        candidates.append({**identity, 'source_path': bind(path),
                           'source_sha256': file_sha(path),
                           'code_sha256': hashlib.sha256(code.encode('utf-8')).hexdigest(),
                           'code': code, 'validation_loss': loss, 'selected_on': 'validation'})

    for job in jobs:
        job_id = job['job_id']
        layer, run, term = resolved[job_id]
        row = rows[job_id]
        status = term['status']
        bind(run / 'terminal_status.json')
        cp = read_json(root / 'checkpoints' / (job['checkpoint_id'] + '.json'))
        if status == 'preparation_incomplete' and cp['status'] == 'ready':
            raise ValueError('preparation-incomplete task has an available checkpoint')
        if status == 'branch_unavailable' and (job['strategy'] != 'B' or cp.get('branch')):
            raise ValueError('branch-unavailable reason contradicts the checkpoint')
        if status == 'historical_attempt_preserved' and not row['archived_cost']['requests']:
            raise ValueError('historical terminal task has no archived attempt costs')
        if status in {'infrastructure_incomplete', 'budget_exhausted', 'sent_unknown', 'provider_failed'}:
            if not (term.get('reason') or term.get('diagnostics') or term.get('error_type')
                    or row['requests'] or row['archived_cost']['requests']):
                raise ValueError('failed task lacks an auditable missing-outcome reason')
        selection_path = run / 'selection_candidates.json'
        selections = read_json(selection_path) if selection_path.exists() else {}
        if status in {'preparation_incomplete', 'branch_unavailable', 'historical_attempt_preserved'} and selections:
            raise ValueError('unavailable/preserved task unexpectedly supplies selected candidates')
        if selections:
            if set(selections) != {'4', '8'} or not (run / 'search_result.json').exists():
                raise ValueError('candidate selections lack a terminal result binding')
            if not (run / 'checkpoint.json').exists():
                raise ValueError('candidate selections lack their completed proposal trace')
            bind(selection_path)
            bind(run / 'search_result.json')
            bind(run / 'checkpoint.json')
        for horizon in (4, 8):
            selection = selections.get(str(horizon), {})
            if selection.get('status') == 'frozen_on_validation':
                if row['completed_proposals'] < horizon:
                    raise ValueError('a candidate is frozen beyond the completed proposal horizon')
                if (selection.get('prefix_proposals') != horizon
                        or selection.get('strategy') != job['strategy']):
                    raise ValueError('candidate horizon or strategy differs from its task')
                candidate(selection, selection_path, candidate_id=f'run-{job_id}-p{horizon}',
                          kind='continuation_prefix', job_id=job_id,
                          checkpoint_id=job['checkpoint_id'], block=job['data_block'],
                          strategy=job['strategy'], repetition=job['repetition'], horizon=horizon)
            else:
                if status == 'continuation_complete' or row['completed_proposals'] >= horizon:
                    raise ValueError('a completed horizon is missing its frozen candidate')
                if selection and selection.get('status') != 'missing_prefix':
                    raise ValueError('unknown candidate freeze status')
                missing.append({'job_id': job_id, 'horizon': horizon, 'terminal_status': status,
                                'reason': term.get('reason') or status,
                                'completed_proposals': row['completed_proposals'],
                                'counted_as_zero_benefit': False})

    missing_references = []
    for checkpoint_id in sorted({j['checkpoint_id'] for j in jobs}):
        path = root / 'checkpoints' / (checkpoint_id + '.json')
        cp = read_json(path)
        bind(path)
        for action, field in (('I', 'incumbent'), ('B', 'branch')):
            ref = cp.get(field) if cp['status'] == 'ready' else None
            if ref is None:
                missing_references.append({'checkpoint_id': checkpoint_id, 'action': action,
                                           'reason': 'checkpoint_unavailable' if cp['status'] != 'ready' else 'branch_unavailable'})
                continue
            node = next(n for n in cp['nodes'] if n['id'] == ref['id'])
            if not node['evaluation']['valid'] or node['evaluation']['loss'] != ref['loss']:
                raise ValueError('starting reference differs from frozen validation evidence')
            candidate({'code': node['code'], 'validation_loss': ref['loss'], 'selected_on': cp['selected_on']},
                      path, candidate_id=f'checkpoint-{checkpoint_id}-start-{action}',
                      kind='checkpoint_reference', checkpoint_id=checkpoint_id,
                      block=cp['continuation']['block'], reference_action=action, horizon=0)
    if len(candidates) > 288:
        raise ValueError('candidate count exceeds the predeclared evaluation cap')
    result = {**common, 'ready_for_test_review': True, 'blockers': [],
              'candidates': candidates, 'candidate_count': len(candidates), 'candidate_cap': 288,
              'missing_horizons': missing, 'missing_references': missing_references,
              'evidence_files': sorted(evidence.values(), key=lambda x: x['path']),
              'status_counts': checked['status_counts'],
              'cost_including_history': checked['cost_including_history'],
              'terminal_unknown_requests': checked['terminal_unknown_requests'],
              'interpretation': 'Candidate freeze preflight only; not Test release or method effectiveness.'}
    result['preflight_sha256'] = digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.study.resolve()):
        raise ValueError('preflight output must be outside the immutable study')
    result = inspect_freeze(args.study)
    save_json(args.output, result, immutable=True)
    print(json.dumps({k: result[k] for k in ('ready_for_test_review', 'planned_jobs',
                      'test_access', 'test_gate_released')} | {'blockers': len(result['blockers'])}))


if __name__ == '__main__':
    main()
