"""Reexecute all held-out readouts against their archived TSP14 coordinates."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

from chapter6_demo.v12_2.common import read_json,save_json,file_sha,digest
from .s3_evidence import close
from .s3_tsp_r3.evaluator import evaluate_test


def one(args):
    study,job=args;study=Path(study)
    directory=study/'runs'/job['job_id'];readout=read_json(directory/'selection_frozen.json')
    test=read_json(study/'tests'/(job['job_id']+'.json'))
    snapshot=read_json(study/'data'/f"test-b{job['data_block']}.json")
    assert file_sha(directory/'selection_frozen.json')==test['binding']['readout_sha256']
    assert digest(snapshot)==test['binding']['test_snapshot_sha256']
    programs={p['id']:p for p in readout['programs']}
    for role,key in [('best','best_id'),('seed','seed_best_id')]:
        original=test['evaluations'][role];fresh=evaluate_test(programs[readout[key]]['code'],snapshot)
        for field in ['valid','loss','per_instance_loss','per_instance_value','behavior','trajectory_behavior',
                      'trajectory_values','solutions','family_loss','program_identity','data_snapshot_sha256','evaluated_instance_ids']:
            assert close(original[field],fresh[field]),f"{job['job_id']}:{role}:{field}"
        assert all(sorted(route)==list(range(14)) for route in fresh['solutions'])
    return {'job_id':job['job_id'],'status':'passed','program_evaluations':2,'instance_evaluations':120}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();m=read_json(a.study/'manifest.json')
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        rows=list(pool.map(one,[(str(a.study),j) for j in m['jobs']]))
    report={'status':'passed','jobs':len(rows),'program_evaluations':sum(r['program_evaluations'] for r in rows),
            'instance_evaluations':sum(r['instance_evaluations'] for r in rows),'rows':rows,'new_model_calls':0,
            'numerics':'absolute and relative tolerance 1e-12; exact routes and identities'}
    save_json(a.output,report);print(json.dumps({k:v for k,v in report.items() if k!='rows'}))


if __name__=='__main__':main()
