"""Verify downloaded hashes, every ZIP member, and study freeze bindings offline."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

def sha(data):
    return hashlib.sha256(data).hexdigest()

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def verify(directory):
    directory=Path(directory)
    index=json.loads((directory/'HASHES.json').read_bytes())
    for name, row in index['files'].items():
        data=(directory/name).read_bytes()
        assert len(data)==row['size_bytes'], name
        assert sha(data)==row['sha256'], name
    members=json.loads((directory/'ARCHIVE_MEMBERS.json').read_bytes())
    counts={}
    for name, archive in members['archives'].items():
        data=(directory/name).read_bytes()
        assert sha(data)==archive['sha256']
        with zipfile.ZipFile(directory/name) as z:
            assert z.testzip() is None
            infos=z.infolist()
            assert len(infos)==archive['member_count']
            files={i.filename:i for i in infos if not i.is_dir()}
            assert len(files)==archive['file_count']
            assert set(files)==set(archive['files'])
            for path, row in archive['files'].items():
                pp=PurePosixPath(path.replace('\\','/'))
                assert not pp.is_absolute() and '..' not in pp.parts
                content=z.read(path)
                assert len(content)==row['size_bytes'] and sha(content)==row['sha256'], path
            if name=='raw-study.zip':
                manifest=json.loads(z.read('manifest.json'))
                cps=json.loads(z.read('checkpoint_manifest.json'))
                for obj, key in [(manifest,'manifest_sha256'),(cps,'checkpoint_manifest_sha256')]:
                    assert sha(canonical({k:v for k,v in obj.items() if k!=key}))==obj[key]
                assert cps['study_manifest_sha256']==manifest['manifest_sha256']
                for rec in manifest['data']+cps['records']:
                    assert sha(z.read(rec['path']))==rec['sha256']
                assert len(cps['records'])==16
                assert not any(p.startswith('test/') or p=='test_gate.json' for p in files)
            counts[name]={'members':len(infos),'files':len(files),'crc':'passed','member_hashes':'passed'}
    return {'status':'passed','indexed_files':len(index['files']), 'archives':counts,
            'study_manifest_sha256':manifest['manifest_sha256'],
            'checkpoint_manifest_sha256':cps['checkpoint_manifest_sha256'],
            'scope':'this batch and its source/support archive; not all repository history'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory',required=True,type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args(); result=verify(a.directory)
    if a.output:
        a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
