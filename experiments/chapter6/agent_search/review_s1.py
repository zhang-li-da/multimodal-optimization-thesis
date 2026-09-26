"""Independent offline audit of S1 bytes, decisions, costs and numerical results."""
from __future__ import annotations
import argparse
import base64
from collections import Counter
import copy
import csv
import json
import math
from pathlib import Path
import statistics

import numpy as np
from chapter6_demo.discovery import planner_prompt,coder_prompt
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_1_controller import V121SearchState
from chapter6_demo.v12_2.calls import decode_response
from chapter6_demo.v12_2.common import digest,read_json,save_json,source_record,file_sha
from chapter6_demo.v12_2.data import evaluate_search,evaluate_test
from chapter6_demo.v12_2.runner import plain,restore,branch_exposure
from chapter6_demo.agent_search.directions import describe,lineage


def csv_write(path,rows):
    if not rows:return
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=sorted({k for r in rows for k in r}),lineterminator=chr(10));w.writeheader();w.writerows(rows)


def moments(values):
    return {'n':len(values),'mean':statistics.mean(values) if values else None,
        'min':min(values) if values else None,'max':max(values) if values else None,
        'median':statistics.median(values) if values else None}


def numeric_compare(actual,expected):
    # Categorical paths and identity exact; finite numbers use predeclared tolerance.
    keys=('valid','loss','per_instance_loss','per_instance_value','family_loss','behavior',
          'trajectory_behavior','trajectory_values','solutions','features_called','local_checks','program_identity')
    issues=[];max_error=0.
    def check(a,b,path):
        nonlocal max_error
        if type(a) in (int,float) and type(b) in (int,float):
            if any(k in path for k in ('behavior','solutions','features_called','local_checks')):
                if a!=b:issues.append(path)
            else:
                max_error=max(max_error,abs(a-b))
                if not math.isclose(a,b,abs_tol=1e-12,rel_tol=1e-10):issues.append(path)
        elif isinstance(a,list) and isinstance(b,list):
            if len(a)!=len(b):issues.append(path+'.length')
            for i,(x,y) in enumerate(zip(a,b)):check(x,y,path+'.'+str(i))
        elif isinstance(a,dict) and isinstance(b,dict):
            if set(a)!=set(b):issues.append(path+'.keys')
            for k in a.keys()&b.keys():check(a[k],b[k],path+'.'+k)
        elif a!=b:issues.append(path)
    for k in keys:
        if k in actual or k in expected:check(actual.get(k),expected.get(k),k)
    return issues,max_error


def audit_run(study,job,numeric=False):
    study=Path(study);run=study/'runs'/job['job_id']
    if not (run/'checkpoint.json').exists():return [],[],[],{'unstarted':True}
    cp=read_json(run/'checkpoint.json');state=restore(cp)
    nodes=cp['seeds']+[r['node'] for r in cp['records']]
    lineage_ids=lineage(nodes)
    calls=[];records=[];descriptors=[]
    numerical=[]
    # Byte-level response redecoding, no model or credentials.
    for folder in sorted((run/'calls').glob('*')):
        req=read_json(folder/'request.json');st=read_json(folder/'state.json') if (folder/'state.json').exists() else {}
        resp=read_json(folder/'response.json') if (folder/'response.json').exists() else {}
        body={}
        if (folder/'raw_response.json').exists():
            raw=read_json(folder/'raw_response.json')
            assert raw['request_sha256']==digest(req) and raw['envelope_sha256']==digest(raw['envelope'])
            assert resp=={'request_sha256':digest(req),**decode_response(raw['envelope'])}
            body=json.loads(base64.b64decode(raw['envelope']['body_base64']))
        finish=(body.get('choices') or [{}])[0].get('finish_reason')
        text=resp.get('text','')
        calls.append({'job_id':job['job_id'],'block':job['data_block'],'seed':job['search_seed_label'],
             'controller':job['controller'],'step':req['step'],'stage':req['stage'],
             'finish_reason':finish,'state':st.get('status'),'requested_model':req['model'],
             'returned_model':resp.get('returned_model'),'input_tokens':resp.get('input_tokens'),
             'output_tokens':resp.get('output_tokens'),'seconds':resp.get('seconds'),
             'usage_complete':resp.get('usage_complete'),'max_tokens':req['max_tokens'],
             'unclosed_thought':text.count('<think>')!=text.count('</think>')})
    replay=V121SearchState('tsp',job['controller'],job['search_seed'])
    for node in cp['seeds']:replay.observe(copy.deepcopy(node))
    for step,record in enumerate(cp['records']):
        comparison={}
        for method in ('niche_fixed_dev','relational_branch'):
            branch=copy.deepcopy(replay);branch.method=method
            chosen=plain(copy.deepcopy(branch.choose(step)))
            comparison[method]=chosen
        actual=plain(copy.deepcopy(replay.choose(step)))
        assert plain(actual)==record['decision']
        assert plain(branch_exposure(replay,actual))==record['exposure']
        exp=record['exposure'];entries=exp['entries'];event=record['event']
        fifo=entries[0]['node_id'] if entries else None
        q=[e['family_priority'] for e in entries]
        gain=[.25*e['gain_term'] for e in entries]
        qp=planner_prompt('tsp',actual,step)
        saved_req=read_json(run/'calls'/f'{step:03d}-planner'/'request.json')
        assert saved_req['prompt']==qp
        # Treatment labels never go to the model; differing selected programs may.
        assert 'niche_fixed_dev' not in qp and 'relational_branch' not in qp
        normal_equal=comparison['niche_fixed_dev']==comparison['relational_branch']
        if not actual['branch_development_scheduled']:assert normal_equal
        r={'job_id':job['job_id'],'controller':job['controller'],'block':job['data_block'],'seed':job['search_seed_label'],
           'step':step,'node_id':record['node']['id'],'parent_id':record['node']['parent_id'],
           'valid':event['valid'],'classification':event['branch_classification'],
           'admitted':event['branch_admitted'],'branch_development':event['branch_parent_development'],
           'eligible_count':len(entries),'multi_branch':exp['multi_branch_slot'],
           'family_differentiated':exp['branch_slot'] and exp['family_priority_varies'],
           'full_vs_fifo_differs':bool(exp['branch_slot'] and exp['full_rank_parent_id']!=fifo),
           'full_vs_gain_differs':exp['full_vs_gain_choice_differs'],
           'actual_matches_fifo':not exp['branch_slot'] or event['branch_parent_id']==fifo,
           'same_history_FR_selection_differs':not normal_equal,
           'same_history_FR_prompt_differs':planner_prompt('tsp',comparison['niche_fixed_dev'],step)!=planner_prompt('tsp',comparison['relational_branch'],step),
           'parent_improved':event['parent_improved'],'global_improved':event['improved'],
           'parent_gain':event['parent_improvement_margin'],'global_gain':event['global_improvement_margin'],
           'attempt_depth':event['branch_attempt_depth'],'success_depth':event['branch_success_depth'],
           'family_q_span':max(q)-min(q) if q else 0.,'weighted_gain_span':max(gain)-min(gain) if gain else 0.,
           'family_fallback_count':sum(e['family_fallback'] for e in entries),
           'family_observations_min':min((e['family_observations'] for e in entries),default=None),
           'best_validation_gap':min(n['evaluation']['loss'] for n in replay.nodes if n['evaluation']['valid']),
           'test_data_used':False}
        replay.observe(copy.deepcopy(record['node']));assert plain(replay.events[-1])==record['event']
        r['best_validation_gap_after']=replay.best
        records.append(r)
    for n in nodes:
        desc=describe(n['code'])
        descriptors.append({'job_id':job['job_id'],'node_id':n['id'],'parent_id':n.get('parent_id'),
            'lineage_id':lineage_ids[n['id']], 'validation_gap':n['evaluation'].get('loss'),
            'behavior_hash':digest(n['evaluation'].get('behavior')) if n['evaluation']['valid'] else None,
            'reported_tags':n.get('reported_tags',n['tags']), 'allocated_tag':n.get('allocated_tag'),**desc})
    if numeric:
        search_path=study/'data'/f"search-b{job['data_block']}.json"
        snapshot=read_json(search_path)
        for n in nodes:
            evaluated=evaluate_search(n['code'],snapshot)
            issues,error=numeric_compare(evaluated,n['evaluation'])
            numerical.append({'job_id':job['job_id'],'node':n['id'],'split':'validation_plus_probe',
                'passed':not issues,'max_abs_error':error,'issues':issues})
        tpath=study/'tests'/f"{job['job_id']}.json"
        if tpath.exists():
            tested=read_json(tpath);snap=read_json(study/'data'/f"test-b{job['data_block']}.json")
            frozen=read_json(run/'selection_frozen.json')
            for n in frozen['programs']:
                evaluated=evaluate_test(n['code'],snap)
                issues,error=numeric_compare(evaluated,tested['evaluations'][str(n['id'])])
                numerical.append({'job_id':job['job_id'],'node':n['id'],'split':'test',
                    'passed':not issues,'max_abs_error':error,'issues':issues})
    return records,calls,descriptors,{'numerical':numerical,'restored_state':True,'prompt_replay':True}


def audit(study,output,numeric=False):
    study,output=Path(study),Path(output);m=read_json(study/'manifest.json')
    assert digest({k:v for k,v in m.items() if k!='manifest_sha256'})==m['manifest_sha256']
    assert m['source']==source_record(), 'Imported runtime source drift'
    # Do not run test analysis until the entire manifest is terminal or halted.
    statuses={j['job_id']:(read_json(study/'runs'/j['job_id']/'status.json') if (study/'runs'/j['job_id']/'status.json').exists() else {}) for j in m['jobs']}
    if not all(s.get('status') in ('search_complete_test_not_run','infrastructure_incomplete') for s in statuses.values()) and not (study/'dispatch/halt.json').exists():
        raise ValueError('Finish or explicitly halt before analysis')
    events=[];calls=[];directions=[];numerics=[];rows=[]
    for job in m['jobs']:
        e,c,d,v=audit_run(study,job,numeric)
        events+=e;calls+=c;directions+=d;numerics+=v.get('numerical',[])
        tpath=study/'tests'/f"{job['job_id']}.json"
        t=read_json(tpath) if tpath.exists() else {}
        values=[x for x in c if x['usage_complete']]
        rows.append({'job_id':job['job_id'],'controller':job['controller'],'block':job['data_block'],'seed':job['search_seed_label'],
            'status':statuses[job['job_id']].get('status','not_started'),'completed_proposals':len(e),
            'valid_candidates':sum(r['valid'] for r in e),'admissions':sum(r['admitted'] for r in e),
            'development_attempts':sum(r['branch_development'] for r in e),
            'multi_branch_slots':sum(r['multi_branch'] for r in e),
            'differentiated_family_slots':sum(r['family_differentiated'] for r in e),
            'full_vs_fifo_choice_differences':sum(r['full_vs_fifo_differs'] for r in e),
            'full_vs_gain_choice_differences':sum(r['full_vs_gain_differs'] for r in e),
            'parent_improving_dev_children':sum(r['branch_development'] and r['parent_improved'] for r in e),
            'global_improving_dev_children':sum(r['branch_development'] and r['global_improved'] for r in e),
            'calls':len(c),'known_tokens':sum(r['input_tokens']+r['output_tokens'] for r in values),
            'usage_complete':len(values)==len(c),'api_seconds':sum(r['seconds'] or 0 for r in c),
            'test_gap':t.get('primary_test_gap'),'seed_test_gap':t.get('seed_validation_selected_test_gap'),
            'test_valid':t.get('primary_test_valid'),'max_success_depth':max((r['success_depth'] or 0 for r in e),default=0),
            'output_truncations':sum(r['finish_reason']=='length' for r in c)})
    pairs=[]
    for block in m['protocol']['blocks']:
        for seed in m['protocol']['search_seeds']:
            f,r= [next(v for v in rows if v['block']==block and v['seed']==seed and v['controller']==control) for control in m['protocol']['controllers']]
            pairs.append({'block':block,'seed':seed,'fifo_test_gap':f['test_gap'],'ranked_test_gap':r['test_gap'],
                'delta_pp':100*(r['test_gap']-f['test_gap']) if f['test_gap'] is not None and r['test_gap'] is not None else None})
    block_means=[statistics.mean(v['delta_pp'] for v in pairs if v['block']==b) for b in m['protocol']['blocks']
                 if all(v['delta_pp'] is not None for v in pairs if v['block']==b)]
    ci=None
    if len(block_means)>=2:
        rng=np.random.default_rng(9260101);x=np.array(block_means)
        boots=x[rng.integers(0,len(x),(20000,len(x)))].mean(axis=1)
        ci=list(map(float,np.quantile(boots,[.025,.975])))
    group=[]
    for controller in m['protocol']['controllers']:
        rs=[r for r in rows if r['controller']==controller]
        group.append({'controller':controller,'runs':len(rs),
            'test_gap':moments([r['test_gap'] for r in rs if r['test_gap'] is not None]),
            'known_tokens':sum(r['known_tokens'] for r in rs),
            'runs_with_multi_branch':sum(r['multi_branch_slots']>0 for r in rs),
            **{k:sum(r[k] for r in rs) for k in ('completed_proposals','valid_candidates','admissions','development_attempts',
                 'multi_branch_slots','differentiated_family_slots','full_vs_fifo_choice_differences','full_vs_gain_choice_differences',
                 'parent_improving_dev_children','global_improving_dev_children','calls','output_truncations')}})
    gates={'each_arm_at_least_half_runs_multi_branch':all(g['runs_with_multi_branch']>=3 for g in group),
        'each_arm_at_least_10_multi_branch_slots':all(g['multi_branch_slots']>=10 for g in group),
        'at_least_5_same_history_FR_or_RG_differences':sum(e['full_vs_fifo_differs'] or e['full_vs_gain_differs'] for e in events)>=5,
        'diagnostic_only_not_quality_pass':True}
    summary={'manifest_sha256':m['manifest_sha256'],'runtime_source_unchanged':True,'new_model_calls':0,
        'group_results':group,'pairs':pairs,'block_mean_deltas_pp':block_means,
        'delta_mean_pp':statistics.mean(block_means) if block_means else None,'block_bootstrap_95_descriptive':ci,
        'observability_gates':gates,'total_calls':len(calls),'total_known_tokens':sum(r['known_tokens'] for r in rows),
        'returned_models':dict(Counter(r['returned_model'] for r in calls)),
        'replayed_decisions':len(events),'ordinary_step_FR_differences':sum(not e['branch_development'] and e['same_history_FR_selection_differs'] for e in events),
        'numerical_evaluations':len(numerics),'numerical_passed':all(r['passed'] for r in numerics) if numerics else None,
        'max_abs_numerical_error':max((r['max_abs_error'] for r in numerics),default=None),
        'posthoc':True,'claims':'Post-hoc diagnostic and descriptive paired quality, not confirmatory causal superiority.'}
    output.mkdir(parents=True,exist_ok=True)
    for name,val in (('run_table',rows),('decisions',events),('calls',calls),('directions',directions),('numerical',numerics),('pairs',pairs)):
        csv_write(output/(name+'.csv'),val)
    save_json(output/'audit.json',summary,immutable=True)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--numeric',action='store_true');a=p.parse_args()
    with offline_only(): s=audit(a.study,a.output,a.numeric)
    print(json.dumps(s,ensure_ascii=True))

if __name__=='__main__':main()
