"""Recount every new call and preserve executed source without more API calls."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[4]
BASE=Path('C:/Users/67473/Desktop/5/phase_b_public_prefix_20260930/local-opencode-acceptance')
EXECUTED='3781365526e20840c0aba329e9413e31d162c4e4'

def sha(data): return hashlib.sha256(data).hexdigest()
def read(path): return json.loads(path.read_bytes())
def write(path,obj): path.write_bytes((json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode())

def main():
    out=HERE/'supplement'
    out.mkdir(exist_ok=True)
    probe=(BASE/'probe.json').read_bytes()
    (out/'PROBE.json').write_bytes(probe)
    acc=BASE/'full-20261002'
    source_rel=(HERE/'run_prefix.py').relative_to(ROOT).as_posix()
    executed=subprocess.check_output(['git','show',EXECUTED+':'+source_rel],cwd=ROOT)
    payload={'executed/run_prefix.py':executed,'probe.json':probe}
    for path in acc.rglob('*'):
        if path.is_file(): payload['acceptance/'+path.relative_to(acc).as_posix()]=path.read_bytes()
    for name,data in payload.items():
        assert b'Bearer ' not in data and b'Authorization: ' not in data, name
    with zipfile.ZipFile(out/'acceptance-and-source.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name,data in sorted(payload.items()):z.writestr(name,data)
    index={n:{'bytes':len(b),'sha256':sha(b)} for n,b in sorted(payload.items())}
    write(out/'ARCHIVE_INDEX.json',{'files':index,'zip_sha256':sha((out/'acceptance-and-source.zip').read_bytes())})
    summary=read(acc/'summary.json')
    cases=[]
    for phase,folder in [('acceptance',acc),('search',HERE/'evidence/run/prefix_runs/prefix-sp-b65')]:
        for p in sorted((folder/'calls').glob('*/state.json')):
            state=read(p); response_path=p.with_name('response.json')
            response=read(response_path) if response_path.exists() else {}
            cases.append({'phase':phase,'call':p.parent.name,'state':state['status'],
                'known_tokens':response['input_tokens']+response['output_tokens'] if response.get('usage_complete') else None,
                'returned_model':response.get('returned_model')})
    p=json.loads(probe)
    cases.append({'phase':'short_probe','call':'single','state':'response_returned_summary_only',
        'known_tokens':p['usage']['input_tokens']+p['usage']['output_tokens'],'returned_model':p['usage']['model']})
    assert len(cases)==12 and sum(r['known_tokens'] or 0 for r in cases)==32652
    manifest=read(HERE/'evidence/run/RUN_MANIFEST.json')
    audit={'status':'halted_incomplete_with_execution_deviations','calls':cases,'total_requests':12,
        'known_tokens_lower_bound':32652,'unknown_requests':1,'total_tokens':None,
        'search_requests':5,'search_known_tokens':15152,'acceptance_requests':6,'acceptance_tokens':16909,
        'probe_requests':1,'probe_tokens':591,'full_experiment_completed':False,
        'interface':'Python HTTP transport reading local OpenCode configuration and credentials; OpenCode CLI not used',
        'prepared_source_commit':manifest['source_commit'],'executed_launcher_commit':EXECUTED,
        'manifest_source_matches_executed_launcher':False,'executed_launcher_sha256':sha(executed),
        'limitations':['manifest source not re-frozen after startup repair',
            'fresh 3+3 acceptance was completed but not hash-bound by run manifest; only historical acceptance copied there',
            'short probe saved parsed output and usage only, not raw HTTP envelope',
            'short probe and acceptance budgets were not enumerated before calls; actual costs now fully reported',
            'runner written for SP prefixes only; does not implement the requested complete continuation experiment',
            'reused blocks 60-67; this is not a new independent data sample',
            'sent_unknown is an in-flight marker until transport terminates; prior progress message declared failure too early',
            'failure near 180-second timeout supports a timeout suspicion, not a confirmed provider error category'],
        'post_run_guard_checks':['saved halt rejects before transport','changed source commit rejects before transport',
            'changed manifest digest rejects before transport'],'post_run_guard_model_calls':0,
        'continuation_executed':False,'test_executed':False,'new_calls_during_supplement':0}
    write(out/'AUDIT.json',audit)
    print(json.dumps({'requests':12,'known_tokens':32652,'unknown_requests':1,'archive_members':len(payload)}))

if __name__=='__main__':main()
