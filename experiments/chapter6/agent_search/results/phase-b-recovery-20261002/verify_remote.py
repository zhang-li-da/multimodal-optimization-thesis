from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
from urllib.request import Request,urlopen
import zipfile

ROOT=Path('C:/Users/67473/Desktop/5/_delivery/ch6-phase-b-review-20261001')
REL='experiments/chapter6/agent_search/results/phase-b-recovery-20261002'
OUT=ROOT/REL
REPO='zhang-li-da/multimodal-optimization-thesis'
def sha(b):return hashlib.sha256(b).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--commit',required=True);a=p.parse_args()
 expected=json.loads((OUT/'DELIVERY_HASHES.json').read_text(encoding='utf8'))
 # Git credentials remain in memory and are used only for the GitHub API.
 cred=subprocess.run(['git','credential','fill'],input='protocol=https'+chr(10)+'host=github.com'+chr(10)+chr(10),
                     text=True,capture_output=True,cwd=ROOT,check=True).stdout
 token=dict(line.split('=',1) for line in cred.splitlines() if '=' in line).get('password')
 headers={'Accept':'application/vnd.github.raw+json','User-Agent':'chapter6-fixed-commit-audit'}
 if token:headers['Authorization']='Bearer '+token
 files={}
 download=Path('C:/Users/67473/Desktop/5/phase_b_recovery_remote')/a.commit
 download.mkdir(parents=True,exist_ok=True)
 for rel,hashed in expected['files'].items():
  url=f'https://api.github.com/repos/{REPO}/contents/{REL}/{rel}?ref={a.commit}'
  error=None
  for attempt in range(3):
   try:
    with urlopen(Request(url,headers=headers),timeout=60) as response:raw=response.read()
    if sha(raw)!=hashed:raise ValueError('Downloaded file digest mismatch: '+rel)
    error=None;break
   except Exception as exc:
    error=type(exc).__name__
    if attempt<2:time.sleep(2)
  if error:raise RuntimeError('Remote read failed: '+rel+' '+error)
  target=download/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
  files[rel]=hashed
 index=json.loads((download/'RAW_MEMBERS.json').read_text(encoding='utf8'))
 with zipfile.ZipFile(download/index['archive']) as z:
  assert z.testzip() is None
  assert len(z.namelist())==len(set(z.namelist()))==index['member_count']
  for row in index['members']:
   data=z.read(row['path']);assert len(data)==row['bytes'] and sha(data)==row['sha256']
  manifest=json.loads(z.read('manifest.json'))
  assert manifest['manifest_sha256']==expected['manifest_sha256']
  audit=json.loads(z.read('PREFIX_AUDIT.json'))
  assert len(audit['rows'])==len(audit['checkpoints'])==16
  assert len({r['block'] for r in audit['rows']})==8
  counts={suffix:sum(n.endswith(suffix) for n in z.namelist()) for suffix in
          ['/request.json','/response.json','/raw_response.json','/candidate.json','/checkpoint.json','/state.json']}
 receipt={'fixed_remote_commit':a.commit,'repository':REPO,'files_verified':len(files),
          'manifest_sha256':manifest['manifest_sha256'],'member_hashes_verified':index['member_count'],
          'zip_integrity':'passed','planned_jobs':8,'planned_checkpoints':16,'coverage':counts,
          'cost':audit['cost'],'all_downloaded_bytes_match_local':True,'test_access':False}
 (OUT/'REMOTE_VERIFICATION.json').write_bytes((json.dumps(receipt,indent=2)+chr(10)).encode())
 print(json.dumps(receipt))
if __name__=='__main__':main()
