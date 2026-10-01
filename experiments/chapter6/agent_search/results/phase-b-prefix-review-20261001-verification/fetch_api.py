"""Download the fixed publication via GitHub Contents API, then verify every byte."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import quote
import urllib.request
import zipfile

COMMIT = '2c55b72022e581172bcb9bcb5a61fabb84258a21'
REL = 'experiments/chapter6/agent_search/results/phase-b-prefix-review-20261001'
API = 'https://api.github.com/repos/zhang-li-da/multimodal-optimization-thesis/contents/'
ARCHIVED_SOURCE = 'experiments/chapter6/agent_search/minimal_mechanism/DATA_SOURCE_SHA256.tsv'

class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://api.github.com/'):
            raise RuntimeError('Refusing to forward authenticated request to a different host')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--resume-files', action='store_true', help='Only finish missing GitHub downloads; never resumes an experiment')
    args=p.parse_args()
    if args.directory.exists() and not args.resume_files: raise ValueError('Use a fresh download directory or explicitly resume missing files')
    args.directory.mkdir(parents=True,exist_ok=True)
    env={**os.environ, 'GIT_TERMINAL_PROMPT':'0', 'GCM_INTERACTIVE':'never'}
    result=subprocess.run(['git','credential','fill'],input='protocol=https\nhost=github.com\n\n',
        text=True, capture_output=True, timeout=15, env=env)
    credential=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if not credential.get('password'):
        raise RuntimeError('No existing GitHub credential; this download exceeds anonymous API limits')
    headers={'User-Agent':'chapter6-fixed-commit-audit', 'Accept':'application/vnd.github.raw+json',
        'Authorization':'Bearer '+credential['password']}
    def fetch(name, target):
        request=urllib.request.Request(API+quote(name, safe='/')+'?ref='+COMMIT, headers=headers)
        opener=urllib.request.build_opener(SameHostRedirect())
        with opener.open(request, timeout=45) as response:
            data=response.read()
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    if not (args.directory/'HASHES.json').exists():
        fetch(REL+'/HASHES.json',args.directory/'HASHES.json')
    assert hashlib.sha256((args.directory/'HASHES.json').read_bytes()).hexdigest() == 'c401012d0c8575a84ae93ec3cfc6496e59e7e1f5e20ade0d59c3c9e0c9f43be6'
    hashes=json.loads((args.directory/'HASHES.json').read_bytes())
    print(json.dumps({'hash_index_downloaded':True,'publication_files':len(hashes['files'])}),flush=True)
    def missing(target, record):
        if not target.exists(): return True
        data=target.read_bytes()
        assert len(data)==record['bytes'] and hashlib.sha256(data).hexdigest()==record['sha256'],target.name
        return False
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(fetch,REL+'/'+name,args.directory/name) for name,rec in hashes['files'].items() if missing(args.directory/name,rec)]
        for f in futures: f.result()
    root=args.directory/'downloaded-repository-files'
    sources=json.loads((args.directory/'REMOTE_REVIEW_FILES.json').read_bytes())['files']
    # This large deduplication index is already in the downloaded source ZIP.
    # Verify both the ZIP and its member before using those remote bytes.
    source_zip=args.directory/'review-sources.zip'
    assert not missing(source_zip,hashes['files']['review-sources.zip'])
    with zipfile.ZipFile(source_zip) as archive:
        data=archive.read(ARCHIVED_SOURCE)
    record=sources[ARCHIVED_SOURCE]
    assert len(data)==record['bytes'] and hashlib.sha256(data).hexdigest()==record['sha256']
    target=root/ARCHIVED_SOURCE
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(data)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(fetch,name,root/name) for name,rec in sources.items() if missing(root/name,rec)]
        for f in futures: f.result()
    # Load the verifier downloaded at the same fixed commit; do not evaluate TSP.
    spec=importlib.util.spec_from_file_location('published_verifier',args.directory/'verify_remote.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result=module.verify(args.directory,root)
    result.update(remote_commit=COMMIT, verified_at_utc=datetime.now(timezone.utc).isoformat(),
        transport='GitHub Contents API with raw media, fixed ref; raw host TLS failed',
        authentication='existing Git credential used in memory only; never logged or saved',
        distinct_files_downloaded=1+len(hashes['files'])+len(sources)-1,
        source_files_downloaded_individually=len(sources)-1,
        source_files_read_from_verified_remote_archive=[ARCHIVED_SOURCE],
        model_calls=0, program_evaluations=0)
    args.receipt.write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(result),flush=True)

if __name__=='__main__': main()
