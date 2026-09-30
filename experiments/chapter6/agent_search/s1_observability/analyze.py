from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import statistics
from chapter6_demo.v12_2.common import read_json,save_json,utcnow,digest
from chapter6_demo.v12_2.runner import restore
from chapter6_demo.v12_2.test_stage import evaluate_frozen
from .study import verify,TERMINAL


def analyze(study, output):
    study,output=Path(study),Path(output); m=verify(study,frozen=True)
    rows=[]; events=[]; calls=[]
    for j in m['jobs']:
        run=study/'runs'/j['job_id']; status=read_json(run/'status.json') if (run/'status.json').exists() else {}
        summary=(read_json(run/'search_result.json').get('summary',{}) if (run/'search_result.json').exists() else status.get('summary',{}))
        usage=(read_json(run/'search_result.json').get('usage',{}) if (run/'search_result.json').exists() else status.get('usage',{}))
        test=read_json(study/'tests'/f"{j['job_id']}.json") if (study/'tests'/f"{j['job_id']}.json").exists() else {}
        row={**{k:j[k] for k in ('job_id','controller','data_block','search_seed_label','steps')},
             'status':status.get('status'),'completed_proposals':summary.get('completed_proposals'),
             'valid_generated':summary.get('valid_generated'),'branch_admissions':summary.get('branch_admissions'),
             'branch_development_attempts':summary.get('branch_development_attempts'),
             'parent_improving_branch_children':summary.get('parent_improving_branch_children'),
             'branch_development_successful_extensions':summary.get('branch_development_successful_extensions'),
             'branch_development_max_attempt_depth':summary.get('branch_development_max_attempt_depth'),
             'branch_development_max_success_depth':summary.get('branch_development_max_success_depth'),
             'multi_branch_slots':summary.get('multi_branch_slots'),
             'family_differentiated_branch_slots':summary.get('family_differentiated_branch_slots'),
             'full_vs_gain_choice_differences':summary.get('full_vs_gain_choice_differences'),
             'model_calls':summary.get('model_calls'), 'known_tokens':usage.get('known_tokens'),
             'usage_complete':usage.get('usage_complete'), 'test_gap':test.get('primary_test_gap'),
             'test_valid':test.get('primary_test_valid'), 'seed_test_gap':test.get('seed_validation_selected_test_gap')}
        rows.append(row)
        if (run/'checkpoint.json').exists():
            cp=read_json(run/'checkpoint.json')
            events.extend([{**e,'job_id':j['job_id'],'controller':j['controller'],'block':j['data_block'],'seed':j['search_seed_label']} for e in cp['records']])
            for folder in (run/'calls').glob('*') if (run/'calls').exists() else []:
                if (folder/'request.json').exists():
                    req=read_json(folder/'request.json'); resp=read_json(folder/'response.json') if (folder/'response.json').exists() else {}
                    calls.append({'job_id':j['job_id'],'step':req['step'],'stage':req['stage'],'max_tokens':req['max_tokens'],'input_tokens':resp.get('input_tokens'),'output_tokens':resp.get('output_tokens'),'seconds':resp.get('seconds'),'status':read_json(folder/'state.json').get('status')})
    complete=[r for r in rows if r['status']=='search_complete_test_not_run']
    pair_rows=[]
    for block in m['protocol']['blocks']:
        for seed in m['protocol']['search_seeds']:
            pair=[r for r in rows if r['data_block']==block and r['search_seed_label']==seed]
            if len(pair)==2:
                f=next((r for r in pair if r['controller']=='niche_fixed_dev'),None); rel=next((r for r in pair if r['controller']=='relational_branch'),None)
                pair_rows.append({'block':block,'seed':seed,'fixed_status':f['status'],'relation_status':rel['status'],'fixed_test_gap':f['test_gap'],'relation_test_gap':rel['test_gap'],'delta_pp':None if f['test_gap'] is None or rel['test_gap'] is None else 100*(rel['test_gap']-f['test_gap'])})
    output.mkdir(parents=True,exist_ok=True)
    summary={'study_id':m['protocol']['study_id'],'manifest_sha256':m['manifest_sha256'],'analyzed_utc':utcnow(),'new_model_calls_by_analysis':0,'jobs':rows,'pairs':pair_rows,'event_count':len(events),'call_count':len(calls),'complete_searches':len(complete),'claim':'S1 natural observability screening; no superiority or causal family-ranking claim.'}
    save_json(output/'summary.json',summary,immutable=True)
    for name,values in [('jobs',rows),('pairs',pair_rows),('events',events),('calls',calls)]:
        if not values: continue
        with (output/f'{name}.csv').open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=sorted({k for x in values for k in x}));writer.writeheader();writer.writerows(values)
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--study',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps({'jobs':len(analyze(a.study,a.output)['jobs'])}))
if __name__=='__main__':main()
