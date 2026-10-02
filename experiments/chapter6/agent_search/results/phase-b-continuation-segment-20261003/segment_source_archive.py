import json
import zipfile
import subprocess
import hashlib
from pathlib import Path
from chapter6_demo.v12_2.common import source_record

root=Path.cwd()
base=root/'experiments/chapter6/agent_search/minimal_mechanism'
dest=Path('C:/Users/67473/Desktop/5/segment_delivery_staging_20261003')
dest.mkdir(exist_ok=True)
paths=set(source_record()['files'])
idx=json.loads((base/'SOURCE_SHA256.json').read_text(encoding='utf-8'))
paths.update((base/r['path']).relative_to(root).as_posix() for r in idx['files'])
paths.add((base/'SOURCE_SHA256.json').relative_to(root).as_posix())
for sub in ['component_validation','s3_tsp_r3','s2_tsp']:
    paths.update(p.relative_to(root).as_posix() for p in (root/'experiments/chapter6/agent_search'/sub).glob('*.py'))
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
rows=[]
with zipfile.ZipFile(dest/'execution-source.zip','w',zipfile.ZIP_DEFLATED) as z:
    for rel in sorted(paths):
        raw=subprocess.check_output(['git','show',commit+':'+rel])
        z.writestr(rel,raw)
        rows.append({'path':rel,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
data={'commit':commit,'members':rows,'archive_sha256':hashlib.sha256((dest/'execution-source.zip').read_bytes()).hexdigest()}
(dest/'EXECUTION_SOURCE_INDEX.json').write_text(json.dumps(data,indent=2)+chr(10),encoding='utf-8')
print(json.dumps({'commit':commit,'source_files':len(rows),'archive_bytes':(dest/'execution-source.zip').stat().st_size}))
