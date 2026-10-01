"""Package offline evidence using exact Git blobs; never evaluate or call a model."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
METHOD = 'experiments/chapter6/agent_search/minimal_mechanism/'

def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

def write(name, value):
    (HERE/name).write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())

def blob(commit, name):
    return subprocess.check_output(['git', 'show', commit+':'+name], cwd=ROOT)

def main():
    manifest = json.loads((HERE/'REVIEW_MANIFEST.json').read_bytes())
    commit = manifest['revised_source_commit']
    index_bytes = blob(commit, METHOD+'SOURCE_SHA256.json')
    index = json.loads(index_bytes)
    assert index['manifest_sha256'] == manifest['revised_source_index_digest']
    sources = {METHOD+'SOURCE_SHA256.json': index_bytes}
    for record in index['files']:
        name = METHOD+record['path']
        data = blob(commit, name)
        assert digest(data) == {'sha256': record['sha256'], 'bytes': record['bytes']}, name
        sources[name] = data
    old_audit = 'experiments/chapter6/agent_search/results/phase-b-public-prefix-20261001/audit_prefix.py'
    sources[old_audit] = blob(commit, old_audit)
    write('REMOTE_REVIEW_FILES.json', {'source_commit': commit, 'files':
        {name: digest(data) for name, data in sorted(sources.items())}})
    scripts = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in HERE.glob('*.py')}
    # Fixed packaging timestamp makes repeat packaging byte-reproducible.
    # It is not a model request or execution timestamp.
    with zipfile.ZipFile(HERE/'review-sources.zip', 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted({**sources, **scripts}.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    archives = {}
    for path in sorted(HERE.glob('*.zip')):
        with zipfile.ZipFile(path) as archive:
            assert archive.testzip() is None
            records = archive.infolist()
            assert len({r.filename for r in records}) == len(records)
            archives[path.name] = {**digest(path.read_bytes()), 'member_count': len(records),
                'directory_count': sum(r.is_dir() for r in records),
                'files': {r.filename: digest(archive.read(r.filename)) for r in records if not r.is_dir()}}
    write('ARCHIVE_INDEX.json', {'archives': archives})
    paths = sorted(p for p in HERE.rglob('*') if p.is_file() and
        '__pycache__' not in p.parts and p.name != 'HASHES.json')
    write('HASHES.json', {'schema': 'publication-file-sha256-v1',
        'self_excluded': True, 'files': {p.relative_to(HERE).as_posix(): digest(p.read_bytes()) for p in paths}})
    print(json.dumps({'files': len(paths), 'source_files': len(sources),
        'archives': {k: {'members': v['member_count'], 'bytes': v['bytes']} for k,v in archives.items()}}))

if __name__ == '__main__':
    main()
