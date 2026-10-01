"""Verify only this publication and its source files; never evaluate any program."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import urllib.request
import zipfile

REPO = 'zhang-li-da/multimodal-optimization-thesis'
REL = 'experiments/chapter6/agent_search/results/phase-b-prefix-review-20261001'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read(path):
    return json.loads(path.read_bytes())

def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def safe_path(name):
    p = PurePosixPath(name.replace(chr(92), '/'))
    if p.is_absolute() or '..' in p.parts or ':' in name:
        raise ValueError('unsafe member/path')
    return p

def fetch(commit, name, target):
    safe_path(name)
    url = f'https://raw.githubusercontent.com/{REPO}/{commit}/{name}'
    request = urllib.request.Request(url, headers={'User-Agent': 'chapter6-artifact-verifier'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

def verify(directory, source_root=None):
    hashes = read(directory/'HASHES.json')
    for name, record in hashes['files'].items():
        safe_path(name)
        data=(directory/name).read_bytes()
        assert sha(data)==record['sha256'] and len(data)==record['bytes'], name
    counts={}
    for name, index in read(directory/'ARCHIVE_INDEX.json')['archives'].items():
        assert sha((directory/name).read_bytes())==index['sha256']
        with zipfile.ZipFile(directory/name) as archive:
            assert archive.testzip() is None
            assert len(archive.infolist())==index['member_count']
            assert len(set(archive.namelist()))==len(archive.infolist())
            for member in archive.namelist(): safe_path(member)
            files={i.filename for i in archive.infolist() if not i.is_dir()}
            assert files == set(index['files'])
            for member, record in index['files'].items():
                safe_path(member)
                data=archive.read(member)
                assert sha(data)==record['sha256'] and len(data)==record['bytes'], member
            counts[name]={'members':len(archive.infolist()),'files':len(files),'all_hashes_match':True}
    with zipfile.ZipFile(directory/'raw-study.zip') as archive:
        names=archive.namelist()
        manifest=json.loads(archive.read('manifest.json'))
        cps=json.loads(archive.read('checkpoint_manifest.json'))
        for obj, field in [(manifest,'manifest_sha256'),(cps,'checkpoint_manifest_sha256')]:
            assert sha(canonical({k:v for k,v in obj.items() if k!=field}))==obj[field]
        assert cps['study_manifest_sha256']==manifest['manifest_sha256']
        assert len(manifest['prefix_jobs'])==8 and len(cps['records'])==16
        assert (directory/'manifest.json').read_bytes()==archive.read('manifest.json')
        assert (directory/'checkpoint_manifest.json').read_bytes()==archive.read('checkpoint_manifest.json')
        ready_branches=0
        for rec in cps['records']:
            assert (directory/rec['path']).read_bytes()==archive.read(rec['path'])
            cp=json.loads(archive.read(rec['path']))
            if cp['status']=='ready':
                assert len(cp['nodes'])==3+rec['prefix_step']
                ready_branches+=cp.get('branch') is not None
        for rec in manifest['data']+cps['records']:
            assert sha(archive.read(rec['path']))==rec['sha256']
        assert all(rec['role']=='search' for rec in manifest['data'])
        for rec in manifest['data']:
            data=json.loads(archive.read(rec['path']))
            assert 'test' not in data and len(data['probe'])==12 and len(data['validation'])==36
        assert not any(n.startswith(('test/','runs/')) or n=='test_gate.json' for n in names)
        assert not any('test-' in n for n in names if n.startswith('data/'))
        requests=responses=unknown=known=proposals=0
        statuses={}
        for job in manifest['prefix_jobs']:
            prefix='prefix_runs/'+job['job_id']+'/'
            status=json.loads(archive.read(prefix+'terminal_status.json'))['status']
            statuses[status]=statuses.get(status,0)+1
            slots=[n for n in names if n.startswith(prefix+'slots/') and n.endswith('/candidate.json')]
            proposals+=len(slots)
            for n in slots:
                candidate=json.loads(archive.read(n))
                node=candidate.get('node',candidate)
                assert node.get('code') and node['evaluation']['valid']
            for n in names:
                if not n.startswith(prefix+'calls/') or not n.endswith('/state.json'): continue
                state=json.loads(archive.read(n)); folder=n.rsplit('/',1)[0]
                request=json.loads(archive.read(folder+'/request.json'))
                assert request['model']=='MiniMax-M3' and request['provider']=='minimax-cn-coding-plan'
                assert sha(canonical(request))==state['request_sha256']
                if state['status']=='prepared': continue
                requests+=1
                if state['status']=='response_persisted':
                    response=json.loads(archive.read(folder+'/response.json'))
                    assert response['returned_model']=='MiniMax-M3' and response['usage_complete']
                    assert folder+'/raw_response.json' in names
                    responses+=1
                    known+=response['input_tokens']+response['output_tokens']
                else:
                    assert state['status']=='sent_unknown'
                    assert folder+'/raw_response.json' not in names
                    unknown+=1
        assert (requests,responses,unknown,known,proposals)==(33,32,1,127470,16)
        assert statuses=={'sent_unknown':1,'not_started':7}
    planned=read(directory/'CONTINUATION_REQUEST.json')
    assert len(planned['tasks'])==128 and sum(j['eligible_for_future_request'] for j in planned['tasks'])==8
    assert not any(j['authorized'] for j in planned['tasks'])
    assert planned['requested_if_separately_approved']['tokens']==800000
    source_files=read(directory/'REMOTE_REVIEW_FILES.json')['files']
    with zipfile.ZipFile(directory/'review-sources.zip') as archive:
        for name, record in source_files.items():
            data=archive.read(name)
            assert sha(data)==record['sha256'] and len(data)==record['bytes'], name
    if source_root:
        for name, record in source_files.items():
            data=(source_root/safe_path(name)).read_bytes()
            assert sha(data)==record['sha256'] and len(data)==record['bytes'], name
    return {'status':'passed','publication_files_verified':len(hashes['files']),
        'source_files_verified':len(source_files) if source_root else 0,'archives':counts,
        'manifest_sha256':manifest['manifest_sha256'],'prefix_tasks':8,'planned_checkpoints':16,
        'ready_checkpoints':sum(r['status']=='ready' for r in cps['records']),
        'ready_checkpoints_with_branch':ready_branches,
        'requests':requests,'responses':responses,'unknown_requests':unknown,'known_tokens':known,
        'completed_proposals':proposals,'all_member_hashes_match':True,
        'archive_file_hashes_verified':sum(c['files'] for c in counts.values()),
        'publication_hash_index_sha256':sha((directory/'HASHES.json').read_bytes()),
        'continuation_not_started':True,'Test_not_materialized':True}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--commit')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--source-root',type=Path)
    args=parser.parse_args()
    root=args.source_root
    if args.commit:
        assert re.fullmatch('[0-9a-f]{40}',args.commit),'use a fixed commit'
        if args.directory.exists(): raise ValueError('download into a NEW directory')
        args.directory.mkdir(parents=True)
        fetch(args.commit,REL+'/HASHES.json',args.directory/'HASHES.json')
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(fetch,args.commit,REL+'/'+name,args.directory/name)
                     for name in read(args.directory/'HASHES.json')['files']]
            for future in futures: future.result()
        root=args.directory/'downloaded-repository-files'
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(fetch,args.commit,name,root/name)
                     for name in read(args.directory/'REMOTE_REVIEW_FILES.json')['files']]
            for future in futures: future.result()
    result=verify(args.directory,root)
    result['remote_commit']=args.commit
    result['verified_at_utc']=datetime.now(timezone.utc).isoformat()
    if args.output: args.output.write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result))

if __name__=='__main__': main()
