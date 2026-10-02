"""One registered recovery of UNSTARTED phase-B tasks; never retries an attempt."""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from collections import Counter
from pathlib import Path

from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.common import digest, file_sha, git, read_json, run_lock, save_json, utcnow
from .freeze_sources import verify_index
from .phase_b_runner import run_phase_b
from .phase_b_study import continuation_jobs, _select_prefix_checkpoint
from .recovery_transport import RecoveryTransport

HERE = Path(__file__).resolve().parent
HISTORY = HERE / 'results/continuation-recovery-20261002'
TERMINAL = {'continuation_complete', 'infrastructure_incomplete', 'budget_exhausted',
            'historical_attempt_preserved', 'preparation_incomplete', 'branch_unavailable'}
PARAMETERS = {'temperature': .7, 'planner_max_tokens': 16384, 'coder_max_tokens': 8192,
              'timeout_seconds': 600, 'token_budget': 100000, 'request_limit': 16,
              'wall_limit_seconds': 900}


def freeze(path, value):
    save_json(path, value, immutable=True)


def reserve(request):
    return len(request['system'].encode('utf-8')) + len(request['prompt'].encode('utf-8')) + request['max_tokens'] + 512


def call_cost(request, response):
    known = sum(v for v in (response.get('input_tokens'), response.get('output_tokens'))
                if type(v) is int and v >= 0)
    complete = response.get('usage_complete') is True
    return {'requests': 1, 'known_tokens': known, 'unknown_requests': int(not complete),
            'unknown_reservation': max(0, reserve(request) - known) if not complete else 0}


def sum_cost(rows):
    return {k: sum(row[k] for row in rows) for k in
            ('requests', 'known_tokens', 'unknown_requests', 'unknown_reservation')}


def family_registry(prefix):
    return prefix.resolve().parent / 'phase_b_continuation_registry'


def historical_attempts():
    attempts, archives = [], []
    for row in read_json(HISTORY / 'ARCHIVE_SHA256.json')['archives']:
        path = HISTORY / row['path']
        if file_sha(path) != row['sha256']:
            raise ValueError('historical archive changed')
        archives.append({'path': row['path'], 'sha256': row['sha256']})
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise ValueError('historical archive damaged')
            def read(name):
                return json.loads(z.read(name).decode('utf-8'))
            names = set(z.namelist())
            # A request/config/claim is evidence of an attempt even without a result.
            runs = {n.split('/')[1] for n in names if n.startswith('runs/') and
                    (n.endswith('/config.json') or n.endswith('/request.json'))}
            for job_id in sorted(runs):
                base = 'runs/' + job_id
                costs = []
                for n in sorted(names):
                    if not n.startswith(base + '/calls/') or not n.endswith('/request.json'):
                        continue
                    request = read(n)
                    response_name = n.rsplit('/', 1)[0] + '/response.json'
                    response = read(response_name) if response_name in names else {}
                    costs.append(call_cost(request, response))
                result_name = base + '/search_result.json'
                result = read(result_name) if result_name in names else {}
                attempts.append({'job_id': job_id, 'archive': row['path'],
                                 'status': result.get('status', 'infrastructure_incomplete'),
                                 'completed_proposals': result.get('summary', {}).get('completed_proposals', 0),
                                 **sum_cost(costs)})
    return attempts, archives


def ledger(out, manifest, exclude_call=None):
    rows = list(manifest['historical_attempts'])
    for p in sorted((out / 'runs').glob('*/calls/*/request.json')):
        if exclude_call is not None and p.parent == exclude_call:
            continue
        state_path = p.with_name('state.json')
        if state_path.exists() and read_json(state_path).get('status') == 'prepared':
            # The durable call layer writes `prepared` before its sent marker;
            # no provider request has been dispatched at this point.
            continue
        response_path = p.with_name('response.json')
        raw_path = p.with_name('raw_response.json')
        response = read_json(response_path) if response_path.exists() else {}
        if not response and raw_path.exists():
            try:
                raw = read_json(raw_path)
                if raw['envelope_sha256'] != digest(raw['envelope']):
                    raise ValueError('raw response hash changed')
                response = decode_response(raw['envelope'])
            except Exception:
                response = {}
        rows.append(call_cost(read_json(p), response))
    return sum_cost(rows)


def _decode_stored_response(raw_path):
    """Recover usage from a durable raw envelope without sending anything."""
    if not raw_path.exists():
        return {}
    raw = read_json(raw_path)
    if raw.get('envelope_sha256') != digest(raw.get('envelope', {})):
        raise ValueError('raw response hash changed')
    return decode_response(raw['envelope'])


def _run_call_audit(run):
    """Return a task-local call ledger and sanitized status categories.

    The global ledger is authoritative for budget reservation. This helper adds
    the per-task evidence needed to distinguish provider failure, unknown usage,
    and a request that was prepared but never marked sent.
    """
    costs = []
    categories = Counter()
    state_counts = Counter()
    prepared = 0
    parse_errors = 0
    calls_dir = run / 'calls'
    for request_path in sorted(calls_dir.glob('*/request.json')):
        folder = request_path.parent
        state_path = folder / 'state.json'
        state = read_json(state_path) if state_path.exists() else {}
        state_status = state.get('status', 'state_missing')
        state_counts[state_status] += 1
        if state_status == 'prepared':
            # DurableCalls writes this state before the sent marker and before
            # transport dispatch. It is not an attempted provider request.
            prepared += 1
            continue
        try:
            request = read_json(request_path)
            response_path = folder / 'response.json'
            response = read_json(response_path) if response_path.exists() else {}
            if not response:
                response = _decode_stored_response(folder / 'raw_response.json')
            cost = call_cost(request, response)
        except Exception:
            # A malformed durable record is itself an unknown-cost attempt;
            # preserve the request count without inventing token usage.
            parse_errors += 1
            cost = {'requests': 1, 'known_tokens': 0, 'unknown_requests': 1,
                    'unknown_reservation': 0}
        costs.append(cost)
        diagnostic = state.get('diagnostics') or {}
        category = diagnostic.get('error_category')
        if category:
            categories[str(category)] += 1
        elif state_status in {'provider_failed', 'sent_unknown', 'indeterminate'}:
            categories[state_status] += 1
    return {
        **sum_cost(costs),
        'state_counts': dict(state_counts),
        'diagnostic_categories': dict(categories),
        'prepared_requests': prepared,
        'parse_errors': parse_errors,
    }


def prepare(out, prefix, registry):
    if registry.resolve() != family_registry(prefix):
        raise ValueError('use the single family registry, not a new per-batch registry')
    if out.exists():
        raise ValueError('output already exists; use run/audit on the registered batch')
    verify_index()
    if git('status', '--porcelain'):
        raise ValueError('commit source before freeze')
    protocol = read_json(HERE / 'protocol.b.json')
    protocol_timeout = int(protocol['generation']['timeout_seconds'])
    continuation_timeout = int(PARAMETERS['timeout_seconds'])
    if protocol_timeout <= 0 or continuation_timeout < protocol_timeout:
        raise ValueError('continuation timeout must cover the frozen protocol request timeout')
    jobs = continuation_jobs(protocol)
    attempts, archives = historical_attempts()
    attempted = {r['job_id'] for r in attempts}
    prefix_manifest = read_json(prefix / 'manifest.json')
    if digest({k: v for k, v in prefix_manifest.items() if k != 'manifest_sha256'}) != prefix_manifest['manifest_sha256']:
        raise ValueError('prefix manifest changed')
    audit = read_json(prefix / 'PREFIX_AUDIT.json')
    files, payloads, snapshots, checkpoints = [], {}, {}, {}
    for row in prefix_manifest['data']:
        p = prefix / row['path']
        data = read_json(p)
        if file_sha(p) != row['sha256'] or set(data) != {'block', 'profile', 'probe', 'validation'}:
            raise ValueError('only exact frozen search snapshots allowed')
        snapshots[data['block']] = row
        files.append({'path': row['path'], 'sha256': row['sha256']})
        payloads[row['path']] = p.read_bytes()
    for row in audit['checkpoints']:
        p = prefix / row['path']
        cp = read_json(p)
        if file_sha(p) != row['sha256']:
            raise ValueError('checkpoint changed')
        if cp['status'] == 'ready':
            block = cp['continuation']['block']
            expected = _select_prefix_checkpoint(block, cp['prefix_step'], cp['nodes'], snapshots[block]['sha256'])
            if any(expected[k] != cp[k] for k in ('nodes', 'incumbent', 'branch')):
                raise ValueError('checkpoint uses wrong prefix/selection')
        checkpoints[row['id']] = cp
        files.append({'path': row['path'], 'sha256': row['sha256']})
        payloads[row['path']] = p.read_bytes()
    for job in jobs:
        cp = checkpoints[job['checkpoint_id']]
        job['eligibility'] = ('historical_attempt_preserved' if job['job_id'] in attempted else
                              'preparation_incomplete' if cp['status'] != 'ready' else
                              'branch_unavailable' if job['strategy'] == 'B' and not cp.get('branch') else 'ready')
    manifest = {'schema': 'chapter6-registered-continuation-v1', 'created_utc': utcnow(),
                'source_commit': git('rev-parse', 'HEAD'),
                'source_index_sha256': file_sha(HERE / 'SOURCE_SHA256.json'),
                'protocol_sha256': file_sha(HERE / 'protocol.b.json'),
                'revision_sha256': file_sha(HERE / 'RESUME_METHOD_ZH.md'),
                'prefix_manifest_sha256': prefix_manifest['manifest_sha256'],
                'registry': str(registry.resolve()), 'output': str(out.resolve()),
                'model': {'provider': 'minimax-cn-coding-plan', 'model': 'MiniMax-M3',
                          'transport': 'direct HTTP via local OpenCode config', 'concurrency': 1},
                'parameters': PARAMETERS, 'jobs': jobs, 'files': files,
                'timeout_policy': {
                    'protocol_request_timeout_seconds': protocol_timeout,
                    'continuation_request_timeout_seconds': continuation_timeout,
                    'continuation_revision': 'RESUME_METHOD_ZH.md explicitly raises the per-request wait bound to 600 seconds; the 900-second task wall remains authoritative',
                },
                'historical_attempts': attempts, 'historical_archives': archives,
                'limits': {'max_requests_including_history': 2048, 'max_tokens_including_history': 12800000,
                           'new_jobs': sum(j['eligibility'] == 'ready' for j in jobs),
                           'new_proposals': sum(j['eligibility'] == 'ready' for j in jobs) * 8,
                           'new_requests': sum(j['eligibility'] == 'ready' for j in jobs) * 16,
                           'new_tokens': sum(j['eligibility'] == 'ready' for j in jobs) * 100000},
                'authorization': {'user_instruction': '继续完善方法然后开展下一轮实验',
                    'scope': 'one resumption for never-started tasks only within existing total ceilings; no task retries, no new blocks, no C/D/E',
                    'prior_pause': 'preserved; this user instruction permits one new attempt to dispatch remaining jobs, not automatic restart of new halts'},
                'service_acceptance': {'basis': 'archived complete E0/EG workloads use the same provider/model/output caps',
                    'new_calls': 0, 'current_workload_check': 'first queued real task; no duplicate canary; incomplete task stops subsequent dispatch'},
                'rules': {'request_retry': False, 'task_retry': False,
                          'pause_on': ['unknown_usage', 'provider_server', 'authentication', 'quota_exhausted', 'connection_limit', 'infrastructure_error'],
                          'two_consecutive_rate_limits': True,
                          'test_access': False, 'unstarted_are_not_terminal': True},
                'test_access': False}
    manifest['manifest_sha256'] = digest(manifest)
    with run_lock(registry):
        owner = registry / 'owner.json'
        if owner.exists():
            raise ValueError('a recovery is already registered; do not create a replacement batch')
        out.mkdir(parents=True)
        for name, raw in payloads.items():
            p = out / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        freeze(out / 'manifest.json', manifest)
        freeze(owner, {'output': str(out.resolve()), 'manifest_sha256': manifest['manifest_sha256']})
        for job in jobs:
            if job['eligibility'] != 'ready':
                freeze(out / 'runs' / job['job_id'] / 'terminal_status.json',
                       {'status': job['eligibility'], 'new_model_calls': 0, 'test_access': False})
    return manifest


def verify(out):
    m = read_json(out / 'manifest.json')
    if digest({k: v for k, v in m.items() if k != 'manifest_sha256'}) != m['manifest_sha256']:
        raise ValueError('manifest changed')
    if str(out.resolve()) != m['output']:
        raise ValueError('not the registered output')
    owner = read_json(Path(m['registry']) / 'owner.json')
    if owner != {'output': m['output'], 'manifest_sha256': m['manifest_sha256']}:
        raise ValueError('registry owner mismatch')
    if git('rev-parse', 'HEAD') != m['source_commit'] or git('status', '--porcelain'):
        raise ValueError('execution source changed')
    verify_index()
    if file_sha(HERE / 'SOURCE_SHA256.json') != m['source_index_sha256']:
        raise ValueError('source index changed')
    for row in m['files']:
        if file_sha(out / row['path']) != row['sha256']:
            raise ValueError('frozen search input changed')
    return m


class GuardedTransport(RecoveryTransport):
    def __init__(self, out, manifest, run):
        super().__init__('minimax-cn-coding-plan', 'MiniMax-M3', run, timeout=600)
        self.out, self.manifest = out, manifest

    def send(self, request, persist):
        folder = self.directory / 'calls' / f"{request['step']:03d}-{request['stage']}"
        costs = ledger(self.out, self.manifest, exclude_call=folder)
        cap = self.manifest['limits']
        if (costs['requests'] + 1 > cap['max_requests_including_history'] or
                costs['known_tokens'] + costs['unknown_reservation'] + reserve(request) > cap['max_tokens_including_history']):
            freeze(folder / 'not_dispatched.json', {'reason': 'global_budget_reservation', 'utc': utcnow()})
            raise ValueError('global budget reservation refused')
        def checked_persist(envelope):
            persist(envelope)
            returned = decode_response(envelope)
            if returned.get('returned_model') != 'MiniMax-M3':
                self.last_diagnostics = {'error_category': 'returned_model_mismatch',
                                         'expected_model': 'MiniMax-M3', 'automatic_retry': False}
                raise ValueError('returned model differs from frozen requested model')
        return super().send(request, checked_persist)


def halt(out, job_id, reason):
    # Write pause before any optional audit, so an audit exception cannot lose it.
    freeze(out / 'halt.json', {'after_job': job_id, 'reason': reason, 'utc': utcnow(),
                               'automatic_resume': False, 'test_access': False})


def audit(out, manifest=None):
    m = manifest or read_json(out / 'manifest.json')
    historical = {row['job_id']: row for row in m.get('historical_attempts', [])}
    rows = []
    for job in m['jobs']:
        run = out / 'runs' / job['job_id']
        status = read_json(run / 'terminal_status.json') if (run / 'terminal_status.json').exists() else {}
        result = read_json(run / 'search_result.json') if (run / 'search_result.json').exists() else {}
        call_audit = _run_call_audit(run)
        prior = historical.get(job['job_id'], {})
        live_attempt_started = ((run / 'config.json').exists() or
                                (run / 'attempt_claim.json').exists() or
                                bool(call_audit['requests']))
        terminal_present = (run / 'terminal_status.json').exists()
        status_name = status.get('status', 'not_started')
        # Historical attempts are represented by a preserved terminal status,
        # but retain their archived resource ledger in the per-task row.
        requests = prior.get('requests', 0) + call_audit['requests']
        known_tokens = prior.get('known_tokens', 0) + call_audit['known_tokens']
        unknown_requests = prior.get('unknown_requests', 0) + call_audit['unknown_requests']
        unknown_reservation = prior.get('unknown_reservation', 0) + call_audit['unknown_reservation']
        diagnostics = status.get('diagnostics') or {}
        diagnostic_category = diagnostics.get('error_category')
        rows.append({'job_id': job['job_id'], 'strategy': job['strategy'], 'checkpoint_id': job['checkpoint_id'],
                     'status': status_name,
                     'completed_proposals': result.get('summary', {}).get('completed_proposals', 0),
                     'selections_sha256': file_sha(run / 'selection_candidates.json') if (run / 'selection_candidates.json').exists() else None,
                     'terminal_status_present': terminal_present,
                     'attempt_started_without_terminal': bool(live_attempt_started and not terminal_present),
                     'requests': requests,
                     'known_tokens': known_tokens,
                     'unknown_requests': unknown_requests,
                     'unknown_reservation': unknown_reservation,
                     'unknown_cost': bool(unknown_requests),
                     'provider_failure_categories': call_audit['diagnostic_categories'],
                     'terminal_error_category': diagnostic_category,
                     'prepared_requests': call_audit['prepared_requests'],
                     'call_parse_errors': call_audit['parse_errors']})
    costs = ledger(out, m)
    counts = dict(Counter(r['status'] for r in rows))
    incomplete = [r['job_id'] for r in rows if r['attempt_started_without_terminal']]
    unknown_cost_jobs = [r['job_id'] for r in rows if r['unknown_cost']]
    provider_failure_jobs = [r['job_id'] for r in rows
                             if r['provider_failure_categories'] or r['terminal_error_category']]
    value = {'manifest_sha256': m['manifest_sha256'], 'created_utc': utcnow(),
             'rows': rows, 'status_counts': counts, 'cost_including_history': costs,
             'all_tasks_terminal': all(r['status'] in TERMINAL and
                                       not r['attempt_started_without_terminal'] for r in rows),
             'unfinished_started_jobs': incomplete,
             'unknown_cost_job_ids': unknown_cost_jobs,
             'provider_failure_job_ids': provider_failure_jobs,
             'halted': (out / 'halt.json').exists(), 'test_access': False}
    save_json(out / 'progress.json', value)
    return value


def dispatch(out, *, runner=run_phase_b, transport_factory=GuardedTransport):
    m = verify(out)
    registry = Path(m['registry'])
    with run_lock(registry):
        if (out / 'halt.json').exists():
            raise ValueError('batch halted; automatic resumption prohibited')
        for job in m['jobs']:
            run = out / 'runs' / job['job_id']
            if (run / 'terminal_status.json').exists():
                continue
            claim = registry / 'claims' / (job['job_id'] + '.json')
            if claim.exists() or (run / 'config.json').exists():
                freeze(run / 'terminal_status.json', {'status': 'infrastructure_incomplete',
                       'reason': 'prior_started_attempt_will_not_restart', 'test_access': False})
                halt(out, job['job_id'], 'interrupted_claim_preserved')
                break
            costs = ledger(out, m)
            if (costs['requests'] + 16 > m['limits']['max_requests_including_history'] or
                costs['known_tokens'] + costs['unknown_reservation'] + 100000 > m['limits']['max_tokens_including_history']):
                halt(out, job['job_id'], 'cannot_reserve_complete_task_budget')
                break
            # Claim is durable BEFORE any provider construction or network request.
            freeze(claim, {'job_id': job['job_id'], 'manifest_sha256': m['manifest_sha256'],
                           'started_utc': utcnow(), 'reserved_requests': 16, 'reserved_tokens': 100000})
            freeze(run / 'attempt_claim.json', read_json(claim))
            print(json.dumps({'event': 'task_start', 'job_id': job['job_id'], 'utc': utcnow()}), flush=True)
            transport = None
            start = time.perf_counter()
            try:
                cp = read_json(out / 'checkpoints' / (job['checkpoint_id'] + '.json'))
                data = read_json(out / 'data' / f"search-b{job['data_block']}.json")
                transport = transport_factory(out, m, run)
                result = runner(job, cp, data, run, {'manifest_sha256': m['manifest_sha256'],
                    'source_commit': m['source_commit']}, m['parameters'], transport, mode='live')
                status = result['status']
                if status not in TERMINAL:
                    raise ValueError('runner returned unknown status')
                reason = result.get('stop_details', {})
            except Exception as exc:
                status = 'infrastructure_incomplete'
                reason = {'error_type': type(exc).__name__, 'automatic_retry': False}
            diagnostic = getattr(transport, 'last_diagnostics', None)
            freeze(run / 'terminal_status.json', {'status': status, 'reason': reason,
                    'diagnostics': diagnostic, 'wall_seconds': time.perf_counter() - start,
                    'test_access': False, 'utc': utcnow()})
            after = ledger(out, m)
            if (status == 'infrastructure_incomplete' or diagnostic or
                    after['unknown_requests'] > costs['unknown_requests']):
                halt(out, job['job_id'], {'status': status, 'diagnostics': diagnostic,
                                          'unknown_usage': after['unknown_requests'] > costs['unknown_requests']})
            progress = audit(out, m)
            print(json.dumps({'event': 'task_end', 'job_id': job['job_id'], 'status': status,
                              'counts': progress['status_counts'], 'cost': after}), flush=True)
            if (out / 'halt.json').exists():
                break
            time.sleep(1)
        return audit(out, m)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run', 'audit'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prefix', type=Path)
    parser.add_argument('--registry', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.prefix is None or args.registry is None:
            parser.error('prepare requires --prefix and --registry')
        result = prepare(args.output.resolve(), args.prefix.resolve(), args.registry.resolve())
    elif args.action == 'run':
        result = dispatch(args.output.resolve())
    else:
        result = audit(args.output.resolve())
    print(json.dumps({k: result[k] for k in ('manifest_sha256', 'limits', 'status_counts',
                    'cost_including_history', 'all_tasks_terminal') if k in result}))


if __name__ == '__main__':
    main()
