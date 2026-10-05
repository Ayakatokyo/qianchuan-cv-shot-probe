"""Export a public report and a separate complete memory-observation evidence ZIP."""
from pathlib import Path
from runtime_memory import check_stage, release_completed, release_owned, StageMonitor
import os
import re
import shutil
import tempfile
import zipfile
from probe_core import ProbeError, artifact, digest, read, write, safe_file, run_lock, report, resource_summary, ROOT, safe_error

def verify_export(root):
    root=Path(root)
    if not (root/'bundle-manifest.json').is_file():raise ProbeError('delivery_manifest_missing')
    manifest=read(root/'bundle-manifest.json')
    paths={a['path'] for a in manifest['artifacts']}
    required={'report/index.html','report/report.json','report/receipt.json','resources.ndjson'}
    if not required<=paths:raise ProbeError('delivery_incomplete')
    public=read(root/'report/report.json')
    if public.get('videoPath') and 'media/source-video.mp4' not in paths:raise ProbeError('delivery_video_missing')
    cv=public.get('cv',{})
    if cv.get('status')=='succeeded':
        prefix='cv/'+cv['attemptId']+'/'
        if prefix+'shots.json' not in paths or any(prefix+s['frameRef'] not in paths for s in cv['shots']):raise ProbeError('delivery_cv_missing')
    for item in manifest['artifacts']:
        path=safe_file(root,item['path'])
        if artifact(path,root)!=item:raise ProbeError('delivery_artifact_changed')
    html_path=root/'report/index.html'
    for target in re.findall(r'(?:src|href)=["\']([^"\']+)',html_path.read_text()):
        if target.startswith('data:image/jpeg;base64,'):continue
        if target.startswith(('http:','https:','data:')):raise ProbeError('delivery_remote_resource')
        target_path=(html_path.parent/target).resolve()
        if not target_path.is_relative_to(root.resolve()) or target_path.relative_to(root.resolve()).as_posix() not in paths:raise ProbeError('delivery_resource_missing')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name!='bundle-manifest.json'}
    if actual!=paths:raise ProbeError('delivery_file_set_changed')
    return {'status':'verified','fileCount':len(paths)+1,'containsVideo':'media/source-video.mp4' in paths}

def copy_bounded(source, target, monitor):
    target.parent.mkdir(parents=True,exist_ok=True)
    with source.open('rb') as src,target.open('wb') as dst:
        while True:
            monitor.check()
            chunk=src.read(1024*1024)
            if not chunk:break
            dst.write(chunk)
    shutil.copystat(source,target)


def zip_bounded(path, root, monitor):
    with zipfile.ZipFile(path,'w') as archive:
        for source in sorted(root.rglob('*')):
            if not source.is_file():continue
            monitor.check()
            info=zipfile.ZipInfo.from_file(source,source.relative_to(root).as_posix())
            info.compress_type=zipfile.ZIP_STORED if source.suffix=='.mp4' else zipfile.ZIP_DEFLATED
            with source.open('rb') as src,archive.open(info,'w') as dst:
                while True:
                    monitor.check();chunk=src.read(1024*1024)
                    if not chunk:break
                    dst.write(chunk)


def export_report(root,destination,*,expected_attempt=None):
    root=Path(root);destination=Path(destination)
    archive=destination.with_name(destination.name+'.zip')
    evidence=destination.with_name(destination.name+'.memory')
    evidence_zip=destination.with_name(destination.name+'.memory.zip')
    if any(p.exists() for p in (destination,archive,evidence,evidence_zip)):raise ProbeError('delivery_output_exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Reserve the evidence identity before allocating scratch files.
    evidence.mkdir()
    monitor=StageMonitor(evidence,'export_precheck')
    scratch=None;tmpzip=None;result=None;error=None;published=False
    try:
        with run_lock(root),monitor:
            release_completed(root,'export_precheck')
            check_stage(root,'export')
            scratch=Path(tempfile.mkdtemp(prefix='.report-export-',dir=destination.parent))
            fd,tmpzip=tempfile.mkstemp(prefix='.report-bundle-',suffix='.zip',dir=destination.parent);os.close(fd)
            monitor.checkpoint('export_render')
            report(root);public=read(root/'report/report.json')
            if expected_attempt is not None and public.get('cv',{}).get('attemptId')!=expected_attempt:
                raise ProbeError('delivery_cv_attempt_changed')
            monitor.checkpoint('export_copy')
            for relative in ('report/index.html','report/report.json','report/receipt.json','resources.ndjson'):
                copy_bounded(safe_file(root,relative),scratch/relative,monitor)
            if public.get('videoPath'):
                relative='media/source-video.mp4';src=safe_file(root,relative)
                if artifact(src,root)!=public['videoArtifact']:raise ProbeError('artifact_changed')
                monitor.check();copy_bounded(src,scratch/relative,monitor)
            cv=public.get('cv',{})
            if cv.get('attemptId'):
                prefix='cv/'+cv['attemptId']+'/'
                selected=['config.json','resources.ndjson','shots.json','status.json','receipt.json','worker-environment.json','boundaries.ndjson','supervisor-failure.json','worker-failure.json','memory-guard.json','memory-admission.json','guard-samples.ndjson']+[s['frameRef'] for s in cv.get('shots',[]) if s['representativeStatus']=='available']
                for relative in selected:
                    if (root/prefix/relative).exists():copy_bounded(safe_file(root,prefix+relative),scratch/prefix/relative,monitor)
            for relative in ('phase-memory.ndjson','phase-memory.json','cache-advice.ndjson'):
                if (root/relative).exists():copy_bounded(safe_file(root,relative),scratch/relative,monitor)
            monitor.checkpoint('export_verify_copies')
            artifacts=[artifact(p,scratch) for p in sorted(scratch.rglob('*')) if p.is_file()]
            write(scratch/'bundle-manifest.json',{'schemaVersion':1,'status':public['status'],'containsPrivateSources':False,'artifacts':artifacts})
            verify_export(scratch)
            monitor.checkpoint('export_zip')
            zip_bounded(tmpzip,scratch,monitor)
            monitor.checkpoint('export_verify_zip')
            with zipfile.ZipFile(tmpzip) as z:
                if z.testzip() is not None:raise ProbeError('delivery_zip_crc_failed')
                expected={p.relative_to(scratch).as_posix() for p in scratch.rglob('*') if p.is_file()}
                if set(z.namelist())!=expected:raise ProbeError('delivery_zip_incomplete')
            zip_sha=digest(tmpzip)
            monitor.checkpoint('export_cache_advice')
            release_completed(root,'export_complete')
            release_owned(evidence,'export_copies_complete',scratch,[p for p in scratch.rglob('*') if p.is_file()])
            release_owned(evidence,'export_zip_complete',destination.parent,[Path(tmpzip)])
            monitor.checkpoint('export_publish')
            os.rename(scratch,destination);os.rename(tmpzip,archive);published=True
            monitor.checkpoint('export_complete')
            result={'status':'exported','bundleDir':str(destination),'zipPath':str(archive),'zipSha256':zip_sha,'containsVideo':bool(public.get('videoPath')),'fileCount':len(artifacts)+1,
                    'memoryEvidenceDir':str(evidence),'memoryEvidenceZip':str(evidence_zip)}
        monitor.check()
    except Exception as exc:
        error={'errorCode':getattr(exc,'code','delivery_failed'),'message':safe_error(exc)}
        raise
    finally:
        if scratch is not None and scratch.exists():shutil.rmtree(scratch)
        if tmpzip is not None:Path(tmpzip).unlink(missing_ok=True)
        write(evidence/'status.json',{'status':'failed' if error else 'exported','stage':'export_complete','outputPublished':published,'guardStopReason':monitor.failure,**(error or {})})
        write(evidence/'receipt.json',{'schemaVersion':1,'packageVersion':read(ROOT/'config/platform.json')['version'],
              'status':'failed' if error else 'exported','outputPublished':published,'result':result,'error':error,
              'guardStopReason':monitor.failure,'memoryObservation':resource_summary(evidence),
              'cgroupBefore':monitor.baseline,'coverage':'render/copy/hash/verify/ZIP/cache advice/publish; evidence ZIP assembly excluded',
              'artifacts':[artifact(p,evidence) for p in sorted(evidence.rglob('*')) if p.is_file() and p.name!='receipt.json']})
        with zipfile.ZipFile(evidence_zip,'x',compression=zipfile.ZIP_DEFLATED) as z:
            for p in sorted(evidence.iterdir()):
                if p.is_file():z.write(p,p.name)
    return result
