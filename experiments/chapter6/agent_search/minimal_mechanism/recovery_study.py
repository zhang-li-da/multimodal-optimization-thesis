"""Versioned recovery of unstarted B prefixes; no replay of unknown calls."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import time

from chapter6_demo.v12_2.common import digest, file_sha, git, read_json, save_json, utcnow
from ..component_validation.service import GlobalPauseGate
from .phase_b_study import _run_prefix_with_budget, _select_prefix_checkpoint, continuation_jobs
from .phase_b_runner import history_summary, PhaseBState, _append_history_prompt
from .recovery_transport import RecoveryTransport
from .freeze_sources import verify_index

ROOT = Path(__file__).resolve().parents[4]
OLD = Path('C:/Users/67473/Desktop/5/phase_b_public_prefix_20260930/frozen')
RERUN = Path('C:/Users/67473/Desktop/5/phase_b_opencode_complete_20261001')
ACCEPTANCE = OLD.parent / 'local-opencode-acceptance'


def write(path, value):
    save_json(path, value, immutable=True)


def reserve(request):
    return len(request['system'].encode()) + len(request['prompt'].encode()) + 512 + request['max_tokens']


def ledger(directory):
    rows = []
    for path in sorted(directory.rglob('request.json')):
        folder = path.parent
        if not (folder / 'state.json').exists():
            continue
        state = read_json(folder / 'state.json')
        if state['status'] == 'prepared':
            continue
        response = read_json(folder / 'response.json') if (folder / 'response.json').exists() else {}
        request = read_json(path)
        known = (response.get('input_tokens') or 0) + (response.get('output_tokens') or 0)
        complete = response.get('usage_complete', False)
        rows.append({'path': folder.relative_to(directory).as_posix(),
                     'status': state['status'], 'known_tokens': known,
                     'usage_complete': complete,
                     'unknown_reservation': 0 if complete else reserve(request),
                     'request_sha256': file_sha(path),
                     'returned_model': response.get('returned_model')})
    return {'requests': len(rows), 'known_tokens': sum(r['known_tokens'] for r in rows),
            'unknown_requests': sum(not r['usage_complete'] for r in rows),
            'unknown_reservation': sum(r['unknown_reservation'] for r in rows), 'calls': rows}


def prepare(out):
    if out.exists():
        raise ValueError('Recovery directory must not exist')
    old = read_json(OLD / 'manifest.json')
    if old['manifest_sha256'] != '29cc55e3443fa84c2156671c9b087585df6c6b224b81ba2d4cafa24421bf0550':
        raise ValueError('unexpected original lineage')
    verify_index()
    if git('status', '--porcelain'):
        raise ValueError('Commit execution source before freezing')
    out.mkdir(parents=True)
    # Original experiments remain untouched. Copies retain every previous cost.
    for name, directory in [('original', OLD), ('superseded_rerun', RERUN), ('service_acceptance', ACCEPTANCE)]:
        shutil.copytree(directory, out / 'provenance' / name)
    evidence = {p.relative_to(out).as_posix(): file_sha(p)
                for p in (out / 'provenance').rglob('*') if p.is_file()}
    prior = ledger(out / 'provenance')
    # The short probe persisted aggregate usage but no DurableCalls directory.
    probe = read_json(ACCEPTANCE / 'probe.json')
    probe_usage = probe.get('usage', {})
    if not probe_usage:
        raise ValueError('short service probe cost unavailable')
    prior['requests'] += 1
    prior['known_tokens'] += probe_usage['input_tokens'] + probe_usage['output_tokens']
    prior['short_probe'] = probe_usage
    jobs = copy.deepcopy(old['prefix_jobs'])
    for job in jobs:
        job['timeout_seconds'] = 600
    records = []
    for rec in old['data']:
        source = OLD / rec['path']
        snapshot = read_json(source)
        if rec['role'] != 'search' or 'test' in snapshot or file_sha(source) != rec['sha256']:
            raise ValueError('invalid frozen search snapshot')
        target = out / rec['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        records.append(rec)
    executed = []
    for job in jobs:
        old_run = OLD / 'prefix_runs' / job['job_id']
        if (old_run / 'search_result.json').exists():
            executed.append(job['job_id'])
    if executed != ['prefix-sp-b65']:
        raise ValueError('Unexpected original execution status; no adaptive lineage selection allowed')
    shutil.copytree(OLD / 'prefix_runs' / 'prefix-sp-b65', out / 'prefix_runs' / 'prefix-sp-b65')
    value = {'schema': 'chapter6-prefix-recovery-v1', 'created_utc': utcnow(),
             'source_commit': git('rev-parse', 'HEAD'), 'source_index': verify_index(),
             'original_manifest_sha256': old['manifest_sha256'],
             'protocol_sha256': file_sha(Path(__file__).with_name('protocol.b.json')),
             'historical_files': evidence, 'prior_cost': prior,
             'prefix_jobs': jobs, 'data': records, 'checkpoints': [8, 24],
             'preserved_terminal_jobs': executed,
             'new_dispatch_order': [j['job_id'] for j in jobs if j['job_id'] not in executed],
             'limits': {'requests': 384, 'tokens': 2000000, 'requests_per_job': 48,
                        'tokens_per_job': 250000, 'proposals_per_job': 24, 'wall_per_job': 3600},
             'model': {'provider': 'minimax-cn-coding-plan', 'model': 'MiniMax-M3',
                       'local_configuration': 'OpenCode', 'transport': 'direct HTTP',
                       'stream': False, 'temperature': .7, 'planner_max_tokens': 16384,
                       'coder_max_tokens': 8192, 'timeout_seconds': 600, 'concurrency': 1},
             'retry_policy': 'none; preserve unknown calls; do not rerun any started historical job',
             'global_pause': 'quota/authentication/connection limit immediately; two consecutive rate or transport failures',
             'test_access': False, 'continuation_authorized': False,
             'authorization_basis': 'User authorizes completing public prefixes with local OpenCode MiniMax-M3; recovery continues only unstarted jobs, charged together with both historical attempts and recorded service checks. Continuations require resolution of the explicit earlier exclusion.',
             'changes': ['180 to 600 second per-request timeout within the same task wall cap',
                         'persist sanitized network diagnostics; no repeated request',
                         'unknown costs remain missing and their input-byte/output-cap reservations are locked'],
             'service_acceptance': {'path': 'provenance/service_acceptance/full-20261002',
                                    'additional_acceptance_calls': 0}}
    value['manifest_sha256'] = digest(value)
    write(out / 'manifest.json', value)
    return value


def verify(out):
    manifest = read_json(out / 'manifest.json')
    if digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'}) != manifest['manifest_sha256']:
        raise ValueError('manifest changed')
    if manifest['source_commit'] != git('rev-parse', 'HEAD') or git('status', '--porcelain'):
        raise ValueError('execution commit changed or dirty')
    if manifest['source_index'] != verify_index():
        raise ValueError('source index changed')
    for rel, expected in manifest['historical_files'].items():
        if file_sha(out / rel) != expected:
            raise ValueError('historical evidence changed')
    for rec in manifest['data']:
        data = read_json(out / rec['path'])
        if 'test' in data or file_sha(out / rec['path']) != rec['sha256']:
            raise ValueError('search-only data violation')
    return manifest


def cumulative_cost(out, manifest):
    prior = manifest['prior_cost']
    ledgers = [ledger(out / 'prefix_runs' / job) for job in manifest['new_dispatch_order']]
    keys = ['requests', 'known_tokens', 'unknown_requests', 'unknown_reservation']
    return {k: prior[k] + sum(rec[k] for rec in ledgers) for k in keys}


def dispatch(out):
    manifest = verify(out)
    if (out / 'halt.json').exists():
        raise ValueError('Global pause active; no automatic resume')
    gate = GlobalPauseGate(2)
    transport_failures = 0
    for job in manifest['prefix_jobs']:
        if job['job_id'] in manifest['preserved_terminal_jobs']:
            continue
        run = out / 'prefix_runs' / job['job_id']
        if (run / 'search_result.json').exists():
            continue
        if (run / 'config.json').exists():
            raise ValueError('Interrupted job must be audited before resuming; no cost reset')
        cost = cumulative_cost(out, manifest)
        if cost['requests'] + 48 > 384 or cost['known_tokens'] + cost['unknown_reservation'] + 250000 > 2000000:
            write(run / 'terminal_status.json', {'status': 'budget_exhausted', 'reason': 'batch reservation'})
            continue
        snapshot = read_json(out / next(r['path'] for r in manifest['data'] if r['block'] == job['data_block']))
        transport = RecoveryTransport(job['provider'], job['model'], run)
        parameters = {k: job[k] for k in ('temperature', 'planner_max_tokens', 'coder_max_tokens',
                      'timeout_seconds', 'token_budget', 'request_limit', 'wall_limit_seconds')}
        print(json.dumps({'event': 'starting', 'job': job['job_id'], 'utc': utcnow(), 'cumulative_cost': cost}), flush=True)
        started = time.perf_counter()
        try:
            result = _run_prefix_with_budget(job, snapshot, run,
                        {'recovery_manifest_sha256': manifest['manifest_sha256']}, parameters, transport)
            status = result['status']
        except Exception as exc:
            status = 'infrastructure_incomplete'
            write(run / 'exception.json', {'error_type': type(exc).__name__,
                        'cause_type': type(exc.__cause__).__name__ if exc.__cause__ else None,
                        'elapsed_seconds': time.perf_counter() - started, 'utc': utcnow()})
        write(run / 'terminal_status.json', {'status': status, 'utc': utcnow(),
                                            'diagnostics': transport.last_diagnostics})
        cost = cumulative_cost(out, manifest)
        save_json(out / 'progress.json', {'last_job': job['job_id'], 'status': status, 'cost': cost, 'utc': utcnow()})
        print(json.dumps({'event': 'terminal', 'job': job['job_id'], 'status': status, 'cost': cost}), flush=True)
        pause = gate.observe(transport.last_diagnostics)
        category = (transport.last_diagnostics or {}).get('error_category')
        transport_failures = transport_failures + 1 if category in {'transport_timeout', 'transport_error'} else 0
        if pause['pause'] or transport_failures >= 2 or (status == 'infrastructure_incomplete' and not category):
            write(out / 'halt.json', {'reason': pause, 'transport_failures': transport_failures,
                                     'after_job': job['job_id'], 'diagnostics': transport.last_diagnostics})
            break
    for job in manifest['prefix_jobs']:
        run = out / 'prefix_runs' / job['job_id']
        if not (run / 'terminal_status.json').exists() and not (run / 'search_result.json').exists():
            write(run / 'terminal_status.json', {'status': 'not_started', 'reason': 'global_pause',
                                               'missing_outcome': True})
    return audit(out, manifest)


def audit(out, manifest=None):
    manifest = manifest or read_json(out / 'manifest.json')
    rows, checkpoints, histories = [], [], []
    for job in manifest['prefix_jobs']:
        run = out / 'prefix_runs' / job['job_id']
        result = read_json(run / 'search_result.json') if (run / 'search_result.json').exists() else {}
        terminal = read_json(run / 'terminal_status.json') if (run / 'terminal_status.json').exists() else {}
        saved = read_json(run / 'checkpoint.json') if (run / 'checkpoint.json').exists() else {}
        records, seeds = saved.get('records', []), saved.get('seeds', [])
        status = result.get('status', terminal.get('status', 'not_started'))
        job_cost = ledger(run)
        for step in (8, 24):
            checkpoint_id = f"b{job['data_block']}-step{step:02d}"
            snapshot_sha = next(r['sha256'] for r in manifest['data'] if r['block'] == job['data_block'])
            if len(records) < step:
                cp = {'checkpoint_id': checkpoint_id, 'block': job['data_block'], 'prefix_step': step,
                      'status': 'preparation_incomplete', 'completed_proposals': len(records)}
            else:
                cp = _select_prefix_checkpoint(job['data_block'], step, seeds + [r['node'] for r in records[:step]], snapshot_sha)
            cp.update(public_prefix_job_id=job['job_id'], public_prefix_status=status)
            path = out / 'checkpoints' / (checkpoint_id + '.json')
            write(path, cp)
            checkpoints.append({'id': checkpoint_id, 'path': path.relative_to(out).as_posix(),
                                'sha256': file_sha(path), 'status': cp['status'],
                                'branch_available': bool(cp.get('branch'))})
            branch = cp.get('branch')
            branch_audit = next((x for x in cp.get('branch_candidate_audit', []) if branch and x['node_id'] == branch['id']), {})
            rows.append({'block': job['data_block'], 'checkpoint': step, 'task_status': status,
                         'completed_proposals': len(records), 'valid_programs': sum(r['node']['evaluation'].get('valid', False) for r in records),
                         'invalid_programs': sum(not r['node']['evaluation'].get('valid', False) for r in records),
                         'truncations': sum(r.get('costs', {}).get('truncations', 0) for r in records),
                         'checkpoint_status': cp['status'], 'incumbent': cp.get('incumbent'), 'branch': branch,
                         'branch_difference': branch_audit,
                         'cost': {k: v for k, v in job_cost.items() if k != 'calls'},
                         'wall_seconds': result.get('summary', {}).get('wall_seconds'), 'checkpoint_sha256': file_sha(path)})
            if cp['status'] == 'ready':
                summary = history_summary(cp['nodes'], cp['incumbent']['loss'])
                decision = PhaseBState(cp, 'EG').choose(0)
                from chapter6_demo.discovery import planner_prompt
                prompt = _append_history_prompt(planner_prompt('tsp', {
                    'target': decision['target'], 'action': decision['action'], 'parent': None,
                    'reference': None, 'evidence': decision['evidence']}, 0), decision)
                write(out / 'eg_audit' / (checkpoint_id + '.json'), {'summary': summary, 'prompt': prompt, 'decision': decision})
                histories.append({'checkpoint': checkpoint_id, 'visible_node_ids': [n['id'] for n in cp['nodes']],
                                  'summary_node_ids': [n['node_id'] for n in summary['recorded_program_summaries']],
                                  'intent_count': sum(bool(n.get('intent')) for n in summary['recorded_program_summaries']),
                                  'hypothesis_count': sum(bool(n.get('strategy_hypothesis')) for n in summary['recorded_program_summaries']),
                                  'failure_count': len(summary['observed_failures']),
                                  'history_evidence': decision['evidence'], 'prompt_sha256': digest(prompt)})
    jobs = continuation_jobs(read_json(Path(__file__).with_name('protocol.b.json')))
    cp_map = {cp['id']: cp for cp in checkpoints}
    for job in jobs:
        cp = cp_map[job['checkpoint_id']]
        job['eligibility'] = ('preparation_incomplete' if cp['status'] != 'ready' else
                              'branch_unavailable' if job['strategy'] == 'B' and not cp['branch_available'] else 'executable')
    executable = sum(j['eligibility'] == 'executable' for j in jobs)
    summary = {'created_utc': utcnow(), 'manifest_sha256': manifest['manifest_sha256'], 'rows': rows,
               'checkpoints': checkpoints, 'histories': histories, 'cost': cumulative_cost(out, manifest),
               'continuation_request': {'authorized': False, 'jobs': jobs, 'executable_jobs': executable,
                                        'max_proposals': executable * 8, 'max_requests': executable * 16,
                                        'max_tokens': executable * 100000},
               'test_access': False, 'no_inference_of_component_effectiveness': True}
    write(out / 'PREFIX_AUDIT.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'audit'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'prepare': prepare, 'run': dispatch, 'audit': audit}[args.action](args.output)
    print(json.dumps({k: v for k, v in result.items() if k in {'manifest_sha256', 'cost', 'test_access'}}), flush=True)


if __name__ == '__main__':
    main()
