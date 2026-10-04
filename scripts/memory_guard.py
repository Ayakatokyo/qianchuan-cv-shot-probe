"""Skill-owned guard: conservative inactive-file credit plus independent ceilings.
No cgroup writes, cache clearing, or kernel-limit changes. Estimates are not free RAM.
"""
from pathlib import Path
import subprocess
import sys

def values(raw):
    result={}
    for line in (raw or '').splitlines():
        parts=line.split()
        if len(parts)==2:
            try:result[parts[0].rstrip(':')]=max(0,int(parts[1]))
            except ValueError:pass
    return result

def process_tree_rss(pid):
    try:
        processes={}
        if sys.platform=='darwin':
            observed=subprocess.run(['ps','-axo','pid=,ppid=,rss='],capture_output=True,text=True,timeout=2)
            if observed.returncode:return None
            for line in observed.stdout.splitlines():
                child,parent,rss=map(int,line.split())
                processes[child]=(parent,rss*1024)
        else:
            for directory in Path('/proc').iterdir():
                if not directory.name.isdigit():continue
                try:
                    fields=values((directory/'status').read_text().replace(' kB',''))
                    processes[int(directory.name)]=(fields['PPid'],fields.get('VmRSS',0)*1024)
                except (OSError,KeyError):continue
        included={pid};changed=True
        while changed:
            changed=False
            for child,(parent,rss) in processes.items():
                if parent in included and child not in included:included.add(child);changed=True
        return sum(processes[p][1] for p in included if p in processes) if pid in processes else None
    except (OSError, ValueError, subprocess.TimeoutExpired):return None

def evaluate_guard(snapshot,config,*,baseline=None,tree_rss=None,reserve_mib=0):
    result={'policyOrigin':'skill','limitOrigin':'sandbox_cgroup','abort':False,'reason':'unknown_cgroup','headroomKnown':False,'stageReserveBytes':reserve_mib*1048576,'processTreeRssBytes':tree_rss,'processTreeBudgetBytes':config['maxProcessTreeRssMiB']*1048576,'workingSetFraction':config['memoryGuardFraction'],'rawEmergencyFraction':config['memoryHardFraction'],'reclaimEstimate':'conservative inactive_file credit; not guaranteed reclaimable/free RAM'}
    if tree_rss is not None and tree_rss>result['processTreeBudgetBytes']:result.update(abort=True,reason='process_tree_budget')
    keys=('memory.current','memory.max') if snapshot.get('version')==2 else ('memory.usage_in_bytes','memory.limit_in_bytes')
    try:used,limit=(int(snapshot[k]) for k in keys)
    except (KeyError,ValueError,TypeError):return result
    if snapshot.get('status')!='available' or limit<=0 or limit>=2**60 or used<0:return result
    stats=values(snapshot.get('memory.stat'));v2=snapshot.get('version')==2
    stop_headroom=max(0,int(limit*config['memoryHardFraction'])-used)
    result.update(stageReserveBytes=reserve_mib*1048576,headroomToSkillRawCeilingBytes=stop_headroom,headroomKnown=True)
    # Prefer hierarchical v1 stats; fail closed to raw usage when no usable breakdown.
    prefix='' if v2 or 'total_inactive_file' not in stats else 'total_'
    inactive=stats.get(prefix+'inactive_file');cache=stats.get('file' if v2 else prefix+'cache')
    credit=0;basis='raw_usage_fallback'
    deduction_keys=['shmem','file_dirty','file_writeback'] if v2 else [prefix+'shmem',prefix+'dirty',prefix+'writeback']
    result['missingReclaimDeductionFields']=[k for k in deduction_keys if k not in stats]
    if inactive is not None and cache is not None:
        deductions=sum(stats.get(k,0) for k in (['shmem','file_dirty','file_writeback'] if v2 else [prefix+'shmem',prefix+'dirty',prefix+'writeback']))
        credit=max(0,min(inactive,cache,used)-deductions);basis='usage_minus_conservative_inactive_file'
    working=used-credit
    pressure_full=None
    for line in (snapshot.get('memory.pressure') or '').splitlines():
        if line.startswith('full '):
            try:pressure_full=float(next(p.split('=',1)[1] for p in line.split() if p.startswith('avg10=')))
            except (StopIteration,ValueError):pass
    fail_delta=0
    if baseline and not v2:
        try:fail_delta=max(0,int(snapshot['memory.failcnt'])-int(baseline['memory.failcnt']))
        except (KeyError,ValueError,TypeError):pass
    events=values(snapshot.get('memory.events'));prior=values((baseline or {}).get('memory.events'))
    events_delta={k:max(0,v-prior.get(k,v)) for k,v in events.items()}
    result.update(reason=result['reason'] if result['abort'] else 'within_skill_budget',rawUsageBytes=used,sandboxLimitBytes=limit,inactiveFileCreditBytes=credit,workingSetEstimateBytes=working,guardBasis=basis,memoryStat=stats,pressureFullAvg10=pressure_full,failcntDelta=fail_delta,eventDelta=events_delta)
    if not result['abort']:
        reason=None
        if used>=limit*config['memoryHardFraction']:reason='raw_emergency_ceiling'
        elif working>=limit*config['memoryGuardFraction']:reason='working_set_ceiling'
        elif stop_headroom<reserve_mib*1048576:reason='insufficient_stage_headroom'
        elif fail_delta or any(events_delta.get(k,0) for k in ('oom','oom_kill','max')):reason='shared_cgroup_limit_event'
        elif pressure_full is not None and pressure_full>=1 and used>=limit*.85:reason='shared_cgroup_pressure'
        if reason:result.update(abort=True,reason=reason)
    return result
