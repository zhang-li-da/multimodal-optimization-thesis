import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

def sha(b):return hashlib.sha256(b).hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--relative',required=True)
    p.add_argument('--commit',required=True);p.add_argument('--download',type=Path,required=True);a=p.parse_args()
    local=a.root/a.relative
    expected=json.loads((local/'DELIVERY_HASHES.json').read_text(encoding='utf-8'))
    cred=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',text=True,capture_output=True,cwd=a.root,check=True).stdout
    token=dict(line.split('=',1) for line in cred.splitlines() if '=' in line).get('password')
    headers={'Accept':'application/vnd.github.raw+json','User-Agent':'chapter6-fixed-commit-verification'}
    if token:headers['Authorization']='Bearer '+token
    a.download.mkdir(parents=True,exist_ok=True)
    for rel,hashed in expected['files'].items():
        url=f'https://api.github.com/repos/zhang-li-da/multimodal-optimization-thesis/contents/{a.relative}/{rel}?ref={a.commit}'
        with urlopen(Request(url,headers=headers),timeout=60) as response:raw=response.read()
        if sha(raw)!=hashed:raise ValueError('remote digest differs: '+rel)
        path=a.download/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    index=json.loads((a.download/'ARCHIVE_INDEX.json').read_text(encoding='utf-8'))
    assert sha((a.download/index['archive']).read_bytes())==index['sha256']
    with zipfile.ZipFile(a.download/index['archive']) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(set(z.namelist()))==len(index['members'])
        for row in index['members']:
            raw=z.read(row['path']);assert len(raw)==row['bytes'] and sha(raw)==row['sha256']
        manifest=json.loads(z.read('manifest.json'))
        assert len(manifest['jobs'])==128
        assert len([r for r in manifest['files'] if r['path'].startswith('checkpoints/')])==16
        coverage={s:sum(n.endswith(s) for n in z.namelist()) for s in ['/request.json','/response.json','/candidate.json','/selection_candidates.json','/terminal_status.json']}
    analysis=json.loads((a.download/'ANALYSIS.json').read_text(encoding='utf-8'))
    source=json.loads((a.download/'EXECUTION_SOURCE_INDEX.json').read_text(encoding='utf-8'))
    assert sha((a.download/'execution-source.zip').read_bytes())==source['archive_sha256']
    with zipfile.ZipFile(a.download/'execution-source.zip') as z:
        assert z.testzip() is None and len(z.namelist())==len(source['members'])
        for row in source['members']:
            raw=z.read(row['path']);assert len(raw)==row['bytes'] and sha(raw)==row['sha256']
    report={'fixed_remote_commit':a.commit,'files_verified':len(expected['files']),'archive_members_verified':len(index['members']),
            'manifest_sha256':manifest['manifest_sha256'],'zip_integrity':'passed',
            'planned_jobs':128,'planned_checkpoints':16,'coverage':coverage,
            'source_members_verified':len(source['members']),
            'cumulative_cost':analysis['cumulative_cost'],'test_access':False,'all_remote_bytes_match_local':True}
    (local/'REMOTE_VERIFICATION.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
