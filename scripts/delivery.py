"""Export and verify a self-contained public report bundle, without private acquisition files."""
from pathlib import Path
import os
import re
import shutil
import tempfile
import zipfile
from probe_core import ProbeError, artifact, digest, read, write, safe_file, run_lock, report

def verify_export(root):
    root=Path(root)
    if not (root/'bundle-manifest.json').is_file():raise ProbeError('delivery_manifest_missing')
    manifest=read(root/'bundle-manifest.json')
    paths={a['path'] for a in manifest['artifacts']}
    required={'report/index.html','report/report.json','report/receipt.json','resources.ndjson'}
    if not required<=paths:raise ProbeError('delivery_incomplete')
    public=read(root/'report/report.json')
    if public.get('videoPath') and 'media/source-video.mp4' not in paths:raise ProbeError('delivery_video_missing')
    for item in manifest['artifacts']:
        path=safe_file(root,item['path'])
        if artifact(path,root)!=item:raise ProbeError('delivery_artifact_changed')
    html_path=root/'report/index.html'
    for target in re.findall(r'(?:src|href)=["\']([^"\']+)',html_path.read_text()):
        if target.startswith(('http:','https:','data:')):raise ProbeError('delivery_remote_resource')
        target_path=(html_path.parent/target).resolve()
        if not target_path.is_relative_to(root.resolve()) or target_path.relative_to(root.resolve()).as_posix() not in paths:raise ProbeError('delivery_resource_missing')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='bundle-manifest.json'}
    if actual!=paths:raise ProbeError('delivery_file_set_changed')
    return {'status':'verified','fileCount':len(paths)+1,'containsVideo':'media/source-video.mp4' in paths}

def export_report(root,destination):
    root=Path(root);destination=Path(destination)
    if destination.exists():raise ProbeError('delivery_output_exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    scratch=Path(tempfile.mkdtemp(prefix='.report-export-',dir=destination.parent))
    archive=destination.with_name(destination.name+'.zip')
    if archive.exists():shutil.rmtree(scratch);raise ProbeError('delivery_output_exists')
    fd,tmpzip=tempfile.mkstemp(prefix='.report-bundle-',suffix='.zip',dir=destination.parent);os.close(fd)
    try:
        with run_lock(root):
            report(root)
            public=read(root/'report/report.json')
            for relative in ('report/index.html','report/report.json','report/receipt.json','resources.ndjson'):
                src=safe_file(root,relative);target=scratch/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
            if public.get('videoPath'):
                relative='media/source-video.mp4';target=scratch/relative;target.parent.mkdir(parents=True,exist_ok=True)
                src=safe_file(root,relative)
                if artifact(src,root)!=public['videoArtifact']:raise ProbeError('artifact_changed')
                shutil.copy2(src,target)
            artifacts=[artifact(p,scratch) for p in sorted(scratch.rglob('*')) if p.is_file()]
            write(scratch/'bundle-manifest.json',{'schemaVersion':1,'status':public['status'],'containsPrivateSources':False,'artifacts':artifacts})
            verify_export(scratch)
            with zipfile.ZipFile(tmpzip,'w') as z:
                for path in sorted(scratch.rglob('*')):
                    if path.is_file():z.write(path,path.relative_to(scratch).as_posix(),compress_type=zipfile.ZIP_STORED if path.suffix=='.mp4' else zipfile.ZIP_DEFLATED)
            with zipfile.ZipFile(tmpzip) as z:
                if z.testzip() is not None:raise ProbeError('delivery_zip_crc_failed')
                expected={p.relative_to(scratch).as_posix() for p in scratch.rglob('*') if p.is_file()}
                if set(z.namelist())!=expected:raise ProbeError('delivery_zip_incomplete')
            os.rename(scratch,destination);os.replace(tmpzip,archive)
        return {'status':'exported','bundleDir':str(destination),'zipPath':str(archive),'zipSha256':digest(archive),'containsVideo':bool(public.get('videoPath')),'fileCount':len(artifacts)+1}
    finally:
        if scratch.exists():shutil.rmtree(scratch)
        Path(tmpzip).unlink(missing_ok=True)
