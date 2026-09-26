"""Build offline S1 replay and figures only from archived complete/failed runs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import statistics

from chapter6_demo.v12_1_controller import V121SearchState
from chapter6_demo.v12_2.common import read_json,save_json,file_sha
from chapter6_demo.v12_2.runner import plain,restore
from chapter6_demo.v12_1.audit_r2 import offline_only

HERE=Path(__file__).resolve().parent


def build(study,audit,output):
    study,audit,output=Path(study),Path(audit),Path(output)
    manifest=read_json(study/'manifest.json');summary=read_json(audit/'audit.json')
    assert manifest['manifest_sha256']==summary['manifest_sha256']
    runs=[]
    for job in manifest['jobs']:
        folder=study/'runs'/job['job_id']
        status=read_json(folder/'status.json') if (folder/'status.json').exists() else {'status':'not_started'}
        r={'job_id':job['job_id'],'controller':job['controller'],'block':job['data_block'],'seed':job['search_seed_label'],
           'status':status['status'],'frames':[],'nodes':[]}
        if (folder/'checkpoint.json').exists():
            cp=read_json(folder/'checkpoint.json');state=restore(cp)
            r['nodes']=cp['seeds']+[x['node'] for x in cp['records']]
            r['frames']=[{'record':x,'step':i} for i,x in enumerate(cp['records'])]
            r['checkpoint_sha256']=file_sha(folder/'checkpoint.json')
            r['source_commit']=manifest['source_commit']
        test=study/'tests'/(job['job_id']+'.json')
        result=folder/'search_result.json'
        r['test_gap']=read_json(test)['primary_test_gap'] if test.exists() else None
        r['tokens']=read_json(result)['usage']['known_tokens'] if result.exists() else None
        runs.append(r)
    data={'schema':'chapter6-S1-replay-1','summary':summary,'runs':runs,'manifest_sha256':manifest['manifest_sha256'],
          'default_selection':'First manifest run, not best result','new_model_calls':0}
    # JSON inside a script element must escape <, >, & to stop raw HTML injection.
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<',chr(92)+'u003c').replace('>',chr(92)+'u003e').replace('&',chr(92)+'u0026')
    template=(HERE/'s1_demo.html').read_text(encoding='utf-8')
    output.mkdir(parents=True,exist_ok=True)
    html=template.replace('__PAYLOAD__',payload)
    (output/'index.html').write_text(html,encoding='utf-8',newline=chr(10))
    save_json(output/'replay_data.json',data,immutable=True)
    return {'runs':len(runs),'frames':sum(len(r['frames']) for r in runs),'html_sha256':file_sha(output/'index.html'),'new_model_calls':0}


def figures(audit,output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import csv
    summary=read_json(Path(audit)/'audit.json')
    rows=list(csv.DictReader((Path(audit)/'run_table.csv').open(encoding='utf-8')))
    fig,ax=plt.subplots(1,2,figsize=(10,4),layout='constrained')
    colors=['#276c9b','#a75b2f']
    pairs=[p for p in summary['pairs'] if p['delta_pp'] is not None]
    for p in pairs:ax[0].plot([0,1],[100*p['fifo_test_gap'],100*p['ranked_test_gap']],color='#acb8bd',lw=1)
    for x,(key,label) in enumerate([('fifo_test_gap','FIFO'),('ranked_test_gap','Ranked')]):
        ax[0].scatter([x]*len(pairs),[100*p[key] for p in pairs],color=colors[x],s=40,zorder=3)
    ax[0].set(xticks=[0,1],xticklabels=['FIFO','Ranked'],ylabel='Test optimality gap (%)',title='All paired runs (lower is better)')
    groups=summary['group_results']
    keys=['development_attempts','multi_branch_slots','differentiated_family_slots','full_vs_gain_choice_differences']
    for x,g in enumerate(groups):
        ax[1].bar([i+x*.36 for i in range(4)],[g[k] for k in keys],width=.34,color=colors[x],label=['FIFO','Ranked'][x])
    ax[1].set(xticks=[i+.18 for i in range(4)],xticklabels=['Develop','Multi-B','q differs','R/G differs'],title='Observed selection opportunities')
    ax[1].legend();fig.suptitle('S1: MiniMax-M3 / 3 blocks / 2 seeds per block / 32 proposals')
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    fig.savefig(output/'paired_quality_and_exposure.png',dpi=170);fig.savefig(output/'paired_quality_and_exposure.svg');plt.close(fig)


def main():
    p=argparse.ArgumentParser();p.add_argument('--study',type=Path,required=True);p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    with offline_only():
        result=build(a.study,a.audit,a.output);figures(a.audit,a.output/'figures')
    print(json.dumps(result))

if __name__=='__main__':main()
