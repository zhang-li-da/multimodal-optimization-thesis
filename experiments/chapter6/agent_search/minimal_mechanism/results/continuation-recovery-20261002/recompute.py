"""Recompute descriptive execution facts from published ZIPs; no API or Test calls."""
import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def write(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    rows, archives, members, manifests = [], [], [], []
    expected_archives = {r['path']: r for r in json.loads((HERE / 'ARCHIVE_SHA256.json').read_text(encoding='utf-8'))['archives']}
    for path in sorted(HERE.glob('*.zip')):
        archive_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert archive_sha == expected_archives[path.name]['sha256']
        with zipfile.ZipFile(path) as z:
            assert z.testzip() is None
            names = [i.filename for i in z.infolist() if not i.is_dir()]
            assert len(names) == len(set(names))
            def read(name):
                return json.loads(z.read(name).decode('utf-8'))
            manifest = read('manifest.json')
            manifests.append(manifest)
            batch = path.stem.rsplit('_', 1)[1]
            batch_rows = []
            for name in sorted(names):
                raw = z.read(name)
                members.append({'archive': path.name, 'path': name, 'bytes': len(raw),
                                'sha256': hashlib.sha256(raw).hexdigest()})
                if not name.endswith('/search_result.json'):
                    continue
                result = read(name)
                run = name.rsplit('/', 1)[0]
                checkpoint = read(run + '/checkpoint.json')
                records = checkpoint['records']
                calls = [n for n in names if n.startswith(run + '/calls/') and n.endswith('/request.json')]
                known_tokens = unknown = responses = 0
                returned_models = set()
                for request in calls:
                    response_path = request.rsplit('/', 1)[0] + '/response.json'
                    if response_path not in names:
                        unknown += 1
                        continue
                    response = read(response_path)
                    responses += 1
                    returned_models.add(response.get('returned_model'))
                    values = [response.get('input_tokens'), response.get('output_tokens')]
                    known_tokens += sum(v for v in values if isinstance(v, int))
                    unknown += not response.get('usage_complete', False)
                summary = result['summary']
                assert (len(records), len(calls), known_tokens) == (summary['completed_proposals'], summary['request_count'], summary['known_tokens'])
                valid = [r['node']['evaluation']['loss'] for r in records if r['node']['evaluation']['valid']]
                start = checkpoint['state']['start_incumbent_loss']
                frozen = {h: {k: v for k, v in s.items() if k != 'code'} for h, s in result['selections'].items()}
                for horizon, selection in frozen.items():
                    if selection['status'] != 'frozen_on_validation':
                        continue
                    observed = [r['node']['evaluation']['loss'] for r in records[:int(horizon)] if r['node']['evaluation']['valid']]
                    assert selection['validation_loss'] == min([start] + observed)
                row = {'batch': batch, 'job_id': result['config']['job_id'],
                       'strategy': summary['strategy'], 'status': result['status'],
                       'completed_proposals': len(records), 'valid_programs': len(valid),
                       'invalid_programs': len(records) - len(valid),
                       'truncations': sum('truncat' in json.dumps(r['node'].get('proposal_failure')) for r in records),
                       'requests': len(calls), 'responses': responses, 'known_tokens': known_tokens,
                       'unknown_requests': unknown, 'returned_models': sorted(returned_models),
                       'start_validation_loss': start, 'best_new_validation_loss': min(valid) if valid else None,
                       'final_validation_loss': min([start] + valid),
                       'new_candidates_beating_incumbent': sum(v < start for v in valid),
                       'project_progress_events': sum(r['event']['project_progress'] for r in records),
                       'wall_seconds': summary['wall_seconds'], 'selections': frozen}
                rows.append(row)
                batch_rows.append(row)
            archives.append({'path': path.name, 'sha256': archive_sha, 'file_members': len(names),
                             'execution_attempts': len(batch_rows)})
    planned = {j['job_id']: j for j in manifests[-1]['jobs']}
    assert all({j['job_id'] for j in m['jobs']} == set(planned) for m in manifests)
    attempted = {r['job_id'] for r in rows}
    complete = {r['job_id'] for r in rows if r['status'] == 'continuation_complete'}
    counts = Counter(r['job_id'] for r in rows)
    summary = {
        'schema': 'chapter6-continuation-execution-summary-v2',
        'status': 'paused_infrastructure_incomplete',
        'supersedes': '91c3312 summary mixed execution attempts with unique task IDs',
        'planned_jobs': len(planned),
        'executable_jobs': sum(j['eligibility'] == 'executable' for j in planned.values()),
        'execution_attempts': len(rows),
        'completed_execution_attempts': sum(r['status'] == 'continuation_complete' for r in rows),
        'infrastructure_incomplete_execution_attempts': sum(r['status'] == 'infrastructure_incomplete' for r in rows),
        'unique_attempted_jobs': len(attempted),
        'unique_jobs_with_any_complete_attempt': len(complete),
        'unique_jobs_without_complete_attempt': len(attempted - complete),
        'unique_unattempted_jobs': len(set(planned) - attempted),
        'unattempted_executable_jobs': sum(j['eligibility'] == 'executable' and key not in attempted for key, j in planned.items()),
        'unavailable_checkpoint_jobs': sum(j['eligibility'] == 'preparation_incomplete' for j in planned.values()),
        'repeated_job_ids': {key: count for key, count in counts.items() if count > 1},
        'action_execution_attempts': {a: sum(r['strategy'] == a for r in rows) for a in ['I', 'B', 'E0', 'EG']},
        'test_access': False, 'test_executed': False,
        'component_effectiveness_claim': 'not_estimable: repeated jobs and no same-checkpoint I/B/E0/EG comparisons',
        'cost_interpretation': 'all execution attempts counted; unknown usage is not zero',
        'outcome_interpretation': 'descriptive only; repeated attempts must not replace earlier failures or become independent planned repetitions',
        'archives': archives, 'attempts': rows,
    }
    for key in ['completed_proposals', 'requests', 'responses', 'known_tokens', 'unknown_requests',
                'valid_programs', 'invalid_programs', 'truncations', 'project_progress_events', 'new_candidates_beating_incumbent']:
        summary[key] = sum(r[key] for r in rows)
    write('EXECUTION_SUMMARY.json', summary)
    write('ARCHIVE_MEMBERS_SHA256.json', {'schema': 'archive-member-index-v1', 'members': members})
    print(json.dumps({k: v for k, v in summary.items() if k not in {'archives', 'attempts'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
