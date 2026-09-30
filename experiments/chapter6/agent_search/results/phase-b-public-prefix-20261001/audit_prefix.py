"""Read-only audit of this halted prefix batch; no evaluator or model calls.

Run from repository root using the e40ce10 method files:
python -m experiments.chapter6.agent_search.results.phase-b-public-prefix-20261001.audit_prefix --study PATH --output PATH
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
from chapter6_demo import benchmarks
from chapter6_demo.v12_2.common import digest
from chapter6_demo.v12_2.calls import decode_response
from experiments.chapter6.agent_search.minimal_mechanism.phase_b_runner import history_summary

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write(path, value):
    Path(path).write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))

def check_digest(obj, field):
    assert digest({k:v for k,v in obj.items() if k != field}) == obj[field], field

def audit(study, output):
    study, output = Path(study), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = read(study/'manifest.json')
    cps = read(study/'checkpoint_manifest.json')
    check_digest(manifest, 'manifest_sha256')
    check_digest(cps, 'checkpoint_manifest_sha256')
    assert cps['study_manifest_sha256'] == manifest['manifest_sha256']
    assert len(cps['records']) == 16 and len(manifest['prefix_jobs']) == 8
    root = Path(__file__).resolve().parents[5]
    for name in ['experiments/chapter6/agent_search/minimal_mechanism/phase_b_runner.py',
                 'experiments/chapter6/demo/benchmarks.py']:
        assert sha((root/name).read_bytes()) == manifest['tooling_source']['files'][name]
    for rec in manifest['data']:
        assert sha((study/rec['path']).read_bytes()) == rec['sha256']
        snapshot = read(study/rec['path'])
        assert len(snapshot['probe']) == 12 and len(snapshot['validation']) == 36
        assert 'test' not in snapshot
    assert not (study/'runs').exists(), 'continuation must remain unstarted'
    assert not (study/'test_gate.json').exists()
    assert not list(study.glob('data/*test*'))
    assert not (study/'test').exists()
    blocks, calls, demos = [], [], []
    saved_by_block = {}
    for job in manifest['prefix_jobs']:
        run = study/'prefix_runs'/job['job_id']
        terminal = read(run/'terminal_status.json')
        saved = read(run/'checkpoint.json') if (run/'checkpoint.json').exists() else {}
        saved_by_block[job['data_block']] = saved
        rows = saved.get('records', [])
        nodes = saved.get('seeds', []) + [r['node'] for r in rows]
        result = read(run/'search_result.json') if (run/'search_result.json').exists() else {}
        job_calls = []
        for state_path in sorted(run.glob('calls/*/state.json')):
            folder = state_path.parent
            state, request = read(state_path), read(folder/'request.json')
            assert state['request_sha256'] == digest(request)
            assert request['model'] == 'MiniMax-M3' and request['temperature'] == .7
            rec = {'job_id': job['job_id'], 'call': folder.name, 'status': state['status'],
                   'request_sha256': digest(request), 'requested_model': request['model'],
                   'sent_utc_if_retained': state.get('sent_utc'),
                   'known_tokens': None, 'input_tokens': None, 'output_tokens': None,
                   'request_token_reserve': len(request['system'].encode()) + len(request['prompt'].encode()) + 512 + request['max_tokens']}
            if state['status'] == 'response_persisted':
                raw, response = read(folder/'raw_response.json'), read(folder/'response.json')
                assert raw['request_sha256'] == digest(request)
                assert raw['envelope_sha256'] == digest(raw['envelope'])
                decoded = decode_response(raw['envelope'])
                assert all(response.get(k) == v for k,v in decoded.items())
                assert decoded['usage_complete'] and decoded['returned_model'] == 'MiniMax-M3'
                rec.update({k: decoded[k] for k in ['input_tokens','output_tokens','returned_model','seconds','request_id']})
                rec.update(known_tokens=decoded['input_tokens']+decoded['output_tokens'],
                           received_utc=raw['received_utc'], endpoint=raw['envelope'].get('endpoint'))
            else:
                assert state['status'] == 'sent_unknown'
                assert not (folder/'raw_response.json').exists()
            job_calls.append(rec)
        known = sum(r['known_tokens'] for r in job_calls if r['known_tokens'] is not None)
        unknown = sum(r['known_tokens'] is None for r in job_calls)
        valid = sum(r['node']['evaluation']['valid'] for r in rows)
        seeds = saved.get('seeds', [])
        for row in rows:
            node = row['node']
            prior = [n for n in nodes if n['id'] < node['id'] and n['evaluation']['valid']]
            parent = min(prior, key=lambda n:(n['evaluation']['loss'], n['id']))
            assert row['decision']['action'] == 'develop' and node['parent_id'] == parent['id']
            demos.append({'block':job['data_block'], 'proposal':node['id']-len(seeds)+1,
                'parent_id':node['parent_id'], 'node_id':node['id'], 'valid':node['evaluation']['valid'],
                'validation_gap_percent':100*node['evaluation']['loss'],
                'global_improvement':row['event']['global_improvement'],
                'known_tokens_cumulative':row['usage_after']['known_tokens'],
                'elapsed_seconds':row['elapsed_after'], 'source':f'prefix_runs/{job["job_id"]}/slots/{node["id"]-len(seeds):03d}/candidate.json'})
        blocks.append({'block':job['data_block'], 'job_id':job['job_id'], 'status':terminal['status'],
            'reason':terminal.get('reason') or result.get('stop_details',{}).get('error_type'),
            'completed_proposals':len(rows), 'attempted_proposal_slots':len({r['call'].split('-')[0] for r in job_calls}),
            'valid_generated':valid, 'valid_fraction_completed':valid/len(rows) if rows else None,
            'request_attempts':len(job_calls), 'known_tokens_lower_bound':known,
            'unknown_request_count':unknown, 'wall_seconds':result.get('summary',{}).get('wall_seconds',0),
            'proposal_evaluations':sum(r['node']['evaluation'].get('instance_evaluations',0) for r in rows),
            'seed_evaluations':sum(n['evaluation'].get('instance_evaluations',0) for n in seeds),
            'evaluation_wall_seconds':sum(n['evaluation'].get('wall_seconds',0) for n in nodes),
            'evaluation_cpu_seconds':sum(n['evaluation'].get('cpu_seconds',0) for n in nodes),
            'request_cap':48, 'token_cap':250000})
        calls.extend(job_calls)
    coverage = []
    for rec in sorted(cps['records'], key=lambda r:(r['block'],r['prefix_step'])):
        cp = read(study/rec['path'])
        assert sha((study/rec['path']).read_bytes()) == rec['sha256']
        saved = saved_by_block[rec['block']]
        item = {k:rec[k] for k in ['block','prefix_step','checkpoint_id','status','sha256']}
        item.update(incumbent_validation_gap_percent=None, branch_status='checkpoint_unavailable',
                    branch_gap_pp=None, branch_behavior_distance=None, branch_code_different=None,
                    branch_structure_different=None, history_preview_path=None)
        if cp['status'] == 'ready':
            nodes = saved['seeds']+[r['node'] for r in saved['records'][:rec['prefix_step']]]
            assert cp['nodes'] == nodes, 'future proposal leakage'
            valid = [n for n in nodes if n['evaluation']['valid']]
            inc = min(valid, key=lambda n:(n['evaluation']['loss'],n['id']))
            assert inc['id'] == cp['incumbent']['id']
            eligible = []
            for n in valid:
                delta=n['evaluation']['loss']-inc['evaluation']['loss']
                dist=benchmarks.behavior_distance(n['evaluation']['behavior'],inc['evaluation']['behavior'])
                if n['code'] != inc['code'] and 0 < delta <= .035 and dist > .08:
                    eligible.append(n)
            branch=min(eligible,key=lambda n:(n['evaluation']['loss'],n['id'])) if eligible else None
            assert (branch['id'] if branch else None) == (cp['branch']['id'] if cp['branch'] else None)
            item.update(incumbent_validation_gap_percent=100*inc['evaluation']['loss'],
                        incumbent_id=inc['id'], eligible_branch_count=len(eligible),
                        branch_status='available' if branch else 'branch_unavailable')
            if branch:
                item.update(branch_id=branch['id'],branch_validation_gap_percent=100*branch['evaluation']['loss'],
                    branch_gap_pp=100*(branch['evaluation']['loss']-inc['evaluation']['loss']),
                    branch_behavior_distance=benchmarks.behavior_distance(branch['evaluation']['behavior'],inc['evaluation']['behavior']),
                    branch_code_different=branch['code'] != inc['code'],
                    branch_structure_different=branch['evaluation']['program_identity']['structural_sha256'] != inc['evaluation']['program_identity']['structural_sha256'],
                    incumbent_code=inc['code'], branch_code=branch['code'])
            summary=history_summary(nodes,inc['evaluation']['loss'])
            name=f'EG_HISTORY_PREVIEW_{rec["checkpoint_id"]}.json'
            write(output/name, {'kind':'offline_reconstruction_not_an_executed_EG_action',
                'source_node_ids':[n['id'] for n in nodes], 'summary':summary,
                'summary_sha256':digest(summary), 'new_model_calls':0, 'used_in_search':False})
            item.update(history_preview_path=name, history_summary_sha256=digest(summary),
                        history_valid_count=summary['quality_level']['valid_program_count'])
        else:
            assert len(saved.get('records',[])) < rec['prefix_step']
        coverage.append(item)
    known = sum(x['known_tokens_lower_bound'] for x in blocks)
    attempts = sum(x['request_attempts'] for x in blocks)
    unknown = sum(x['unknown_request_count'] for x in blocks)
    observed = {'requests':attempts,'known_tokens_lower_bound':known,'unknown_requests':unknown,
                'request_ceiling':384,'token_ceiling':2000000, 'unspent_request_slots':384-attempts,
                'token_balance_exact':None if unknown else 2000000-known,
                'ceiling_minus_known_tokens_is_only_an_upper_bound':2000000-known,
                'unknown_request_reservation':sum(x['request_token_reserve'] for x in calls if x['known_tokens'] is None),
                'allocation_released_for_further_calls':False}
    result={'study_manifest_sha256':manifest['manifest_sha256'],'source_commit':manifest['source_commit'],
            'checkpoint_manifest_sha256':cps['checkpoint_manifest_sha256'],
            'halt':read(study/'dispatch/halt.json'), 'block_rows':sorted(blocks,key=lambda x:x['block']),
            'checkpoint_rows':coverage, 'budget':observed,
            'planned_continuations':128,'authorized_continuations':0,'executed_continuations':0,
            'test_evaluations':0,'new_model_calls_during_audit':0,'ready_checkpoints':sum(x['status']=='ready' for x in coverage),
            'coverage_complete':all(x['status']=='ready' for x in coverage),
            'audit_passed':True,'audit_meaning':'raw integrity, selection, SP parents and isolation; not complete experimental coverage',
            'branch_denominator_warning':'15 unavailable checkpoints are not observed failures of branch eligibility'}
    write(output/'PREFIX_AUDIT.json',result)
    write(output/'CALL_AUDIT.json',calls)
    write(output/'DEMO_REAL.json',{'kind':'real_record_projection_no_synthetic_or_counterfactual_results',
        'study_manifest_sha256':manifest['manifest_sha256'], 'trajectory':demos,
        'terminal_unknown_request':[r for r in calls if r['status']=='sent_unknown'], 'test_access':False})
    fields=['block','prefix_step','status','incumbent_validation_gap_percent','branch_status','branch_gap_pp',
            'branch_behavior_distance','branch_code_different','branch_structure_different','history_preview_path','sha256']
    with (output/'COVERAGE.csv').open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore',lineterminator='\n')
        writer.writeheader(); writer.writerows(coverage)
    print(json.dumps({'audit_passed':True, 'ready':result['ready_checkpoints'],'budget':observed}))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.study,a.output)
