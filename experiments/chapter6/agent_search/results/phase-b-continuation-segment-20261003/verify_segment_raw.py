import argparse, hashlib, json, time, zipfile, subprocess
from pathlib import Path
from urllib.request import urlopen

def sha(b):return hashlib.sha256(b).hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--relative',required=True)
    p.add_argument('--commit',required=True);p.add_argument('--download',type=Path,required=True);a=p.parse_args()
    local=a.root/a.relative;a.download.mkdir(parents=True,exist_ok=True)
    base='https://raw.githubusercontent.com/zhang-li-da/multimodal-optimization-thesis/'+a.commit+'/'+a.relative+'/'
    expected=json.loads((local/'DELIVERY_HASHES.json').read_text(encoding='utf-8'))
    # Fetch only this result's files. Retries here are public read-only downloads, never model requests.
    for rel,hashed in expected['files'].items():
        path=a.download/rel
        if path.exists() and sha(path.read_bytes())==hashed:continue
        for attempt in range(3):
            try:
                url='https://api.github.com/repos/zhang-li-da/multimodal-optimization-thesis/contents/'+a.relative+'/'+rel+'?ref='+a.commit
                raw=subprocess.check_output(['curl.exe','-fLsS','--connect-timeout','10','--max-time','30',
                                             '-H','Accept: application/vnd.github.raw+json',url],stderr=subprocess.PIPE)
                if sha(raw)!=hashed:raise ValueError('remote hash mismatch '+rel)
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                print(json.dumps({'downloaded':rel,'bytes':len(raw)}),flush=True);break
            except Exception as exc:
                print(json.dumps({'file':rel,'attempt':attempt+1,'error':type(exc).__name__}),flush=True)
                if attempt==2:raise
                time.sleep(1)
    idx=json.loads((a.download/'ARCHIVE_INDEX.json').read_text(encoding='utf-8'))
    assert sha((a.download/idx['archive']).read_bytes())==idx['sha256']
    with zipfile.ZipFile(a.download/idx['archive']) as z:
        assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))==len(idx['members'])
        for row in idx['members']:
            raw=z.read(row['path']);assert len(raw)==row['bytes'] and sha(raw)==row['sha256']
        coverage={s:sum(n.endswith(s) for n in z.namelist()) for s in ['/request.json','/response.json','/candidate.json','/selection_candidates.json','/terminal_status.json']}
        manifest=json.loads(z.read('manifest.json'))
    source=json.loads((a.download/'EXECUTION_SOURCE_INDEX.json').read_text(encoding='utf-8'))
    assert sha((a.download/'execution-source.zip').read_bytes())==source['archive_sha256']
    with zipfile.ZipFile(a.download/'execution-source.zip') as z:
        assert z.testzip() is None
        for row in source['members']:
            raw=z.read(row['path']);assert len(raw)==row['bytes'] and sha(raw)==row['sha256']
    assert len(manifest['jobs'])==128
    analysis=json.loads((a.download/'ANALYSIS.json').read_text(encoding='utf-8'))
    receipt={'fixed_remote_commit':a.commit,'files_verified':len(expected['files']),'archive_members_verified':len(idx['members']),
             'source_members_verified':len(source['members']),'zip_integrity':'passed','planned_jobs':128,
             'planned_checkpoints':len([r for r in manifest['files'] if r['path'].startswith('checkpoints/')]),
             'coverage':coverage,'manifest_sha256':manifest['manifest_sha256'],'cumulative_cost':analysis['cumulative_cost'],
             'all_remote_bytes_match_delivery_hashes':True,'text_hash_basis':'Git blob LF','test_access':False}
    (local/'REMOTE_VERIFICATION.json').write_text(json.dumps(receipt,indent=2)+chr(10),encoding='utf-8')
    print(json.dumps(receipt),flush=True)

if __name__=='__main__':main()
