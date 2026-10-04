"""Per-run phase admission and best-effort release of completed owned files.
These are Skill policies; no sandbox limits or global caches are changed.
"""
import json
import os
from pathlib import Path
import stat
import time
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
    from probe_core import cgroup_snapshot
    root=Path(root).resolve();before=cgroup_snapshot()
    result={'stage':stage,'policyOrigin':'skill','supported':hasattr(os,'posix_fadvise') and hasattr(os,'POSIX_FADV_DONTNEED'),
            'attemptedFiles':0,'advisedBytes':0,'errors':[], 'before':before,
            'effect':'advisory_only_not_guaranteed_reclaim'}
    if result['supported']:
        for folder in ('acquisition','media'):
            directory=root/folder
            if not directory.is_dir() or directory.is_symlink():continue
            for path in sorted(directory.rglob('*')):
                if path.suffix not in ('.csv','.json','.jsonl','.mp4'):continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):continue
                fd=None
                try:
                    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
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
    append_event(root,'cache-advice.ndjson',result)
    return result
