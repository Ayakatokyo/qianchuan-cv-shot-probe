"""Build only explicitly reviewed runtime files, with exact source verification."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def build(destination):
    spec=json.loads((ROOT/'packaging.json').read_text());package=spec['package'];entries=spec['files']
    if package!=ROOT.name:raise ValueError('package identity mismatch')
    content={}
    for entry in entries:
        rel=entry['source'];target=entry['destination'];path=ROOT/rel
        for value in (rel,target):
            p=PurePosixPath(value)
            if p.is_absolute() or '..' in p.parts or '\\' in value:raise ValueError('unsafe manifest entry')
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():raise ValueError('missing/unsafe runtime file')
        if target in content:raise ValueError('duplicate manifest destination')
        content[target]=path.read_bytes()
    if 'SKILL.md' not in content or 'scripts/run.py' not in content:raise ValueError('missing skill entry')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    output=destination/(package+'.zip');fd,staging=tempfile.mkstemp(dir=destination,suffix='.zip');os.close(fd)
    try:
        with zipfile.ZipFile(staging,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,value in content.items():
                info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16
                z.writestr(info,value)
        with zipfile.ZipFile(staging) as z:
            assert z.testzip() is None and set(z.namelist())==set(content)
            assert all(z.read(name)==data for name,data in content.items())
        os.replace(staging,output)
    finally:Path(staging).unlink(missing_ok=True)
    print(json.dumps({'path':str(output.resolve()),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'fileCount':len(content)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dist-dir',type=Path,default=ROOT/'dist');a=p.parse_args();build(a.dist_dir)
