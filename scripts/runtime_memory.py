"""Per-run phase admission and best-effort release of completed owned files.
These are Skill policies; no sandbox limits or global caches are changed.
"""
import json
import gc
import hashlib
import os
from pathlib import Path
import stat
import time
import threading
from memory_guard import evaluate_guard, process_tree_rss


def policy():
    from probe_core import ROOT, read, ProbeError
    value=read(ROOT/'config/memory-policy.json')
    if value.get('schemaVersion')!=1 or set(value.get('stageReserveMiB',{}))!={'selection','detail_rpa','video_download','media_probe','cv_precheck','export'}:
        raise ProbeError('memory_policy_invalid')
    for reserve in value['stageReserveMiB'].values():
        if not isinstance(reserve,int) or isinstance(reserve,bool) or not 16<=reserve<=512:raise ProbeError('memory_policy_invalid')
    return value


def append_event(root, name, value):
    with (Path(root)/name).open('a',encoding='utf-8') as handle:
        handle.write(json.dumps({'time':time.time(),**value},ensure_ascii=False)+'\n')


def check_stage(root, stage):
    from probe_core import ROOT, read, write, cgroup_snapshot, ProbeError
    config=read(ROOT/'config/cv-low-memory.json')
    result=evaluate_guard(cgroup_snapshot(),config,tree_rss=process_tree_rss(os.getpid()),reserve_mib=policy()['stageReserveMiB'][stage])
    result['stage']=stage
    append_event(root,'phase-memory.ndjson',result)
    write(Path(root)/'phase-memory.json',result)
    if result['abort']:raise ProbeError('insufficient_stage_headroom')
    return result


def release_completed(root, stage):
    """Only regular CSV/JSON/JSONL/video files in this run; failures are evidence.
    DONTNEED is advisory and does not promise a cgroup reduction.
    """
    root=Path(root).resolve()
    paths=[]
    for folder in ('acquisition','media'):
        directory=root/folder
        if directory.is_dir() and not directory.is_symlink():
            paths.extend(p for p in sorted(directory.rglob('*')) if p.suffix in ('.csv','.json','.jsonl','.mp4'))
    return release_owned(root,stage,root,paths)


def release_owned(log_root, stage, owner_root, paths):
    """Advise an explicit list of completed files created/owned by this operation.
    Never scan an export parent, follow symlinks, remove files, or clear global caches.
    """
    from probe_core import cgroup_snapshot
    owner=Path(owner_root).absolute();root=owner.resolve();before=cgroup_snapshot()
    result={'stage':stage,'policyOrigin':'skill','supported':hasattr(os,'posix_fadvise') and hasattr(os,'POSIX_FADV_DONTNEED'),
            'attemptedFiles':0,'advisedBytes':0,'errors':[], 'before':before,
            'effect':'advisory_only_not_guaranteed_reclaim'}
    if owner.is_symlink():result['ownerRejected']='symlink_owner'
    if result['supported'] and not owner.is_symlink():
        for original in dict.fromkeys(Path(p).absolute() for p in paths):
            if not original.is_relative_to(owner):continue
            relative=original.relative_to(owner)
            path=root/relative
            if '..' in relative.parts:continue
            if any((root/Path(*relative.parts[:i])).is_symlink() for i in range(1,len(relative.parts)+1)):continue
            fd=None
            try:
                if not stat.S_ISREG(path.lstat().st_mode):continue
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):continue
                result['attemptedFiles']+=1
                os.fsync(fd)
                os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED)
                result['advisedBytes']+=info.st_size
            except OSError as exc:
                result['errors'].append({'path':path.relative_to(root).as_posix(),'errno':exc.errno})
            finally:
                if fd is not None:os.close(fd)
    result['after']=cgroup_snapshot()
    append_event(log_root,'cache-advice.ndjson',result)
    return result


def release_item(root, stage, *, attempt_id=None, report_root=None):
    """Release only this completed item's inputs, bound CV attempt and snapshot.
    Worker exit releases its native allocations. GC only collects unreachable
    Python objects; neither operation promises a decrease in shared cgroup use.
    """
    from probe_core import safe_file, read, ProbeError
    root=Path(root)
    results=[release_completed(root,stage+'_inputs')]
    if attempt_id is not None:
        attempt=safe_file(root,'cv/'+attempt_id+'/receipt.json').parent
        receipt=read(attempt/'receipt.json')
        if receipt.get('processCleanup',{}).get('status') not in ('completed','not_started'):
            raise ProbeError('process_cleanup_unconfirmed')
        paths=[attempt/item['path'] for item in receipt.get('artifacts',[])]+[attempt/'receipt.json']
        results.append(release_owned(root,stage+'_cv',attempt,paths))
    if report_root is not None:
        results.append(release_owned(root,stage+'_report',root,[root/'report/report.json',root/'report/receipt.json']))
        owner=Path(report_root)
        results.append(release_owned(root,stage+'_snapshot',owner,[owner/'report/report.json',owner/'report/receipt.json']))
    collected=gc.collect()
    append_event(root,'cache-advice.ndjson',{'stage':stage+'_python','policyOrigin':'skill','collectedObjects':collected,
                 'effect':'unreachable_objects_only_not_guaranteed_anon_reclaim'})
    return {'cacheAdvice':results,'collectedObjects':collected}


def digest_owned(path, *, owner_root, log_root, stage, chunk_bytes=1024*1024):
    """Full SHA with best-effort read-page advice for closed owned input files."""
    from probe_core import cgroup_snapshot, ProbeError
    owner=Path(owner_root).absolute();path=Path(path).absolute()
    try:relative=path.relative_to(owner)
    except ValueError:raise ProbeError('unsafe_artifact_path')
    if '..' in relative.parts or any((owner/Path(*relative.parts[:i])).is_symlink() for i in range(len(relative.parts)+1)):
        raise ProbeError('unsafe_artifact_path')
    page_size=os.sysconf('SC_PAGE_SIZE') if hasattr(os,'sysconf') else 4096
    if chunk_bytes<=0 or chunk_bytes%page_size:raise ProbeError('hash_chunk_alignment_invalid')
    supported=hasattr(os,'posix_fadvise') and hasattr(os,'POSIX_FADV_DONTNEED')
    observation={'stage':stage,'policyOrigin':'skill','path':relative.as_posix(),'supported':supported,
                 'operation':'owned_input_sha256','readBytes':0,'hashedBytes':0,'advisedBytes':0,'errors':[],'before':cgroup_snapshot(),
                 'effect':'advisory_only_not_guaranteed_reclaim','hashCoverage':'all_bytes'}
    sha=hashlib.sha256();fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):raise ProbeError('artifact_missing')
        initial=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
        observation['initialStat']={'dev':initial[0],'ino':initial[1],'sizeBytes':initial[2],'mtimeNs':initial[3]}
        if supported:
            try:os.fsync(fd)
            except OSError as exc:observation['errors'].append({'operation':'fsync','errno':exc.errno});supported=False
        with os.fdopen(fd,'rb') as handle:
            fd=None;offset=0;advice_offset=0
            while True:
                chunk=handle.read(chunk_bytes)
                if not chunk:break
                sha.update(chunk);observation['readBytes']+=len(chunk);observation['hashedBytes']+=len(chunk)
                aligned_end=((offset+len(chunk))//page_size)*page_size
                if supported and aligned_end>advice_offset:
                    try:
                        os.posix_fadvise(handle.fileno(),advice_offset,aligned_end-advice_offset,os.POSIX_FADV_DONTNEED)
                        observation['advisedBytes']+=aligned_end-advice_offset;advice_offset=aligned_end
                    except OSError as exc:
                        observation['errors'].append({'operation':'chunk_advice','errno':exc.errno});supported=False
                offset+=len(chunk)
            final_info=os.fstat(handle.fileno());current=path.lstat()
            final=(final_info.st_dev,final_info.st_ino,final_info.st_size,final_info.st_mtime_ns)
            observation['finalStat']={'dev':final[0],'ino':final[1],'sizeBytes':final[2],'mtimeNs':final[3]}
            if final!=initial or (current.st_dev,current.st_ino,current.st_size,current.st_mtime_ns)!=initial or observation['hashedBytes']!=initial[2]:
                raise ProbeError('artifact_changed')
            if supported:
                try:os.posix_fadvise(handle.fileno(),0,0,os.POSIX_FADV_DONTNEED)
                except OSError as exc:observation['errors'].append({'operation':'eof_advice','errno':exc.errno})
        observation['sha256']=sha.hexdigest();return observation['sha256']
    finally:
        if fd is not None:os.close(fd)
        observation['after']=cgroup_snapshot();append_event(log_root,'cache-advice.ndjson',observation)


class StageMonitor:
    """Sample a separate operation directory every 200ms; latch stop decisions.
    The caller checks at bounded I/O chunks and stage boundaries. Sampling cannot
    guarantee interception of instantaneous OOM, and does not kill other tasks.
    """
    def __init__(self, root, stage):
        from probe_core import ROOT, Resources, read, cgroup_snapshot
        self.root=Path(root);self.stage=stage
        self.config=read(ROOT/'config/cv-low-memory.json')
        self.baseline=cgroup_snapshot();self.sampler=Resources(self.root,interval=.2)
        self.stop=threading.Event();self.lock=threading.Lock();self.failure=None;self.thread=None

    def observe(self):
        from probe_core import write
        with self.lock:
            row=self.sampler.sample()
            guard=evaluate_guard(row['cgroup'],self.config,baseline=self.baseline,tree_rss=row['processTreeSampledRssBytes'])
            guard['stage']=row['stage']
            append_event(self.root,'guard-samples.ndjson',guard)
            write(self.root/'memory-guard.json',guard)
            if guard['abort'] and self.failure is None:self.failure=guard['reason']

    def check(self):
        from probe_core import ProbeError
        if self.failure:raise ProbeError('operation_memory_guard_aborted')

    def checkpoint(self, phase):
        from probe_core import write
        write(self.root/'status.json',{'status':'running','stage':phase,'pid':os.getpid()})
        self.observe();self.check()

    def loop(self):
        while not self.stop.wait(.2):
            try:self.observe()
            except Exception:self.failure='memory_monitor_failed';return

    def __enter__(self):
        self.checkpoint(self.stage)
        self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
        return self

    def __exit__(self,*args):
        self.stop.set()
        if self.thread:self.thread.join()
        self.observe()
