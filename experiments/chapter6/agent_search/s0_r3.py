"""Fixed output-headroom acceptance, separate from the completed r2 study."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

from chapter6_demo.s0_output_calibration import calibration as s0
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import ROOT,digest,environment,git,read_json,run_lock,save_json,utcnow

HERE=Path(__file__).resolve().parent
PROTOCOL=HERE/'s0_r3.protocol.json'


def source():
    files=s0.source()['files'].copy()
    for name in ('__init__.py','s0_r3.py','s0_r3.protocol.json','test_s0_r3.py'):
        p=HERE/name
        files[p.relative_to(ROOT).as_posix()]=hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    return {'format':'s0-r3-named-lf-sha256','files':files,'sha256':digest(files)}


def jobs():
    values=s0.acceptance_jobs()+s0.e2e_jobs()
    for j in values:
        j['fixture']=s0.fixtures(j['cohort'],j['template'],j['repeat']+60)
        j['config_id']='fixed_headroom'
        if j['repeat']%2:
            f=j['fixture']
            context=s0.fixtures(j['cohort'],'reference_revision',j['repeat']+60)['selection']
            f['selection']['recent']=[context['parent'],context['reference']]
        j['job_id']='r3-'+j['job_id']
    return values


def freeze(output):
    output=Path(output)
    if output.exists() or git('status','--porcelain','--untracked-files=no'):
        raise ValueError('Use new output and committed source')
    m={'schema':'s0-r3-manifest','status':'FROZEN_PENDING_EXECUTION','source_commit':git('rev-parse','HEAD'),
       'source':source(),'environment':environment(),'protocol':read_json(PROTOCOL),'jobs':jobs(),'created_utc':utcnow()}
    m['manifest_sha256']=digest(m)
    save_json(output/'manifest.json',m,immutable=True)
    return {'manifest_sha256':m['manifest_sha256'],'planned_requests_maximum':48,'source_commit':m['source_commit']}


def verify(study,runtime=True):
    m=s0.verify(study)
    if runtime and (m['source']!=source() or m['environment']!=environment() or m['protocol']!=read_json(PROTOCOL)):
        raise ValueError('Frozen source/environment/protocol differs')
    return m


def run(study,*,transport=None,runtime=True):
    study=Path(study)
    m=verify(study,runtime)
    transport=transport or s0.LazyTransport(m['protocol'])
    rows,plans=[],{}
    with run_lock(study):
        if (study/'run_result.json').exists(): return read_json(study/'run_result.json')
        for j in m['jobs']:
            case=j.get('case_id')
            if case and j['role']=='coder' and case not in plans:
                row={k:v for k,v in j.items() if k!='fixture'}
                row.update(dispatched=False,valid=False,complete=False,json_valid=False,schema_valid=False,
                    executable=False,failure=None,validation_reason='skipped_after_invalid_planner')
                save_json(study/'runs'/j['job_id']/'outcome.json',row,immutable=True)
            else:
                row,parsed=s0.run_one(study,m,j,'fixed_headroom',transport,plans.get(case))
                if case and j['role']=='planner' and row['valid']: plans[case]=parsed
            rows.append(row)
            save_json(study/'progress.json',{'outcomes':rows,'completed_slots':len(rows),'planned_slots':48})
            if row.get('failure'): break
        result={'status':'complete' if len(rows)==48 else 'infrastructure_halted',
                'completed_slots':len(rows),'dispatched_requests':sum(r['dispatched'] for r in rows),
                'fixed_config':'fixed_headroom',**s0.gates(rows)}
        save_json(study/'run_result.json',result,immutable=True)
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('freeze'); c.add_argument('--output',type=Path,required=True)
    c=sub.add_parser('run');c.add_argument('--study',type=Path,required=True);c.add_argument('--live',action='store_true',required=True)
    c=sub.add_parser('analyze');c.add_argument('--study',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.command=='run': result=run(a.study)
    else:
        with offline_only(): result=freeze(a.output) if a.command=='freeze' else s0.analyze(a.study,a.output)
    print(json.dumps({k:v for k,v in result.items() if k!='cohorts'}))


if __name__=='__main__': main()
