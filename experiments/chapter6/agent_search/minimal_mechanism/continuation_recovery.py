"""Run the executable I/B/E0/EG continuations from the frozen prefix recovery."""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import time
from pathlib import Path

from chapter6_demo.v12_2.common import digest, file_sha, git, read_json, save_json, utcnow
from ..component_validation.service import GlobalPauseGate
from .phase_b_runner import run_phase_b
from .phase_b_study import continuation_jobs
from .recovery_transport import RecoveryTransport

PREFIX = Path('C:/Users/67473/Desktop/5/phase_b_recovery_20261002')
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]


def write(path, value):
    save_json(path, value, immutable=True)


def prepare(out: Path):
    if out.exists():
        raise ValueError('continuation recovery output must be new')
    prefix_manifest = read_json(PREFIX / 'manifest.json')
    audit = read_json(PREFIX / 'PREFIX_AUDIT.json')
    checkpoints = {}
    for row in audit['checkpoints']:
        path = PREFIX / row['path']
        checkpoints[row['id']] = read_json(path)
    # Materialize only search snapshots and frozen checkpoints, never Test.
    out.mkdir(parents=True)
    shutil.copytree(PREFIX / 'data', out / 'data')
    for record in prefix_manifest.get('data', []):
        source = PREFIX / record['path']
        copied = out / record['path']
        if file_sha(source) != record['sha256'] or file_sha(copied) != record['sha256']:
            raise ValueError(f"search snapshot hash mismatch: {record['path']}")
    for checkpoint_id, checkpoint in checkpoints.items():
        write(out / 'checkpoints' / f'{checkpoint_id}.json', checkpoint)
    protocol = read_json(HERE / 'protocol.b.json')
    planned = continuation_jobs(protocol)
    executable = []
    for job in planned:
        cp = checkpoints[job['checkpoint_id']]
        job['eligibility'] = ('executable' if cp.get('status') == 'ready' else 'preparation_incomplete')
        if job['eligibility'] == 'executable':
            executable.append(job)
    value = {
        'schema': 'chapter6-continuation-recovery-v1',
        'created_utc': utcnow(),
        'source_commit': git('rev-parse', 'HEAD'),
        'prefix_manifest_sha256': prefix_manifest['manifest_sha256'],
        'prefix_audit_sha256': digest(audit),
        'protocol_sha256': file_sha(HERE / 'protocol.b.json'),
        'method_revision': file_sha(HERE / 'CONTINUATION_METHOD_ZH.md'),
        'model': {'provider': 'minimax-cn-coding-plan', 'requested_model': 'MiniMax-M3',
                  'transport': 'local OpenCode configuration via direct HTTP',
                  'concurrency': 1, 'retry_policy': 'none', 'timeout_seconds': 600,
                  'protocol_timeout_seconds': protocol['generation']['timeout_seconds'],
                  'continuation_timeout_override': True,
                  'temperature': protocol['generation']['temperature'],
                  'planner_max_tokens': protocol['generation']['planner_max_tokens'],
                  'coder_max_tokens': protocol['generation']['coder_max_tokens']},
        'limits': {'planned_jobs': len(planned), 'executable_jobs': len(executable),
                   'proposals_per_job': 8, 'requests_per_job': 16,
                   'token_budget_per_job': 100000, 'wall_seconds_per_job': 900,
                   'max_requests': 2048, 'max_tokens': 12800000},
        'jobs': planned,
        'test_access': False,
        'continuation_authorized': True,
        'authorization_basis': 'Latest user instruction requests improvement based on prefix results followed by complete experiments; prior public-prefix exclusion is superseded for this continuation. Test remains globally sealed until continuation terminal and candidate-freeze audit.',
        'no_resampling': True,
        'no_test_execution': True,
    }
    value['manifest_sha256'] = digest(value)
    write(out / 'manifest.json', value)
    return value


def verify(out):
    manifest = read_json(out / 'manifest.json')
    if digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}) != manifest['manifest_sha256']:
        raise ValueError('continuation manifest digest mismatch')
    if manifest['source_commit'] != git('rev-parse', 'HEAD') or git('status', '--porcelain'):
        raise ValueError('source changed or worktree dirty')
    if manifest['continuation_authorized'] is not True:
        raise ValueError('continuation authorization missing')
    return manifest


def statuses(out, manifest):
    result = {}
    for job in manifest['jobs']:
        path = out / 'runs' / job['job_id'] / 'terminal_status.json'
        result[job['job_id']] = read_json(path).get('status', 'missing') if path.exists() else 'not_started'
    return result


def audit(out, manifest):
    def run_usage(run):
        calls_dir = run / 'calls'
        requests = known_tokens = unknown_requests = 0
        if calls_dir.exists():
            for call in sorted(p for p in calls_dir.iterdir() if p.is_dir()):
                request = call / 'request.json'
                if not request.exists():
                    continue
                requests += 1
                response = call / 'response.json'
                state = read_json(call / 'state.json') if (call / 'state.json').exists() else {}
                if not response.exists() or state.get('status') in {'sent_unknown', 'indeterminate'}:
                    unknown_requests += 1
                    continue
                try:
                    envelope = read_json(response)
                    body = json.loads(base64.b64decode(envelope['body_base64']).decode('utf-8'))
                    usage = body.get('usage', {}) if isinstance(body, dict) else {}
                    if not all(key in usage for key in ('prompt_tokens', 'completion_tokens')):
                        unknown_requests += 1
                        continue
                    known_tokens += int(usage.get('prompt_tokens', 0) or 0)
                    known_tokens += int(usage.get('completion_tokens', 0) or 0)
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    unknown_requests += 1
        return {'requests': requests, 'known_tokens': known_tokens,
                'unknown_requests': unknown_requests,
                'usage_complete': unknown_requests == 0}

    rows = []
    for job in manifest['jobs']:
        run = out / 'runs' / job['job_id']
        result_path = run / 'search_result.json'
        terminal_path = run / 'terminal_status.json'
        result = read_json(result_path) if result_path.exists() else {}
        terminal = read_json(terminal_path) if terminal_path.exists() else {}
        usage = result.get('usage', terminal.get('usage', {}))
        scanned = run_usage(run)
        if scanned['requests']:
            usage = {**usage, 'call_attempts': scanned['requests'],
                     'known_tokens': scanned['known_tokens'],
                     'unknown_requests': scanned['unknown_requests'],
                     'usage_complete': scanned['usage_complete']}
        rows.append({'job_id': job['job_id'], 'strategy': job['strategy'],
                     'checkpoint_id': job['checkpoint_id'], 'eligibility': job.get('eligibility'),
                     'status': result.get('status', terminal.get('status', 'not_started')),
                     'completed_proposals': result.get('summary', {}).get('completed_proposals', 0),
                     'known_tokens': usage.get('known_tokens', 0),
                     'requests': usage.get('call_attempts', 0),
                     'usage_complete': usage.get('usage_complete', False),
                     'unknown_requests': usage.get('unknown_requests', 0),
                     'wall_seconds': result.get('summary', {}).get('wall_seconds'),
                     'test_access': False})
    value = {'schema': 'chapter6-continuation-audit-v1', 'manifest_sha256': manifest['manifest_sha256'],
             'created_utc': utcnow(), 'rows': rows, 'statuses': statuses(out, manifest),
             'test_access': False,
             'executable_jobs': manifest['limits']['executable_jobs'],
             'terminal_jobs': sum(row['status'] in {'continuation_complete', 'infrastructure_incomplete',
                                                   'budget_exhausted', 'branch_unavailable',
                                                   'preparation_incomplete', 'sent_unknown', 'provider_failed'}
                               for row in rows),
             'known_tokens': sum(row['known_tokens'] for row in rows),
             'requests': sum(row['requests'] for row in rows),
             'completed_proposals': sum(row['completed_proposals'] for row in rows),
             'unknown_requests': sum(row['unknown_requests'] for row in rows),
             'all_tasks_accounted': all(row['status'] in TERMINAL and row['status'] != 'not_started'
                                        for row in rows)}
    write(out / 'CONTINUATION_AUDIT.json', value)
    return value


def dispatch(out):
    manifest = verify(out)
    gate = GlobalPauseGate(2)
    transport_failures = 0
    last_dispatch = 0.0
    for job in manifest['jobs']:
        run = out / 'runs' / job['job_id']
        if (run / 'terminal_status.json').exists():
            continue
        run.mkdir(parents=True, exist_ok=True)
        if job['eligibility'] != 'executable':
            write(run / 'terminal_status.json', {'status': 'preparation_incomplete',
                                                 'reason': 'checkpoint_not_ready', 'missing_outcome': True,
                                                 'test_access': False})
            continue
        wait = 1.0 - (time.monotonic() - last_dispatch)
        if wait > 0:
            time.sleep(wait)
        checkpoint = read_json(out / 'checkpoints' / f"{job['checkpoint_id']}.json")
        snapshot = read_json(out / 'data' / next(p.name for p in (out / 'data').glob('*.json')
                                                if f"b{job['data_block']}" in p.name))
        parameters = {'temperature': manifest['model']['temperature'],
                      'planner_max_tokens': manifest['model']['planner_max_tokens'],
                      'coder_max_tokens': manifest['model']['coder_max_tokens'],
                      'timeout_seconds': manifest['model']['timeout_seconds'],
                      'token_budget': manifest['limits']['token_budget_per_job'],
                      'request_limit': manifest['limits']['requests_per_job'],
                      'wall_limit_seconds': manifest['limits']['wall_seconds_per_job']}
        transport = RecoveryTransport(job['provider'], job['model'], run, timeout=600)
        try:
            result = run_phase_b(job, checkpoint, snapshot, run,
                                 {'continuation_manifest_sha256': manifest['manifest_sha256'],
                                  'checkpoint_sha256': file_sha(out / 'checkpoints' / f"{job['checkpoint_id']}.json")},
                                 parameters, transport, mode='live')
            status = result.get('status', 'infrastructure_incomplete')
        except Exception as exc:
            status = 'infrastructure_incomplete'
            write(run / 'exception.json', {'error_type': type(exc).__name__,
                                           'cause_type': type(exc.__cause__).__name__ if exc.__cause__ else None,
                                           'utc': utcnow(), 'no_automatic_retry': True})
        write(run / 'terminal_status.json', {'status': status, 'utc': utcnow(),
                                             'diagnostics': transport.last_diagnostics,
                                             'test_access': False})
        last_dispatch = time.monotonic()
        pause = gate.observe(transport.last_diagnostics)
        category = (transport.last_diagnostics or {}).get('error_category')
        run_audit = audit(out, manifest)
        unknown_usage = any(row['job_id'] == job['job_id'] and row['unknown_requests']
                            for row in run_audit['rows'])
        transport_failures = transport_failures + 1 if category in {
            'transport_timeout', 'transport_error', 'provider_server'} else 0
        immediate_server_pause = category in {'provider_server', 'quota_exhausted',
                                              'connection_limit', 'authentication'}
        if pause['pause'] or immediate_server_pause or unknown_usage or transport_failures >= 2:
            write(out / 'halt.json', {'status': 'paused', 'after_job': job['job_id'],
                                     'reason': {**pause, 'category': category,
                                                'unknown_usage': unknown_usage},
                                     'transport_failures': transport_failures,
                                     'no_automatic_retry': True})
            break
    for job in manifest['jobs']:
        run = out / 'runs' / job['job_id']
        if not (run / 'terminal_status.json').exists():
            run.mkdir(parents=True, exist_ok=True)
            write(run / 'terminal_status.json', {'status': 'not_started', 'reason': 'global_pause',
                                                 'missing_outcome': True, 'test_access': False})
    return audit(out, manifest)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'audit'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'prepare': prepare, 'run': dispatch, 'audit': audit}[args.action](args.output)
    print(json.dumps({k: v for k, v in result.items() if k in {'manifest_sha256', 'known_tokens', 'requests', 'completed_proposals', 'statuses'}}))


if __name__ == '__main__':
    main()
