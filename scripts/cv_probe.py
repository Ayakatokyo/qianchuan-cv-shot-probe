"""Standard-library supervisor for one isolated CV attempt, with no API/RPA calls."""
from importlib.metadata import version,PackageNotFoundError
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from memory_guard import evaluate_guard, process_tree_rss
from probe_core import ROOT,ProbeError,Resources,artifact,cgroup_snapshot,digest,fingerprint,read,write,run_lock,safe_file,verify,probe_media,resource_summary,safe_error

PINNED={'numpy':'2.2.6','opencv-python-headless':'4.11.0.86','scenedetect':'0.6.7.1'}

def _installed(name):
    try:version(name);return True
    except PackageNotFoundError:return False

def dependency_status(backend='ffmpeg-scene'):
    expected=PINNED if backend=='adaptive' else {'imageio-ffmpeg':version('imageio-ffmpeg') if _installed('imageio-ffmpeg') else '0.6.0'}
    observed={}
    for name in expected:
        try:observed[name]=version(name)
        except PackageNotFoundError:observed[name]='unavailable'
    return {'status':'ready' if observed==expected else 'dependency_missing_or_version_mismatch','observed':observed,'expected':expected,'importChecked':False,'importCheck':'runs in monitored CV worker','backend':backend,'requirementsPath':str(ROOT/('requirements-cv.txt' if backend=='adaptive' else 'requirements.txt'))}

def memory_usage(snapshot):
    if snapshot.get('status')!='available':return None
    keys=('memory.current','memory.max') if snapshot.get('version')==2 else ('memory.usage_in_bytes','memory.limit_in_bytes')
    try:used,limit=(int(snapshot[k]) for k in keys)
    except (KeyError,ValueError,TypeError):return None
    if limit<=0 or limit>=2**60:return None
    return used,limit

def oom_counters(snapshot):
    result={}
    for key in ('memory.events','memory.events.local'):
        raw=snapshot.get(key) or ''
        for line in raw.splitlines():
            cells=line.split()
            if len(cells)==2 and cells[1].isdigit():result[key+'.'+cells[0]]=int(cells[1])
    value=snapshot.get('memory.failcnt')
    if value is not None:
        try:result['memory.failcnt']=int(value)
        except ValueError:pass
    return result

def process_identity(pid):
    try:return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]
    except (OSError,IndexError):return None

def recorded_process_alive(status):
    pid=status.get('workerPid') or status.get('pid')
    if not pid:return False
    recorded=status.get('workerStartTicks');actual=process_identity(pid)
    if recorded and actual and recorded!=actual:return False
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def group_is_empty(pgid):
    try:
        result=subprocess.run(['ps','-axo','pid=,pgid=,uid=,stat='],capture_output=True,text=True,timeout=2)
        if result.returncode:return None
        for line in result.stdout.splitlines():
            parts=line.split()
            if len(parts)>=4 and parts[1]==str(pgid) and not parts[3].startswith('Z'):return False
        return True
    except (OSError,subprocess.TimeoutExpired):return None

def stop_group(process):
    cleanup={'status':'completed','groupId':process.pid}
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:process.poll();return cleanup
    except PermissionError:
        cleanup['status']='unconfirmed'
        if process.poll() is None:
            try:process.terminate()
            except (ProcessLookupError,PermissionError):pass
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:process.kill();process.wait(timeout=2)
        except (ProcessLookupError,PermissionError,subprocess.TimeoutExpired):cleanup['status']='unconfirmed'
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    except PermissionError:
        # Some macOS process-group exits race with killpg; never hide live/unknown descendants.
        cleanup['status']='completed' if group_is_empty(process.pid) is True else 'unconfirmed'
    cleanup['workerExited']=process.poll() is not None
    return cleanup

def config_value(path=None):
    config=read(path or ROOT/'config/cv-low-memory.json')
    expected={'profile','maxDimension','maxShots','timeoutSec','memoryGuardFraction','minShotSec','adaptiveThreshold','windowWidth','minContentVal','backend','sceneThreshold','memoryHardFraction','maxProcessTreeRssMiB'}
    if set(config)!=expected or config['profile']!='low-memory':raise ProbeError('cv_config_invalid')
    if config['backend'] not in ('ffmpeg-scene','adaptive'):raise ProbeError('cv_config_invalid')
    for key in expected-{'profile','backend'}:
        if not isinstance(config[key],(int,float)) or isinstance(config[key],bool) or not math.isfinite(config[key]):raise ProbeError('cv_config_invalid')
    if not (1<=config['maxDimension']<=320 and 1<=config['maxShots']<=300 and 0.05<=config['timeoutSec']<=300 and .5<=config['memoryGuardFraction']<=.8 and .01<=config['minShotSec']<=2 and 1<=config['windowWidth']<=8 and config['adaptiveThreshold']>0 and config['minContentVal']>0 and 0<config['sceneThreshold']<=100 and .9<=config['memoryHardFraction']<=.97 and 32<=config['maxProcessTreeRssMiB']<=512):raise ProbeError('cv_config_invalid')
    for key in ('maxDimension','maxShots','windowWidth'):
        if not isinstance(config[key],int):raise ProbeError('cv_config_invalid')
    return config

def implementation_sha():
    return fingerprint({name:digest(ROOT/'scripts'/name) for name in ('cv_probe.py','cv_worker.py','native_cv_worker.py','memory_guard.py','probe_core.py')})

def verify_cv(root,attempt_id=None):
    acquisition=verify(root)
    attempt_id=attempt_id or read(root/'cv/latest.json')['attemptId']
    attempt=safe_file(root,f'cv/{attempt_id}/receipt.json').parent;receipt=read(attempt/'receipt.json')
    if receipt['status']!='succeeded':raise ProbeError('cv_incomplete')
    video=next(a for a in acquisition['artifacts'] if a['path']=='media/source-video.mp4')
    if receipt['inputVideoSha256']!=video['sha256']:raise ProbeError('cv_input_changed')
    for item in receipt['artifacts']:
        if artifact(safe_file(attempt,item['path']),attempt)!=item:raise ProbeError('cv_artifact_changed')
    data=read(attempt/'shots.json');shots=data['shots']
    if not shots or len(shots)>300 or shots[0]['startFrame']!=0 or shots[0]['startSec']!=0:raise ProbeError('cv_coverage_invalid')
    for index,shot in enumerate(shots):
        if not shot['startFrame']<shot['endFrame'] or not shot['startSec']<shot['endSec']:raise ProbeError('cv_coverage_invalid')
        if index and (shots[index-1]['endFrame']!=shot['startFrame'] or shots[index-1]['endSec']!=shot['startSec']):raise ProbeError('cv_coverage_invalid')
        if not shot['startFrame']<=shot['representativeFrame']<shot['endFrame'] or not shot['startSec']<=shot['representativeTimeSec']<shot['endSec']:raise ProbeError('cv_representative_invalid')
        safe_file(attempt,shot['frameRef'])
        if shot['representativeStatus']!='available':raise ProbeError('cv_representative_missing')
    if shots[-1]['endFrame']!=data['frameCount'] or shots[-1]['endSec']!=data['durationSec']:raise ProbeError('cv_coverage_invalid')
    return receipt

def cv_status(root):
    latest=root/'cv/latest.json'
    if not latest.exists():return {'status':'not_run'}
    attempt_id=read(latest)['attemptId'];attempt=root/'cv'/attempt_id
    state=read(attempt/'status.json')
    if (attempt/'process.json').exists():state.update(read(attempt/'process.json'))
    observed=state['status']
    if observed=='running' and not recorded_process_alive(state):observed='interrupted'
    receipt=read(attempt/'receipt.json') if (attempt/'receipt.json').exists() else {}
    data=read(attempt/'shots.json') if (attempt/'shots.json').exists() else {}
    return {'status':observed,'attemptId':attempt_id,'stage':state.get('stage'),'failureStage':state.get('failureStage'),'errorCode':state.get('errorCode'),'exitCode':receipt.get('workerExitCode'),'signal':receipt.get('signal'),'config':read(attempt/'config.json'),'memoryGuard':receipt.get('memoryGuard'),
            'frameCount':data.get('frameCount'),'shotCount':len(data.get('shots',[])),'detectionStatus':data.get('detectionStatus'),'representativeStatus':data.get('representativeStatus'),
            'sourceStartPtsSec':data.get('sourceStartPtsSec'),'durationSec':data.get('durationSec'),'timeMapping':data.get('timeMapping'),'variableFrameIntervalsObserved':data.get('variableFrameIntervalsObserved'),
            'memoryObservation':resource_summary(attempt),'cgroupCounterDelta':receipt.get('cgroupCounterDelta'),'processCleanup':receipt.get('processCleanup'),'terminationDiagnosis':receipt.get('terminationDiagnosis'),'oomAttribution':'Counters belong to the visible shared cgroup; delta alone does not identify this worker.',
            'dependencies':read(attempt/'worker-environment.json') if (attempt/'worker-environment.json').exists() else None,'shots':data.get('shots',[])}

def probe_cv(root,*,attempt_id=None,config_file=None,backend=None):
    root=Path(root);config=config_value(config_file)
    if backend:config['backend']=backend
    if config['backend'] not in ('ffmpeg-scene','adaptive'):raise ProbeError('cv_config_invalid')
    impl=implementation_sha()
    if attempt_id and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',attempt_id):raise ProbeError('cv_attempt_id_invalid')
    with run_lock(root):
        acquisition=verify(root);video=safe_file(root,'media/source-video.mp4');video_sha=digest(video)
        signature=fingerprint({'config':config,'implementationSha256':impl,'videoSha256':video_sha})
        if (root/'cv/latest.json').exists():
            prior_id=read(root/'cv/latest.json')['attemptId'];prior=root/'cv'/prior_id
            prior_state=read(prior/'status.json')
            if (prior/'process.json').exists():prior_state.update(read(prior/'process.json'))
            if prior_state.get('workerPid') and recorded_process_alive(prior_state):raise ProbeError('cv_active_or_unconfirmed')
            if prior_state['status']=='running':
                if recorded_process_alive(prior_state):raise ProbeError('cv_active_or_unconfirmed')
                prior_state.update(status='interrupted',errorCode='previous_execution_interrupted');write(prior/'status.json',prior_state)
            if attempt_id is None and (prior/'receipt.json').exists():
                prior_receipt=read(prior/'receipt.json')
                if prior_receipt.get('status')=='succeeded' and prior_receipt.get('bindingSha256')==signature:
                    verify_cv(root,prior_id)
                    from probe_core import report
                    result=report(root);result['reusedCvAttempt']=True;return result
        attempt_id=attempt_id or ('cv-'+uuid.uuid4().hex[:12]);attempt=root/'cv'/attempt_id
        if attempt.exists():raise ProbeError('cv_attempt_exists')
        attempt.mkdir(parents=True)
        write(root/'cv/latest.json',{'attemptId':attempt_id})
        write(attempt/'config.json',config)
        write(attempt/'status.json',{'status':'running','stage':'cv_precheck','pid':os.getpid(),'attemptId':attempt_id})
        before=cgroup_snapshot();process=None;error=None;cleanup={'status':'not_started'};started=time.monotonic()
        with Resources(attempt):
            try:
                guard=evaluate_guard(before,config,baseline=before)
                write(attempt/'memory-guard.json',guard)
                if guard['abort']:raise ProbeError('insufficient_headroom')
                actual_media=probe_media(video)
                job={'attemptDir':str(attempt),'videoPath':str(video),'videoSha256':video_sha,'media':actual_media,'config':config,'implementationSha256':impl}
                write(attempt/'job.json',job)
                env=dict(os.environ)
                for key in ('YUCE_AUTHORIZATION','YCSESSIONID','YUCE_SESSION_ID'):env.pop(key,None)
                for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):env[key]='1'
                with (attempt/'worker.stdout.log').open('w') as out,(attempt/'worker.stderr.log').open('w') as err:
                    process=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts'/('native_cv_worker.py' if config['backend']=='ffmpeg-scene' else 'cv_worker.py')),str(attempt/'job.json')],stdout=out,stderr=err,env=env,start_new_session=True)
                    binding={'workerPid':process.pid,'workerStartTicks':process_identity(process.pid)}
                    write(attempt/'process.json',binding)
                    state=read(attempt/'status.json');state.update(binding);write(attempt/'status.json',state)
                    while process.poll() is None:
                        if time.monotonic()-started>config['timeoutSec']:raise ProbeError('cv_timeout')
                        guard=evaluate_guard(cgroup_snapshot(),config,baseline=before,tree_rss=process_tree_rss(os.getpid()))
                        write(attempt/'memory-guard.json',guard)
                        if guard['abort']:raise ProbeError('memory_guard_aborted')
                        time.sleep(.2)
                if time.monotonic()-started>config['timeoutSec']:raise ProbeError('cv_timeout')
                worker_state=read(attempt/'status.json')
                if process.returncode!=0 or worker_state.get('workerResult')!='succeeded':
                    code=worker_state.get('errorCode') or ('signal_terminated_unknown' if process.returncode<0 else 'cv_worker_failed')
                    raise ProbeError(code)
                if digest(video)!=video_sha:raise ProbeError('cv_input_changed')
            except Exception as exc:error=getattr(exc,'code','cv_supervisor_failed');write(attempt/'supervisor-failure.json',{'errorCode':error,'exceptionType':type(exc).__name__,'message':safe_error(exc)})
            finally:
                if process is not None:
                    cleanup=stop_group(process)
                    if cleanup['status']=='unconfirmed' and error is None:error='process_cleanup_unconfirmed'
                state=read(attempt/'status.json')
                failed_stage=read(attempt/'worker-failure.json').get('stage') if (attempt/'worker-failure.json').exists() else state.get('stage')
                state.update(status='failed' if error else 'succeeded',stage='cv_complete',failureStage=failed_stage if error else None,errorCode=error);write(attempt/'status.json',state)
        after=cgroup_snapshot();start_counts=oom_counters(before);end_counts=oom_counters(after)
        delta={key:end_counts[key]-value for key,value in start_counts.items() if key in end_counts}
        exit_code=process.returncode if process is not None else None
        receipt={'status':'failed' if error else 'succeeded','errorCode':error,'inputVideoSha256':video_sha,'bindingSha256':signature,'implementationSha256':impl,'workerExitCode':exit_code,'signal':-exit_code if exit_code is not None and exit_code<0 else None,'elapsedSec':time.monotonic()-started,'cgroupBefore':before,'cgroupAfter':after,'cgroupCounterDelta':delta,'processCleanup':cleanup,'memoryGuard':read(attempt/'memory-guard.json'),'terminationDiagnosis':{'classification':'skill_guard_terminated' if error in ('memory_guard_aborted','insufficient_headroom') else 'skill_timeout_terminated' if error=='cv_timeout' else 'oom_evidence_observed' if any(v>0 for k,v in delta.items() if k.endswith('.oom_kill')) else ('signal_terminated_unknown' if exit_code is not None and exit_code<0 else 'no_signal_observed'),'attribution':'shared_cgroup_evidence_does_not_prove_worker_cause'},
                 'artifacts':[artifact(p,attempt) for p in sorted(attempt.rglob('*')) if p.is_file()]}
        write(attempt/'receipt.json',receipt)
        if not error:
            try:verify_cv(root,attempt_id)
            except Exception as exc:
                error=getattr(exc,'code','cv_verification_failed');receipt.update(status='failed',errorCode=error);write(attempt/'receipt.json',receipt)
                state=read(attempt/'status.json');state.update(status='failed',errorCode=error);write(attempt/'status.json',state)
        from probe_core import report
        result=report(root);result['cvAttemptId']=attempt_id
        return result
