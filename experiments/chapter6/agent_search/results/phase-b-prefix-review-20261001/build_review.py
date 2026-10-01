"""Audit the existing halted prefix; no generation, evaluator, or Test calls."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

from chapter6_demo.discovery import SYSTEM, planner_prompt
from chapter6_demo.benchmarks import SEEDS
from chapter6_demo.v12_1.audit_r2 import offline_only
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_runner import PhaseBState, _append_history_prompt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OLD = HERE.parent / 'phase-b-public-prefix-20261001'
METHOD = ROOT / 'experiments/chapter6/agent_search/minimal_mechanism'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write(name, value):
    path = HERE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode())

def read(path):
    return json.loads(Path(path).read_bytes())

def build(study, junit):
    audit_module = importlib.import_module('experiments.chapter6.agent_search.results.phase-b-public-prefix-20261001.audit_prefix')
    audit_module.audit(study, HERE)
    audit = read(HERE / 'PREFIX_AUDIT.json')
    manifest = read(study / 'manifest.json')
    cps = read(study / 'checkpoint_manifest.json')
    source = read(METHOD / 'SOURCE_SHA256.json')
    for name in ('raw-study.zip', 'supporting-evidence.zip'):
        shutil.copyfile(OLD / name, HERE / name)
    for name in ('manifest.json', 'checkpoint_manifest.json'):
        shutil.copyfile(study / name, HERE / name)
    shutil.copyfile(junit, HERE / 'OFFLINE_TESTS.xml')
    for rec in cps['records']:
        target = HERE / rec['path']
        target.parent.mkdir(exist_ok=True)
        target.write_bytes((study / rec['path']).read_bytes())

    rows, eg_checks = [], []
    block_map = {row['block']: row for row in audit['block_rows']}
    cp_map = {rec['checkpoint_id']: rec for rec in cps['records']}
    seed_spec = [{'id': i, 'name': name, 'tags': tags, 'code': code,
                 'raw_code_sha256': sha(code.encode())}
                 for i, (name, tags, code) in enumerate(SEEDS['tsp'])]
    write('COMMON_SEEDS.json', {'source_commit': manifest['source_commit'],
        'programs': seed_spec, 'model_calls':0, 'new_evaluations':0})
    for item in audit['checkpoint_rows']:
        cp = read(study / cp_map[item['checkpoint_id']]['path'])
        b = block_map[item['block']]
        saved_path = study / 'prefix_runs' / b['job_id'] / 'checkpoint.json'
        records = read(saved_path).get('records', []) if saved_path.exists() else []
        if saved_path.exists():
            saved_seeds = read(saved_path)['seeds']
            assert len(saved_seeds) == len(seed_spec)
            assert all(seed['code'] == spec['code'] and seed['name'] == spec['name']
                       for seed, spec in zip(saved_seeds, seed_spec))
        visible = records[:item['prefix_step']]
        row = {**item, 'task_terminal_status': b['status'],
            'task_completed_proposals': b['completed_proposals'],
            'observed_proposals_up_to_requested_step': len(visible),
            'valid_proposals_up_to_requested_step': sum(r['node']['evaluation']['valid'] for r in visible),
            'invalid_proposals_up_to_requested_step': sum(not r['node']['evaluation']['valid'] for r in visible),
            'truncated_proposals_up_to_requested_step': sum('truncat' in json.dumps(r['node'].get('proposal_failure')) for r in visible),
            'request_attempts_whole_task': b['request_attempts'],
            'known_tokens_whole_task': b['known_tokens_lower_bound'],
            'unknown_calls_whole_task': b['unknown_request_count'], 'task_wall_seconds': b['wall_seconds']}
        if cp['status'] == 'ready':
            d0, dg = PhaseBState(cp, 'E0').choose(0), PhaseBState(cp, 'EG').choose(0)
            prompts = {}
            for strategy, decision in [('E0', d0), ('EG', dg)]:
                prompt = planner_prompt('tsp', {'target': decision['target'], 'action': decision['action'],
                    'parent': None, 'reference': None, 'evidence': decision['evidence']}, 0)
                prompts[strategy] = _append_history_prompt(prompt, decision)
                (HERE / f'{strategy}_PROMPT_{item["checkpoint_id"]}.txt').write_bytes(prompts[strategy].encode())
            summary = dg['evidence']['historical_search_summary']
            hypotheses = [n.get('strategy_hypothesis') for n in cp['nodes']]
            check = {'checkpoint_id': item['checkpoint_id'], 'source_node_ids': [n['id'] for n in cp['nodes']],
                'summary_program_ids': [n['node_id'] for n in summary['recorded_program_summaries']],
                'nonempty_intents': sum(bool(n.get('intent')) for n in summary['recorded_program_summaries']),
                'hypothesis_field_missing_or_null': sum(h is None for h in hypotheses),
                'source_node_count': len(cp['nodes']),
                'observed_failures_count': len(summary['observed_failures']),
                'unseen_tag_count': len(summary['not_yet_observed_operator_tags']),
                'same_output_schema': d0['evidence']['required_strategy_hypothesis'] == dg['evidence']['required_strategy_hypothesis'],
                'same_model': 'MiniMax-M3', 'same_provider': 'minimax-cn-coding-plan',
                'same_interface': 'def priority(f): -> numeric priority',
                'EG_history_reaches_actual_planner_builder': 'historical_search_summary' in prompts['EG'] and json.dumps(summary, ensure_ascii=False) in prompts['EG'],
                'E0_excludes_history': 'historical_search_summary' not in prompts['E0'],
                'system_sha256': sha(SYSTEM.encode()), 'prompt_sha256': {k: sha(v.encode()) for k,v in prompts.items()},
                'no_future_nodes': len(cp['nodes']) == 3 + item['prefix_step'],
                'prompt_sent': False, 'model_calls': 0,
                'limitations': ['all strategy_hypothesis fields missing/null',
                    'valid but inferior attempts are not in observed_failures',
                    'top five summary is not a complete attempt history',
                    'no unseen tags does not prove direction coverage']}
            assert check['same_output_schema'] and check['EG_history_reaches_actual_planner_builder'] and check['E0_excludes_history']
            eg_checks.append(check)
            for side in ('incumbent', 'branch'):
                if cp.get(side):
                    node = next(n for n in cp['nodes'] if n['id'] == cp[side]['id'])
                    row[side+'_ast_nodes'] = sum(1 for _ in ast.walk(ast.parse(node['code'])))
                    row[side+'_program_identity'] = node['evaluation'].get('program_identity')
        rows.append(row)
    write('COVERAGE_DETAIL.json', rows)
    write('EG_INPUT_AUDIT.json', eg_checks)
    matrix = []
    for job in manifest['jobs']:
        rec = cp_map[job['checkpoint_id']]
        cp = read(study / rec['path'])
        available = rec['status'] == 'ready' and (job['strategy'] != 'B' or cp.get('branch') is not None)
        reason = 'ready_but_not_authorized' if available else ('checkpoint_unavailable' if rec['status'] != 'ready' else 'branch_unavailable')
        matrix.append({**job, 'checkpoint_sha256': rec['sha256'], 'feasibility': reason,
            'eligible_for_future_request': available, 'authorized': False, 'candidate_freeze_steps': [4,8],
            'caps_if_approved': {'proposals': 8, 'requests': 16, 'tokens': 100000, 'wall_seconds':900},
            'planned_proposals_now':0, 'planned_requests_now':0})
    eligible = sum(j['eligible_for_future_request'] for j in matrix)
    request = {'status':'REQUEST_ONLY_NOT_AUTHORIZATION', 'old_study_manifest_sha256':manifest['manifest_sha256'],
        'planned_task_rows':len(matrix), 'eligible_tasks':eligible, 'unavailable_tasks':len(matrix)-eligible,
        'requested_if_separately_approved': {'proposals':8*eligible, 'requests':16*eligible, 'tokens':100000*eligible,
            'serial_wall_seconds':900*eligible, 'search_instance_evaluations':8*48*eligible},
        'current_approved_continuation_requests':0, 'old_maximum':{'tasks':128,'proposals':1024,'requests':2048,'tokens':12800000},
        'conditional_branch_estimand':'B-I only on ready branch-eligible checkpoints; currently one block, not confirmatory',
        'all_state_definition':'on a READY state lacking B, B_policy uses the matched I result without an extra call; keep literal B unavailable. Missing checkpoints stay missing for every arm.',
        'conditions_before_dispatch':['separate explicit continuation authorization', 'resolve frozen global halt and service integrity in a new reviewed continuation protocol',
            'bind immutable old prefix provenance to newly frozen continuation source', 'decide whether to revise EG summary before freezing; do not alter prefix',
            'keep final Test closed until all continuation terminal statuses and candidate freezes reviewed'],
        'tasks':matrix}
    write('CONTINUATION_REQUEST.json',request)
    scope = {'status':'RECORDED_USER_SCOPE_BLOCKED_BY_EXISTING_BATCH', 'date':'2026-10-01',
        'allowed_stage':'public_prefix', 'provider':'minimax-cn-coding-plan','requested_model':'MiniMax-M3',
        'blocks':list(range(60,68)), 'max_proposals':192,'max_requests':384,'max_tokens':2000000,
        'per_block':{'proposals':24,'requests':48,'tokens':250000,'wall_seconds':3600},
        'concurrency':1,'automatic_retries':False,'pause_after_consecutive_rate_limits':2,
        'forbidden':['continuation','C','D','E','Test','replacement_batch','resampling','cross_stage_budget_transfer'],
        'existing_batch':manifest['manifest_sha256'],'new_requests':0,'new_acceptance_requests':0,
        'not_an_executable_approval_file':True, 'reason':'all requested blocks claimed since e40ce10; halt cannot be silently resumed or replaced'}
    write('USER_SCOPE.json',scope)
    write('BUDGET_LEDGER.json', {**audit['budget'], 'new_review_requests':0,'new_review_tokens':0,
        'completed_proposals':sum(b['completed_proposals'] for b in audit['block_rows']),
        'evaluation_instances':sum(b['proposal_evaluations']+b['seed_evaluations'] for b in audit['block_rows']),
        'task_wall_seconds':sum(b['wall_seconds'] for b in audit['block_rows']),
        'confirmed_total_tokens':None, 'unknown_cost_is_not_zero':True, 'continuation_authorized':False, 'test_open':False})
    write('BLOCK_USAGE_CONFLICT.json', {'status':'CONFLICT_NO_NEW_BATCH','basis_commit':'e40ce1023846b293327c94859aafc3f7a58bba97',
        'remote_updates':['be06a83','bac0b9e'],'prior_manifest_sha256':manifest['manifest_sha256'],
        'blocks':[{'block':b['block'],'claimed':True,'actually_called':b['request_attempts']>0,'status':b['status']} for b in audit['block_rows']],
        'scan_scope':'Git changes since e40ce10 and both published archives; no Test scores or full repository download',
        'replacement_or_resumption_created':False})
    with zipfile.ZipFile(HERE/'supporting-evidence.zip') as z:
        acceptance_bytes=z.read('provider-acceptance.json')
        acceptance=json.loads(acceptance_bytes)
    calls=read(HERE/'CALL_AUDIT.json')
    write('SERVICE_COMPATIBILITY.json', {'existing_acceptance_file_sha256':sha(acceptance_bytes),
        'acceptance_time':acceptance['utc'],'requested_model':acceptance['requested_model'],'returned_model':acceptance['returned_model'],
        'completed_planner_coder':[3,3], 'existing_prefix_returned_models':sorted({c['returned_model'] for c in calls if c.get('returned_model')}),
        'existing_prefix_endpoints':sorted({c['endpoint'] for c in calls if c.get('endpoint')}),
        'existing_prefix_generation':manifest['protocol']['generation'],
        'new_acceptance_calls':0, 'current_live_service_availability':'not_tested; old success does not resolve sent_unknown',
        'limitations':['acceptance summary alone lacks provider and cap fields; prior prefix request/raw-response records provide those',
            'no current quota assertion','no claimed immutable provider version']})
    write('REVIEW_MANIFEST.json', {'schema':'chapter6-prefix-review-not-run-manifest-v1','status':'offline_review_blocked',
        'revised_source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'executed_source_commit':manifest['source_commit'],'executed_manifest_sha256':manifest['manifest_sha256'],
        'revised_source_index_digest':source['manifest_sha256'],'revised_protocol_file_sha256':sha((METHOD/'protocol.b.json').read_bytes().replace(b'\r\n',b'\n')),
        'planned_prefixes':8,'ready_checkpoints':audit['ready_checkpoints'],'planned_checkpoints':16,
        'no_new_run_manifest':True,'no_new_model_calls':True,'no_test_access':True,
        'data_records':manifest['data'], 'frozen_prefix_order':[j['data_block'] for j in manifest['prefix_jobs']]})
    print(json.dumps({'review':'complete','eligible_continuations':eligible,'requested':request['requested_if_separately_approved']}))

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True)
    parser.add_argument('--junit',type=Path,required=True)
    args=parser.parse_args()
    with offline_only():
        build(args.study,args.junit)
