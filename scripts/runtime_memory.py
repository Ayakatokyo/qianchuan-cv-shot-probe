"""Per-run phase admission and best-effort release of completed owned files.
These are Skill policies; no sandbox limits or global caches are changed.
"""
import json
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
    if result['supported']:
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
