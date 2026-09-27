"""Split and verify the finalized S3 evidence archive; no model calls."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import zipfile

from chapter6_demo.v12_2.common import file_sha, read_json, save_json


def group(path):
    match=re.search(r'/runs/[^/]*-b(\d+)-s\d+/|/tests/[^/]*-b(\d+)-s\d+\.json|/dispatch/[^/]*-b(\d+)-s\d+\.json',path)
    return 'block-'+next(v for v in match.groups() if v is not None) if match else 'common'


def split(results):
    expected={r['path']:r for r in read_json(results/'ARCHIVE_INDEX.json')}
    folder=results/'raw';folder.mkdir(exist_ok=True)
    buckets=collections.defaultdict(list)
    with zipfile.ZipFile(results/'raw-study.zip') as source:
        assert set(source.namelist())==set(expected)
        for name in source.namelist():buckets[group(name)].append(name)
        records=[]
        for key,names in sorted(buckets.items()):
            target=folder/(key+'.zip')
            with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as dest:
                for name in names:
                    content=source.read(name)
                    assert hashlib.sha256(content).hexdigest()==expected[name]['sha256']
                    dest.writestr(source.getinfo(name),content)
            records.append({'path':target.relative_to(results).as_posix(),'bytes':target.stat().st_size,
                            'sha256':file_sha(target),'members':len(names)})
    save_json(results/'RAW_PARTS.json',{'format':'independent ZIP files; extract all parts into the same directory',
                                      'no_concatenation_required':True,'members':len(expected),'parts':records,
                                      'new_model_calls':0})
    return verify(results)


def verify(results):
    expected={r['path']:r for r in read_json(results/'ARCHIVE_INDEX.json')}
    seen=set();index=read_json(results/'RAW_PARTS.json')
    for part in index['parts']:
        path=results/part['path'];assert file_sha(path)==part['sha256']
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist())==part['members']
            for name in archive.namelist():
                assert name not in seen and name in expected
                value=archive.read(name);rec=expected[name]
                assert len(value)==rec['bytes'] and hashlib.sha256(value).hexdigest()==rec['sha256']
                seen.add(name)
    assert seen==set(expected)
    result={'status':'passed','verified_members':len(seen),'parts':len(index['parts']),
            'new_model_calls':0,'largest_zip_bytes':max(p['bytes'] for p in index['parts'])}
    save_json(results/'ARCHIVE_VERIFICATION.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['split','verify']);parser.add_argument('--results',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(split(args.results) if args.command=='split' else verify(args.results)))


if __name__=='__main__':main()
