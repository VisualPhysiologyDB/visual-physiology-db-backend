"""Offline integrity check of shipped public source snapshots and viewer assets."""
import hashlib,json,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
failures=[];count=0
for item in json.loads((root/'data/tuning/downloads.json').read_text()):
    if not item.get('path'):continue # Archive checksum, not an installed file.
    path=root/item['path'];count+=1
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
        failures.append(item['path'])
for name in ['catalogue.json','reference-profile.fasta']:
    path=root/'data/tuning'/name
    print(name,hashlib.sha256(path.read_bytes()).hexdigest())
if failures:
    print('Integrity failures:',', '.join(failures));sys.exit(1)
print(f'{count} pinned source/asset files verified. No network requests.')
