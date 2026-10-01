"""Verify delivered bytes and raw call accounting, without evaluation/model access."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):return json.loads(p.read_bytes())
def verify(root):
    for name,rec in read(root/'DELIVERY_HASHES.json')['files'].items():
        b=(root/name).read_bytes();assert sha(b)==rec['sha256'] and len(b)==rec['bytes'],name
    idx=read(root/'evidence/HASHES.json')['files']
    with zipfile.ZipFile(root/'opencode-rerun-evidence.zip') as z:
        assert z.testzip() is None and len(z.infolist())==len(idx)==71
        assert set(z.namelist())=={r['path'] for r in idx}
        for rec in idx:
            b=z.read(rec['path']);assert sha(b)==rec['sha256'] and len(b)==rec['bytes']
        manifest=json.loads(z.read('run/RUN_MANIFEST.json'))
        assert len(manifest['prefix_jobs'])==8
        for r in manifest['data']:
            data=z.read('run/'+r['path']);assert sha(data)==r['sha256']
            assert 'test' not in json.loads(data) and r['role']=='search'
        cp=json.loads(z.read('run/CHECKPOINT_MANIFEST.json'))
        assert len(cp['records'])==16 and all(r['status']=='preparation_incomplete' for r in cp['records'])
        search_states=[n for n in z.namelist() if '/calls/' in n and n.endswith('/state.json')]
        assert len(search_states)==5
        unknown=known=responses=0
        for n in search_states:
            st=json.loads(z.read(n));folder=n.rsplit('/',1)[0]
            req=json.loads(z.read(folder+'/request.json'));assert req['model']=='MiniMax-M3'
            if st['status']=='response_persisted':
                r=json.loads(z.read(folder+'/response.json'));known+=r['input_tokens']+r['output_tokens'];responses+=1
                assert folder+'/raw_response.json' in z.namelist() and r['returned_model']=='MiniMax-M3'
            else:assert st['status']=='sent_unknown';unknown+=1
        assert (known,responses,unknown)==(15152,4,1)
        candidates=[n for n in z.namelist() if n.endswith('/candidate.json')]
        assert len(candidates)==2 and all(json.loads(z.read(n))['evaluation']['valid'] for n in candidates)
    sup=read(root/'supplement/ARCHIVE_INDEX.json')
    assert sha((root/'supplement/acceptance-and-source.zip').read_bytes())==sup['zip_sha256']
    with zipfile.ZipFile(root/'supplement/acceptance-and-source.zip') as z:
        assert z.testzip() is None and len(z.infolist())==len(sup['files'])==28
        for name,rec in sup['files'].items():
            b=z.read(name);assert sha(b)==rec['sha256'] and len(b)==rec['bytes']
        states=[n for n in z.namelist() if n.endswith('/state.json')]
        assert len(states)==6
        acceptance_tokens=0
        for n in states:
            assert json.loads(z.read(n))['status']=='response_persisted'
            r=json.loads(z.read(n.replace('state.json','response.json')))
            assert r['returned_model']=='MiniMax-M3' and r['usage_complete']
            acceptance_tokens+=r['input_tokens']+r['output_tokens']
        assert acceptance_tokens==16909
        p=json.loads(z.read('probe.json'))
        assert p['usage']['input_tokens']+p['usage']['output_tokens']==591
    return {'passed':True,'archive_members_verified':99,'search_requests':5,'search_responses':4,
        'acceptance_requests':6,'probe_requests':1,'all_requests':12,'known_tokens':32652,'unknown_requests':1,
        'completed_proposals':2,'ready_checkpoints':0,'test_executed':False,'new_model_calls':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);a=p.parse_args()
    print(json.dumps(verify(a.directory)))
