"""Supplemental S3 analysis and figures; does not call models or edit frozen runs.

Primary contrasts remain the separately frozen analyzer's output. Additional
mechanism counts, same-state next decisions and validation curves are descriptive.
"""
from __future__ import annotations

import argparse
import base64
import collections
import copy
import csv
import json
from pathlib import Path
import statistics

from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.common import read_json, save_json, file_sha, utcnow
from .s3_tsp_r3.controller import restore_state, plain

ARMS = ['SP', 'WR', 'FB_U', 'FB_P', 'TS_P', 'AD_P']


def write_csv(path, rows):
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [], lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)


def cost(study):
    study = Path(study)
    manifest = read_json(study / 'manifest.json')
    stages = {}
    for role, folders in [('search', sorted((study / 'runs').glob('*/calls/*'))),
                          ('preflight', sorted((study / 'preflight/calls').glob('*')))]:
        result = collections.Counter(); models = collections.Counter(); finishes = collections.Counter()
        for folder in folders:
            if not (folder / 'request.json').exists(): continue
            result['request_records'] += 1
            state = read_json(folder / 'state.json') if (folder / 'state.json').exists() else {}
            result['state:' + str(state.get('status', 'missing'))] += 1
            raw_path = folder / 'raw_response.json'
            if raw_path.exists():
                raw = read_json(raw_path); decoded = decode_response(raw['envelope'])
                result['persisted_responses'] += 1
                models[str(decoded['returned_model'])] += 1
                result['known_input_tokens'] += decoded['input_tokens'] or 0
                result['known_output_tokens'] += decoded['output_tokens'] or 0
                result['request_wall_seconds'] += decoded['seconds']
                result['unknown_usage_requests'] += int(not decoded['usage_complete'])
                body = json.loads(base64.b64decode(raw['envelope']['body_base64']))
                choices = body.get('choices') or []
                finishes[str(choices[0].get('finish_reason')) if choices else 'missing'] += 1
            else:
                result['requests_without_persisted_response'] += 1
                result['unknown_usage_requests'] += 1
        stages[role] = {**result, 'returned_models': dict(models), 'finish_reasons': dict(finishes),
                        'known_tokens': result['known_input_tokens'] + result['known_output_tokens'],
                        'usage_complete': result['unknown_usage_requests'] == 0}
    statuses = collections.Counter(); proposals = 0
    for job in manifest['jobs']:
        directory = study / 'runs' / job['job_id']
        p = directory / 'status.json'
        statuses[read_json(p).get('status') if p.exists() else 'not_started_or_no_terminal_status'] += 1
        checkpoint = directory / 'checkpoint.json'
        if checkpoint.exists(): proposals += len(read_json(checkpoint)['records'])
    return {'study_id': manifest['study_id'], 'planned_jobs': len(manifest['jobs']),
            'status_counts': dict(statuses), 'completed_proposals': proposals, 'stages': stages,
            'dollar_cost': None, 'billing_note': 'Coding plan actual invoice not available; tokens are observed usage, not a currency estimate.'}


def collect(study):
    manifest = read_json(study / 'manifest.json')
    rows, curves, same_state, demo = [], [], [], []
    for job in manifest['jobs']:
        directory = study / 'runs' / job['job_id']
        checkpoint = read_json(directory / 'checkpoint.json')
        selection = read_json(directory / 'selection_frozen.json')
        result = read_json(directory / 'search_result.json')
        test_path = study / 'tests' / (job['job_id'] + '.json')
        if not test_path.exists(): raise ValueError('All 48 test results are required before final readout')
        test = read_json(test_path)
        assert test['binding']['readout_sha256'] == file_sha(directory / 'selection_frozen.json')
        events = [r['event'] for r in checkpoint['records']]
        nodes = checkpoint['seeds'] + [r['node'] for r in checkpoint['records']]
        by_id = {n['id']: n for n in nodes}
        ancestors = set(); current = selection['best_id']
        while current is not None:
            assert current not in ancestors
            ancestors.add(current); current = by_id[current].get('parent_id')
        protected = [e for e in events if e['protected_development']]
        lagging = [e for e in protected if e['protected_parent_was_behind_global']]
        admissions = [e for e in events if e['branch_entry_created']]
        granted = [e for e in admissions if e['development_grant_awarded'] > 0]
        parentless = [e for e in granted if e['parent_id'] is None]
        lagging_admissions = [e for e in granted if e['loss'] > e['global_best_loss_before'] + job['gain_epsilon']]
        root_admitted = {e['node_id'] for e in parentless}
        attempt_depth, success_depth = {}, {}
        state = restore_state({**checkpoint, 'records': []})
        decisions = []
        initial = min(n['evaluation']['loss'] for n in checkpoint['seeds'])
        curves.append({'job_id': job['job_id'], 'arm': job['arm_id'], 'block': job['data_block'],
                       'step': 0, 'tokens': 0, 'wall_seconds': 0.0, 'validation_gap': initial})
        for record in checkpoint['records']:
            event, saved = record['event'], record['decision']
            step = saved['step']
            available = state._available_branches()
            if job['arm_id'] == 'FB_P':
                alternative = copy.deepcopy(state); alternative.protection = False
                alternate = alternative.choose(step)
                semantic = lambda d: (d['action'], d['parent']['id'] if d['parent'] else None,
                                       d['reference']['id'] if d['reference'] else None, d['target'])
                same_state.append({'job_id': job['job_id'], 'block': job['data_block'], 'step': step,
                                   'protected': saved['allocation']['protected'],
                                   'available_branches': len(available),
                                   'actual_action': saved['action'], 'disabled_action': alternate['action'],
                                   'actual_parent': saved['parent']['id'] if saved['parent'] else None,
                                   'disabled_parent': alternate['parent']['id'] if alternate['parent'] else None,
                                   'generation_decision_changes': semantic(saved) != semantic(alternate),
                                   'branch_identity_changes': saved['allocation']['direction_id'] != alternate['allocation']['direction_id']})
            pool_before = [{k: v for k, v in entry.items()} for entry in state.pool.values()]
            chosen = state.choose(step)
            assert plain(chosen) == plain(saved)
            observed = state.observe(record['node'], record['costs'])
            assert plain(observed) == plain(event)
            parent_id = event['parent_id']; identifier = event['node_id']
            attempt_depth[identifier] = (attempt_depth.get(parent_id, 0) + 1) if event['branch_development'] else 0
            success_depth[identifier] = (success_depth.get(parent_id, 0) + 1) if event['branch_development'] and event['local_improvement'] else 0
            curves.append({'job_id': job['job_id'], 'arm': job['arm_id'], 'block': job['data_block'],
                           'step': step+1, 'tokens': record['usage_after']['known_tokens'],
                           'wall_seconds': record['elapsed_after'], 'validation_gap': event['global_best_loss_after']})
            decisions.append({'step': step, 'action': saved['action'],
                              'parent_id': parent_id, 'reference_id': record['node']['reference_id'],
                              'branch': saved['allocation']['direction_id'],
                              'protected': event['protected_development'],
                              'lagging': event['protected_parent_was_behind_global'],
                              'p_develop': saved['evidence']['p_develop'], 'pool_before': pool_before,
                              'pool_after': list(copy.deepcopy(state.pool).values()),
                              'loss': event['loss'], 'best_before': event['global_best_loss_before'],
                              'best_after': event['global_best_loss_after'], 'classification': event['classification'],
                              'local_gain': event['parent_gain'], 'grant': event['development_grant_awarded'],
                              'tokens': record['usage_after']['known_tokens'], 'valid': event['valid'],
                              'node_id': identifier, 'code': record['node']['code'],
                              'new_direction': event['new_direction'], 'direction': event['direction_id']})
        unit_values = [u['reward_per_1000_tokens'] for u in state.adaptive_units]
        probs = [d['evidence']['p_develop'] for d in state.decisions]
        final_pool = state.pool.values()
        row = {'job_id': job['job_id'], 'arm': job['arm_id'], 'block': job['data_block'],
               'status': result['status'], 'test_gap_percent': 100*test['primary_test_gap'],
               'initial_test_gap_percent': 100*test['seed_test_gap'],
               'improvement_vs_seed_pp': 100*(test['seed_test_gap']-test['primary_test_gap']),
               'validation_gap_percent': 100*state.best['evaluation']['loss'],
               'tokens': result['summary']['known_tokens'], 'requests': result['summary']['request_count'],
               'wall_seconds': result['summary']['wall_seconds'], 'slots': len(events),
               'valid': sum(e['valid'] for e in events), 'explore_slots': sum(e['action']=='explore' for e in events),
               'develop_slots': sum(e['action']=='develop' for e in events),
               'entries_created': len(admissions), 'entries_created_with_positive_grant': len(granted),
               'zero_grant_entries_created': len(admissions)-len(granted),
               'parentless_explore_granted_entries': len(parentless),
               'lagging_granted_entries': len(lagging_admissions),
               'parentless_entries_later_directly_developed': sum(any(e['parent_id']==n for e in events) for n in root_admitted),
               'branch_slots': sum(e['branch_development'] for e in events),
               'ordinary_B_slots': sum(e['branch_development'] and not e['protected_development'] for e in events),
               'protected_slots': len(protected), 'protected_valid': sum(e['valid'] for e in protected),
               'lagging_protected_slots': len(lagging), 'lagging_protected_valid': sum(e['valid'] for e in lagging),
               'lagging_protected_local_improvements': sum(e['local_improvement'] for e in lagging),
               'lagging_protected_global_improvements': sum(e['global_improvement'] for e in lagging),
               'best_has_protected_ancestor': any(e['node_id'] in ancestors for e in protected),
               'best_has_lagging_protected_ancestor': any(e['node_id'] in ancestors for e in lagging),
               'unspent_protected_slots': sum(e['protection_remaining'] for e in final_pool),
               'unspent_B_eligibility_in_pool': sum(e['remaining'] for e in final_pool),
               'zero_remaining_entries_at_end': sum(e['remaining']==0 for e in final_pool),
               'adaptive_units': len(state.adaptive_units),
               'adaptive_units_with_positive_reward': sum(v is not None and v>0 for v in unit_values),
               'p_develop_min': min(probs), 'p_develop_max': max(probs),
               'p_develop_nondefault_slots': sum(abs(p-.5)>1e-12 for p in probs) if job['arm_id']=='AD_P' else 0,
               'max_attempt_chain_depth': max(attempt_depth.values(), default=0),
               'max_consecutive_parent_improvement_depth': max(success_depth.values(), default=0),
               'evaluation_wall_seconds': sum(n['evaluation']['wall_seconds'] for n in nodes),
               'evaluation_cpu_seconds': sum(n['evaluation']['cpu_seconds'] for n in nodes),
               'instance_evaluation_attempts': sum(n['evaluation']['instance_evaluations'] for n in nodes)}
        rows.append(row)
        demo.append({'job': job, 'summary': row, 'best_id': selection['best_id'], 'seed_id': selection['seed_best_id'],
                     'seeds': [{'id': n['id'], 'code': n['code'], 'loss': n['evaluation']['loss']} for n in checkpoint['seeds']],
                     'steps': decisions})
    return rows, curves, same_state, demo


def plots(rows, curves, primary, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    directory.mkdir(parents=True, exist_ok=True)
    colors = dict(zip(ARMS, ['#444444', '#aa7733', '#227c9d', '#ee6c4d', '#457b9d', '#775da6']))
    plt.rcParams.update({'font.size': 10, 'figure.dpi': 140, 'savefig.bbox': 'tight'})
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for block in sorted({r['block'] for r in rows}):
        values = [next(r['test_gap_percent'] for r in rows if r['block']==block and r['arm']==a) for a in ARMS]
        ax.plot(ARMS, values, color='#cccccc', lw=.8, zorder=1)
    for index, arm in enumerate(ARMS):
        values = [r['test_gap_percent'] for r in rows if r['arm']==arm]
        ax.scatter([index]*len(values), values, c=colors[arm], s=24, alpha=.75, zorder=2)
        ax.scatter(index, statistics.fmean(values), marker='_', s=380, color='black', zorder=3)
    ax.set(ylabel='Test optimality gap (%)', title='TSP14: eight blocks per arm; black bars = means')
    ax.grid(axis='y', alpha=.2); fig.savefig(directory/'test_by_block.png');fig.savefig(directory/'test_by_block.svg');plt.close(fig)
    fig, ax = plt.subplots(figsize=(8, 4.3))
    names = list(primary['contrasts'])
    for i,name in enumerate(names):
        contrast = primary['contrasts'][name]; v = contrast['paired_difference_pp']
        points = [p['difference_percentage_points'] for p in contrast['pairs']]
        ax.scatter(points, [i]*len(points), color='#b9c4d0', s=17, zorder=1)
        ax.errorbar(v['mean'], i, xerr=[[v['mean']-v['lower']], [v['upper']-v['mean']]], fmt='o', color='#23395b', capsize=4)
    ax.set_yticks(range(len(names)),[n.replace('_minus_', ' − ') for n in names]); ax.invert_yaxis()
    ax.axvline(0,color='gray',lw=1); ax.set(xlabel='Paired difference (percentage points; negative favors left)',
                                         title='95% descriptive block-bootstrap intervals; no confirmatory tests')
    fig.savefig(directory/'paired_differences.png');fig.savefig(directory/'paired_differences.svg');plt.close(fig)
    fig, axes = plt.subplots(1,2,figsize=(11,4))
    for arm in ARMS:
        runs = [[c for c in curves if c['job_id']==r['job_id']] for r in rows if r['arm']==arm]
        axes[0].plot(range(33),[100*statistics.fmean(run[k]['validation_gap'] for run in runs) for k in range(33)],
                     color=colors[arm],label=arm)
        grid = np.arange(0,300001,5000)
        values = [[next(c['validation_gap'] for c in reversed(run) if c['tokens']<=t) for t in grid] for run in runs]
        axes[1].plot(grid/1000,np.mean(values,axis=0)*100,color=colors[arm],label=arm)
    axes[0].set(xlabel='Completed proposal slots',ylabel='Mean best validation gap (%)')
    axes[1].set(xlabel='Known tokens (thousands)',ylabel='Mean best validation gap (%)')
    axes[0].set_title('Search progress (validation; not held-out quality)')
    axes[1].set_title('Last incumbent carried forward after search termination')
    for ax in axes: ax.grid(alpha=.2)
    axes[1].legend(ncol=2,fontsize=8);fig.tight_layout();fig.savefig(directory/'validation_efficiency.png');fig.savefig(directory/'validation_efficiency.svg');plt.close(fig)
    for path in directory.glob('*.svg'):
        path.write_text('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8',newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    primary = read_json(args.output/'S3_ANALYSIS.json')
    assert primary['jobs_tested']==primary['jobs_planned']==48
    rows,curves,same_state,demo=collect(args.study)
    write_csv(args.output/'RUN_METRICS.csv',rows);write_csv(args.output/'VALIDATION_CURVES.csv',curves)
    write_csv(args.output/'SAME_STATE_NEXT_DECISION.csv',same_state)
    save_json(args.output/'REPLAY_DATA.json',{'version':'S3 r3','historical_replay':True,'new_model_calls':0,'runs':demo})
    groups={}
    for arm in ARMS:
        selected=[r for r in rows if r['arm']==arm]
        keys=[k for k,v in selected[0].items() if isinstance(v,(int,float,bool)) and k not in ['block']]
        groups[arm]={'n':len(selected),'sum':{k:sum(r[k] for r in selected) for k in keys},
                     'mean':{k:statistics.fmean(r[k] for r in selected) for k in keys}}
    # s3_tsp is a sibling of s3_tsp_r3.
    studies=[args.study.parents[2]/'s3_tsp'/'studies'/f's3-tsp-minimax-strategy-screen-20260927-r{i}' for i in (1,2)]+[args.study]
    ledger=[cost(p) for p in studies]
    save_json(args.output/'COST_LEDGER.json',{'created_utc':utcnow(),'new_model_calls':0,'studies':ledger})
    report={'created_utc':utcnow(),'new_model_calls':0,'analysis_role':'post-freeze descriptive mechanism audit',
            'groups':groups,'same_state':{'definition':'Disable priority in copies of real FB_P pre-decision states; no alternate model generation or outcomes.',
            'histories':len(same_state),'protected_histories':sum(r['protected'] for r in same_state),
            'generation_decision_changes':sum(r['generation_decision_changes'] for r in same_state),
            'branch_identity_changes':sum(r['branch_identity_changes'] for r in same_state)}}
    save_json(args.output/'SUPPLEMENTAL_ANALYSIS.json',report)
    plots(rows,curves,primary,args.output/'figures')
    print(json.dumps({'jobs':len(rows),'same_state':report['same_state'],'new_model_calls':0}))


if __name__=='__main__':main()
