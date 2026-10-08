"""Skill-owned guard: conservative inactive-file credit plus independent ceilings.
No cgroup writes, cache clearing, or kernel-limit changes. Estimates are not free RAM.
"""
from pathlib import Path
import subprocess
import sys
import math

def values(raw):
    result={};seen=set()
    for line in (raw or '').splitlines():
        parts=line.split()
        if len(parts)==2:
            key=parts[0].rstrip(':')
            if key in seen:result.pop(key,None);continue
            seen.add(key)
            if parts[1].isascii() and parts[1].isdigit():result[key]=int(parts[1])
    return result

def proc_meminfo_bounds(raw):
    """Parse native /proc/meminfo; these are host-wide upper bounds, not free RAM."""
    result={};seen=set()
    if not isinstance(raw,str):raise ValueError('proc_meminfo_unreadable')
    for line in raw.splitlines():
        parts=line.split()
        if len(parts) not in (2,3) or not parts[0].endswith(':'):raise ValueError('proc_meminfo_invalid')
        key=parts[0][:-1]
        if key in seen:raise ValueError('proc_meminfo_duplicate')
        seen.add(key)
        if not parts[1].isascii() or not parts[1].isdigit():raise ValueError('proc_meminfo_invalid')
        if len(parts)==3 and parts[2]!='kB':raise ValueError('proc_meminfo_unit_invalid')
        if key in ('Dirty','Writeback'):
            if len(parts)!=3:raise ValueError('proc_meminfo_unit_invalid')
            result[key]=int(parts[1])*1024
    if set(result)!={'Dirty','Writeback'}:raise ValueError('proc_meminfo_fields_missing')
    return result

def native_proc_bounds(snapshot):
    evidence=snapshot.get('nativeProcMeminfo') or {}
    source=evidence.get('source') or {}
    if evidence.get('status')!='available' or source.get('path')!='/proc/meminfo' or source.get('fstype')!='proc' or source.get('mountRoot')!='/':return None
    point=source.get('mountPoint')
    if not isinstance(point,str) or not Path(point).is_absolute() or not Path('/proc/meminfo').is_relative_to(Path(point)):return None
    try:
        before=proc_meminfo_bounds(evidence.get('before'));after=proc_meminfo_bounds(evidence.get('after'))
        upper={key:max(before[key],after[key]) for key in before}
        if evidence.get('upperBoundsBytes')!=upper:return None
        return upper
    except ValueError:return None

def counter(raw):
    return int(raw) if isinstance(raw,str) and raw.isascii() and raw.isdigit() else None

def full_pressure(raw):
    lines=[line.split()[1:] for line in (raw or '').splitlines() if line.startswith('full ')]
    if len(lines)!=1:return None,None
    fields={}
    for part in lines[0]:
        key,separator,value=part.partition('=')
        if not separator or key in fields:return None,None
        fields[key]=value
    try:
        average=float(fields['avg10'])
        if not math.isfinite(average) or average<0:average=None
    except (KeyError,ValueError):average=None
    return average,counter(fields.get('total'))

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
    if type(tree_rss) is not int or tree_rss<0:tree_rss=None
    reserve=reserve_mib*1048576
    result={'policyOrigin':'skill','policyVersion':'0.5.5-cache-backed-admission','limitOrigin':'sandbox_cgroup','abort':False,'reason':'unknown_cgroup','headroomKnown':False,'stageReserveBytes':reserve,'processTreeRssBytes':tree_rss,'processTreeBudgetBytes':config['maxProcessTreeRssMiB']*1048576,'workingSetFraction':config['memoryGuardFraction'],'rawEmergencyFraction':config['memoryHardFraction'],'reclaimEstimate':'conservative inactive_file credit; sampled upper bounds; not guaranteed reclaimable/free RAM'}
    tree_headroom=None if tree_rss is None else max(0,result['processTreeBudgetBytes']-tree_rss)
    result['processTreeHeadroomBytes']=tree_headroom
    if tree_rss is not None and tree_rss>result['processTreeBudgetBytes']:result.update(abort=True,reason='process_tree_budget')
    keys=('memory.current','memory.max') if snapshot.get('version')==2 else ('memory.usage_in_bytes','memory.limit_in_bytes')
    try:used,limit=(int(snapshot[k]) for k in keys)
    except (KeyError,ValueError,TypeError):return result
    if snapshot.get('status')!='available' or limit<=0 or limit>=2**60 or used<0:return result
    stats=values(snapshot.get('memory.stat'));v2=snapshot.get('version')==2
    stop_headroom=max(0,int(limit*config['memoryHardFraction'])-used)
    result.update(stageReserveBytes=reserve_mib*1048576,headroomToSkillRawCeilingBytes=stop_headroom,headroomKnown=True)
    # Prefer hierarchical v1 stats; fail closed to raw usage when no usable breakdown.
    prefix='' if v2 or not any(line.split() and line.split()[0].startswith('total_') for line in (snapshot.get('memory.stat') or '').splitlines()) else 'total_'
    inactive=stats.get(prefix+'inactive_file');cache=stats.get('file' if v2 else prefix+'cache')
    credit=0;basis='raw_usage_fallback'
    deduction_keys=['shmem','file_dirty','file_writeback'] if v2 else [prefix+'shmem',prefix+'dirty',prefix+'writeback']
    result['missingReclaimDeductionFields']=[k for k in deduction_keys if k not in stats]
    # Native v1 file LRU excludes swap-backed shmem; MADV_FREE/LazyFree pages
    # on that LRU are discardable. Do not invent a missing shmem counter.
    required=deduction_keys if v2 else deduction_keys[1:]
    deductions={k:stats[k] for k in required if k in stats}
    sources={k:'memory.stat_same_scope' for k in deductions}
    upper=native_proc_bounds(snapshot) if not v2 else None
    if upper is not None:
        for key,global_key in ((prefix+'dirty','Dirty'),(prefix+'writeback','Writeback')):
            if key not in deductions:deductions[key]=upper[global_key];sources[key]='native_proc_global_upper_bound'
    unresolved=[k for k in required if k not in deductions]
    result.update(reclaimDeductionBytes=dict(deductions),reclaimDeductionSources=sources,nativeProcMeminfo=snapshot.get('nativeProcMeminfo'),unresolvedReclaimDeductionFields=unresolved,shmemDeduction='same_scope_required' if v2 else 'not_required_native_v1_file_lru')
    if inactive is not None and cache is not None and not unresolved:
        deductions=sum(deductions.values())
        credit=max(0,min(inactive,cache,used)-deductions);basis='usage_minus_conservative_inactive_file'
    working=used-credit
    pressure_full,pressure_total=full_pressure(snapshot.get('memory.pressure'))
    _prior_full,prior_total=full_pressure((baseline or {}).get('memory.pressure'))
    pressure_delta=max(0,pressure_total-prior_total) if pressure_total is not None and prior_total is not None and pressure_total>=prior_total else None
    fail=counter(snapshot.get('memory.failcnt'));prior_fail=counter((baseline or {}).get('memory.failcnt'))
    fail_known=not v2 and (baseline or {}).get('status')=='available' and (baseline or {}).get('version')==1 and (baseline or {}).get('memory.limit_in_bytes')==snapshot.get('memory.limit_in_bytes') and fail is not None and prior_fail is not None and fail>=prior_fail
    fail_delta=max(0,fail-prior_fail) if fail_known else None
    oom=values(snapshot.get('memory.oom_control')).get('under_oom') if not v2 else None
    under_oom=bool(oom) if oom in (0,1) else None
    events=values(snapshot.get('memory.events'));prior=values((baseline or {}).get('memory.events'))
    events_delta={k:max(0,v-prior.get(k,v)) for k,v in events.items()}
    hard_headroom=max(0,int(limit*config['memoryHardFraction'])-working) if credit>0 else stop_headroom
    stage_headroom=min(hard_headroom,max(0,int(limit*config['memoryGuardFraction'])-working))
    raw_exceeded=used>=limit*config['memoryHardFraction']
    event_known=all(k in events and k in prior and events[k]>=prior[k] for k in ('max','oom','oom_kill'))
    admission_reasons=[]
    if credit<=0:admission_reasons.append('no_reliable_positive_inactive_credit')
    if working>=limit*config['memoryGuardFraction']:admission_reasons.append('working_set_ceiling')
    if stage_headroom<reserve:admission_reasons.append('insufficient_stage_headroom')
    if tree_rss is None:admission_reasons.append('process_tree_rss_unknown')
    elif tree_rss>=result['processTreeBudgetBytes'] or tree_headroom<reserve:admission_reasons.append('insufficient_process_tree_headroom')
    if v2:
        admission_reasons.append('cache_backed_admission_requires_v1')
        if not event_known:admission_reasons.append('limit_event_baseline_unknown')
        if pressure_full is None:admission_reasons.append('pressure_unknown')
    else:
        if not fail_known:admission_reasons.append('failcnt_baseline_unknown')
        if under_oom is not False:admission_reasons.append('current_oom_unknown_or_active')
    if fail_delta or any(events_delta.get(k,0) for k in ('oom','oom_kill','max')):admission_reasons.append('shared_cgroup_limit_event')
    if pressure_full is not None and pressure_full>=1:admission_reasons.append('shared_cgroup_pressure')
    if pressure_delta:admission_reasons.append('shared_cgroup_pressure_since_baseline')
    if pressure_total is not None and prior_total is not None and pressure_total<prior_total:admission_reasons.append('pressure_counter_regressed')
    if used>=limit:admission_reasons.append('sandbox_limit_reached')
    cache_admit=not admission_reasons and used<limit
    result.update(reason=result['reason'] if result['abort'] else 'within_skill_budget',rawUsageBytes=used,sandboxLimitBytes=limit,inactiveFileCreditBytes=credit,workingSetEstimateBytes=working,guardBasis=basis,memoryStat=stats,pressureFullAvg10=pressure_full,pressureFullTotal=pressure_total,pressureFullTotalDelta=pressure_delta,pressureStatus='available' if pressure_full is not None else 'unknown',failcntDelta=fail_delta,failcntKnown=fail_known,underOom=under_oom,eventDelta=events_delta,headroomForStageBytes=stage_headroom,headroomBasis='conservative_working_set_estimate' if credit>0 else 'raw',rawCeilingExceeded=raw_exceeded,cacheBackedAdmission={'eligible':cache_admit,'used':raw_exceeded and cache_admit,'reasons':admission_reasons},baselineFailcnt=prior_fail)
    if not result['abort']:
        reason=None
        if used>=limit:reason='sandbox_limit_reached'
        elif under_oom is True:reason='shared_cgroup_current_oom'
        elif fail_delta or any(events_delta.get(k,0) for k in ('oom','oom_kill','max')):reason='shared_cgroup_limit_event'
        elif raw_exceeded and not cache_admit:reason='raw_emergency_ceiling'
        elif working>=limit*config['memoryGuardFraction']:reason='working_set_ceiling'
        elif stage_headroom<reserve:reason='insufficient_stage_headroom'
        elif tree_headroom is not None and tree_headroom<reserve:reason='insufficient_process_tree_headroom'
        elif pressure_full is not None and pressure_full>=1 and used>=limit*.85:reason='shared_cgroup_pressure'
        elif raw_exceeded:result['reason']='cache_backed_admitted'
        if reason:result.update(abort=True,reason=reason)
    return result
