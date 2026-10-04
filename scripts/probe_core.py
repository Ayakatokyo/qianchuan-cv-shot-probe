"""Bounded single-video acquisition runtime. No CV/ASR/model imports."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
from importlib.metadata import version, PackageNotFoundError
import html
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import math
import importlib
import uuid
from connector_contract import validate_business_params

ROOT = Path(__file__).resolve().parents[1]
CSV_LIMIT = 32 * 1024 * 1024
VIDEO_LIMIT = 128 * 1024 * 1024

def safe_error(exc):
    message=str(exc)
    for key in ('YUCE_AUTHORIZATION','YCSESSIONID','YUCE_SESSION_ID'):
        secret=os.environ.get(key)
        if secret:message=message.replace(secret,'[redacted]')
    return re.sub(r'https?://[^\s]+','[redacted URL]',message)[:1000]

class ProbeError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False, default=str)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(staging, path)
    finally:
        Path(staging).unlink(missing_ok=True)

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

def artifact(path, root):
    path = Path(path)
    return {'path':path.relative_to(root).as_posix(), 'sha256':digest(path), 'sizeBytes':path.stat().st_size}

def safe_file(root, relative):
    path = Path(root)/relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.resolve().is_relative_to(Path(root).resolve()):
        raise ProbeError('unsafe_artifact_path')
    if path.is_symlink() or not path.is_file(): raise ProbeError('artifact_missing')
    return path

def external_root(value):
    path = Path(value).expanduser().resolve()
    if path==ROOT or path.is_relative_to(ROOT): raise ProbeError('output_inside_skill')
    return path

@contextmanager
def run_lock(root):
    with (root/'.execution.lock').open('a+') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc: raise ProbeError('run_active') from exc
        try: yield
        finally: fcntl.flock(handle, fcntl.LOCK_UN)

def transition(root, stage, state='running', **extra):
    prior = read(root/'status.json') if (root/'status.json').exists() else {}
    prior.update(stage=stage, status=state, pid=os.getpid(), updatedAt=datetime.now(timezone.utc).isoformat(), **extra)
    write(root/'status.json', prior)

def resource_id(value):
    if isinstance(value, dict):
        extra=value.get('extra', {})
        value=extra.get('shop_id') if isinstance(extra, dict) else None
    if not isinstance(value, str) or not value.strip(): raise ProbeError('authorization_selection_invalid')
    return value.strip()

def cgroup_snapshot():
    # Resolve mount + membership; do not assume /sys/fs/cgroup is the process group.
    try:
        memberships=Path('/proc/self/cgroup').read_text().splitlines()
        mounts=Path('/proc/self/mountinfo').read_text().splitlines()
        for line in memberships:
            _hier, controllers, member=line.split(':',2)
            v2=controllers==''
            if not v2 and 'memory' not in controllers.split(','):continue
            for mount in mounts:
                left,right=mount.split(' - ',1); fields=left.split(); fstype=right.split()[0]
                if fstype!=('cgroup2' if v2 else 'cgroup'):continue
                if not v2 and 'memory' not in right.split()[-1].split(','):continue
                mount_root=fields[3]; mount_point=Path(fields[4])
                if member==mount_root: relative=''
                elif member.startswith(mount_root.rstrip('/')+'/'): relative=member[len(mount_root):].lstrip('/')
                else:continue
                directory=mount_point/relative
                result={'version':2 if v2 else 1, 'status':'available'}
                names=('memory.current','memory.max','memory.peak','memory.events','memory.events.local','memory.stat','memory.pressure') if v2 else ('memory.usage_in_bytes','memory.limit_in_bytes','memory.max_usage_in_bytes','memory.failcnt','memory.oom_control','memory.stat','memory.pressure')
                for name in names:
                    try:result[name]=(directory/name).read_text().strip()
                    except OSError:result[name]=None
                return result
    except (OSError, ValueError):pass
    return {'status':'unavailable'}

class Resources:
    def __init__(self, root):
        self.root=root; self.stop=threading.Event(); self.thread=None; self.attempt=uuid.uuid4().hex
    def sample(self):
        status=read(self.root/'status.json')
        raw=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss=None
        try:
            fields=Path('/proc/self/status').read_text()
            match=re.search(r'^VmRSS:\s+(\d+) kB',fields,re.M)
            if match:rss=int(match[1])*1024
        except OSError:pass
        from memory_guard import process_tree_rss, evaluate_guard
        tree_rss=process_tree_rss(os.getpid())
        row={'attemptId':self.attempt,'processTreeSampledRssBytes':tree_rss,'time':time.time(),'stage':status.get('stage'),'pid':os.getpid(),'rssBytes':rss,'processLifetimeHwmBytes':raw if sys.platform=='darwin' else raw*1024,'cgroup':cgroup_snapshot()}
        row['memoryEstimate']=evaluate_guard(row['cgroup'],read(ROOT/'config/cv-low-memory.json'),tree_rss=tree_rss)
        with (self.root/'resources.ndjson').open('a',encoding='utf-8') as handle:
            handle.write(json.dumps(row)+'\n'); handle.flush()
    def loop(self):
        while not self.stop.wait(1): self.sample()
    def __enter__(self):
        self.sample(); self.thread=threading.Thread(target=self.loop,daemon=True); self.thread.start();return self
    def __exit__(self,*args):
        self.stop.set();self.thread.join();self.sample()

def download(url, target, *, max_bytes, session=None, deadline=600, receipt_path=None, receipt_root=None):
    import requests
    parsed=urlsplit(url) if isinstance(url,str) else None
    if not parsed or parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username or parsed.password:
        raise ProbeError('download_url_invalid')
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_name('.'+target.name+'.part')
    response=None; started=time.monotonic(); size=0;sha=hashlib.sha256()
    try:
        # Fresh session per artifact: never send gateway cookies to CDN.
        response=(session or requests).get(url,stream=True,timeout=(20,30))
        response.raise_for_status()
        length=response.headers.get('Content-Length')
        if length is not None:
            try: declared=int(length)
            except ValueError: raise ProbeError('download_length_invalid')
            if declared>max_bytes:raise ProbeError('input_limit')
        with partial.open('wb') as handle:
            for chunk in response.iter_content(chunk_size=1024*1024):
                if time.monotonic()-started>deadline:raise ProbeError('download_timeout')
                if not chunk:continue
                size+=len(chunk)
                if size>max_bytes:raise ProbeError('input_limit')
                handle.write(chunk);sha.update(chunk)
        if size==0:raise ProbeError('download_empty')
        # Reject common error pages before any CSV/media parsing.
        with partial.open('rb') as handle: prefix=handle.read(512).lstrip().lower()
        if prefix.startswith((b'<!doctype html',b'<html')):raise ProbeError('download_error_page')
        os.replace(partial,target)
        if receipt_path is not None:
            write(receipt_path,{'path':target.relative_to(receipt_root).as_posix(),'sizeBytes':size,'sha256':sha.hexdigest()})
    except requests.RequestException as exc:
        raise ProbeError('download_failed') from exc
    finally:
        partial.unlink(missing_ok=True)
        if response is not None:response.close()
    return target

def file_urls(value):
    found=[]
    def visit(node):
        if isinstance(node,dict):
            for key,item in node.items():
                if key=='files' and isinstance(item,list):
                    for f in item:
                        if isinstance(f,dict):
                            urls={f[k] for k in ('fileUrl','file_url','downloadUrl','download_url','url') if isinstance(f.get(k),str) and f[k].strip()}
                            if len(urls)!=1:raise ProbeError('csv_file_url_ambiguous')
                            found.extend(urls)
                elif isinstance(item,(dict,list)):visit(item)
        elif isinstance(node,list):
            for item in node:visit(item)
    visit(value)
    unique=list(dict.fromkeys(found))
    if len(unique)!=1:raise ProbeError('csv_unavailable' if not unique else 'csv_artifact_ambiguous')
    return unique[0]

class Gateway:
    def __init__(self, root, *, environ=None, session=None, sleeper=time.sleep):
        import requests
        self.root=root;env=os.environ if environ is None else environ
        self.base=env.get('ENV_BACKEND_HOST','').rstrip('/');self.auth=env.get('YUCE_AUTHORIZATION','')
        self.agent=env.get('YUCE_SESSION_ID') or env.get('YCSESSIONID') or self.auth
        self.cookie=env.get('YCSESSIONID') or self.auth
        if not self.base or not self.auth:raise ProbeError('runtime_credentials_missing')
        self.session=session or requests.Session();self.sleep=sleeper
    def post(self,path,payload):
        import requests
        try:
            response=self.session.post(self.base+path,json=payload,headers={'Content-Type':'application/json'},cookies={'YCSESSIONID':self.cookie},timeout=30)
            try:response.raise_for_status();result=response.json()
            finally:response.close()
        except (requests.RequestException,ValueError) as exc:raise ProbeError('gateway_failed') from exc
        if not isinstance(result,dict) or result.get('success') is not True:raise ProbeError('gateway_rejected')
        return result.get('data')
    def detail(self,code):
        rows=self.post('/dcConnector/v3/listByConnectorCode',{'connectorCode':code})
        matches=[r for r in rows if isinstance(r,dict) and r.get('connectorCode')==code] if isinstance(rows,list) else []
        if len(matches)!=1 or not matches[0].get('platformCode'):raise ProbeError('connector_unavailable')
        return matches[0]
    def account(self,detail,shop):
        rows=self.post('/adg/v1/agent/rpa/authorizations/query',{'authorization':self.auth,'platform':detail['platformCode']})
        if isinstance(rows,dict):rows=rows.get('accounts') or rows.get('list')
        matches=[r for r in rows if str(r.get('shop_id') or r.get('shopId') or r.get('id'))==shop and r.get('platform') in (None,'',detail['platformCode'])] if isinstance(rows,list) else []
        if len(matches)!=1:raise ProbeError('rpa_shop_not_authorized')
        return matches[0]
    def csv(self,phase,code,params,shop):
        detail=self.detail(code);self.account(detail,shop);validate_business_params(detail,params)
        tasks=read(self.root/'acquisition/tasks.json') if (self.root/'acquisition/tasks.json').exists() else {}
        signature=fingerprint({'code':code,'params':params,'shop':shop})
        task=tasks.get(phase)
        if task is not None:
            if task.get('fingerprint')!=signature:raise ProbeError('task_binding_changed')
            if not task.get('taskId'):raise ProbeError('submission_unconfirmed')
        else:
            task={'connectorCode':code,'fingerprint':signature,'status':'submission_intent','taskId':None};tasks[phase]=task
            write(self.root/'acquisition/tasks.json',tasks)
            result=self.post('/adg/v1/agent/fetch/tasks',{'authorization':self.auth,'agent_session_id':self.agent,'data_source_type':'rpa','platform':detail['platformCode'],'function_code':code,'business_params':params,'shop_id':shop})
            if not isinstance(result,dict) or not result.get('task_group_id'):raise ProbeError('submission_unconfirmed')
            task.update(taskId=result['task_group_id'],status='submitted');write(self.root/'acquisition/tasks.json',tasks)
        started=time.monotonic()
        while True:
            result=self.post('/adg/v1/agent/fetch/tasks/status',{'authorization':self.auth,'task_group_id':task['taskId']})
            if not isinstance(result,dict):raise ProbeError('rpa_result_unconfirmed')
            state=str(result.get('status','')).lower();task['status']=state;write(self.root/'acquisition/tasks.json',tasks)
            if state in ('completed','partial_success'):break
            if state=='failed':raise ProbeError('rpa_failed')
            if state not in ('pending','running'):raise ProbeError('rpa_result_unconfirmed')
            if time.monotonic()-started>=3600:raise ProbeError('rpa_timeout')
            self.sleep(10)
        path=self.root/'acquisition'/f'{phase}.csv'
        if not path.exists():download(file_urls(result),path,max_bytes=CSV_LIMIT)
        return path

def probe_media(path):
    import imageio_ffmpeg
    executable=shutil.which('ffprobe')
    if executable:
        cmd=[executable,'-v','error','-show_streams','-show_format','-of','json',str(path)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=30)
        if p.returncode:raise ProbeError('media_probe_failed')
        raw=json.loads(p.stdout); videos=[s for s in raw.get('streams',[]) if s.get('codec_type')=='video']
        if not videos:raise ProbeError('video_stream_missing')
        v=videos[0]; rate=v.get('avg_frame_rate','0/1').split('/');fps=float(rate[0])/float(rate[1])
        duration=float(raw.get('format',{}).get('duration') or v.get('duration') or 0)
        info={'durationSec':duration,'width':int(v['width']),'height':int(v['height']),'fps':fps,'codec':v['codec_name'],'probeProvider':'ffprobe','videoDurationSec':float(v['duration']) if v.get('duration') not in (None,'N/A') else None,'videoStartSec':v.get('start_time'),'videoTimeBase':v.get('time_base')}
    else:
        executable=imageio_ffmpeg.get_ffmpeg_exe()
        p=subprocess.run([executable,'-hide_banner','-i',str(path)],capture_output=True,text=True,timeout=30)
        duration=re.search(r'Duration: (\d+):(\d+):([\d.]+)',p.stderr)
        video=re.search(r'Video: ([^,]+).*? (\d+)x(\d+).*?([\d.]+) fps',p.stderr)
        if not duration or not video:raise ProbeError('media_probe_failed')
        info={'durationSec':int(duration[1])*3600+int(duration[2])*60+float(duration[3]),'codec':video[1],'width':int(video[2]),'height':int(video[3]),'fps':float(video[4]),'probeProvider':'ffmpeg_header'}
    if not 0<info['durationSec']<=180 or not 0<info['fps']<=60 or max(info['width'],info['height'])>1920:raise ProbeError('media_input_limit')
    info['binarySha256']=digest(executable)
    return info

def verify(root):
    receipt=read(root/'acquisition/receipt.json')
    if receipt.get('status')!='video_ready':raise ProbeError('acquisition_incomplete')
    if receipt.get('requestSha256')!=digest(root/'request.json'):raise ProbeError('request_changed')
    for item in receipt['artifacts']:
        path=safe_file(root,item['path'])
        if path.stat().st_size!=item['sizeBytes'] or digest(path)!=item['sha256']:raise ProbeError('artifact_changed')
    return receipt

def resource_summary(root):
    first=last=None;count=0;peaks={};active=0.0
    def observe(measured):
        for key in ('rawUsageBytes','workingSetEstimateBytes'):
            value=measured.get(key)
            if isinstance(value,(float,int)):peaks[key]=max(peaks.get(key,0),value)
        stats=measured.get('memoryStat',{})
        for name,aliases in {'cacheBytes':('file','total_cache','cache'),'anonBytes':('anon','total_rss','rss'),'inactiveFileBytes':('total_inactive_file','inactive_file')}.items():
            value=next((stats[k] for k in aliases if k in stats),None)
            if isinstance(value,int):peaks[name]=max(peaks.get(name,0),value)
    path=root/'resources.ndjson'
    if path.exists():
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                try:row=json.loads(line)
                except ValueError:continue  # A killed writer may leave one partial last line.
                if first is None:first=row
                if last and row.get('attemptId',row.get('pid'))==last.get('attemptId',last.get('pid')):
                    gap=row['time']-last['time']
                    if 0<=gap<=2.5:active+=gap
                last=row;count+=1
                measured=row.get('memoryEstimate')
                if measured is None:
                    from memory_guard import evaluate_guard
                    measured=evaluate_guard(row.get('cgroup',{}),read(ROOT/'config/cv-low-memory.json'))
                observe(measured)
                for key in ('processTreeSampledRssBytes','processLifetimeHwmBytes'):
                    value=row.get(key)
                    if isinstance(value,(float,int)):peaks[key]=max(peaks.get(key,0),value)
    guard_path=root/'guard-samples.ndjson'
    if guard_path.exists():
        with guard_path.open() as handle:
            for line in handle:
                try:g=json.loads(line)
                except ValueError:continue
                observe(g)
                value=g.get('processTreeRssBytes')
                if isinstance(value,int):peaks['processTreeSampledRssBytes']=max(peaks.get('processTreeSampledRssBytes',0),value)
    return {'activeSampledSec':active,'sampleCount':count,'elapsedObservedSec':last['time']-first['time'] if count else None,
            'peaks':peaks,'firstCgroup':first.get('cgroup') if first else None,
            'lastCgroup':last.get('cgroup') if last else None,
            'limits':'A每秒、B守卫约200ms采样仍可能漏掉短峰值；RSS求和可能重复共享页；HWM是进程生命周期值；cgroup峰值和事件可能含本组其他进程。不可读时额度与余量未知。'}

def dependency_versions():
    result={}
    for name in ('requests','jsonschema','imageio-ffmpeg'):
        try:result[name]=version(name)
        except PackageNotFoundError:result[name]='unavailable'
    return result

def preflight():
    missing=[]
    for module in ('requests','jsonschema','imageio_ffmpeg'):
        try:importlib.import_module(module)
        except ImportError:missing.append(module)
    return {'status':'dependency_missing' if missing else 'ready','python':sys.version,
            'pythonExecutable':sys.executable,'dependencies':dependency_versions(),'missing':missing,
            'requirementsPath':str(ROOT/'requirements.txt')}

def compare_media(expected,media):
    comparisons=[]
    for field in ('width','height','durationSec','fps'):
        claimed=expected.get(field)
        if claimed in (None,'',0):continue
        value=float(claimed);actual=float(media[field])
        if not math.isfinite(value) or value<=0 or not math.isfinite(actual):raise ProbeError('media_metadata_invalid')
        precision=expected.get('durationPrecisionSec') if field=='durationSec' else None
        if field=='durationSec':
            tolerance=float(precision) if precision in (0.1,1,1.0) else (1.0 if value.is_integer() else 0.1)
            basis='integer_seconds_metadata' if tolerance==1 else 'fractional_seconds_metadata'
        else:tolerance=0.0;basis='exact'
        comparisons.append({'field':field,'expected':value,'actual':actual,'tolerance':tolerance,
                            'basis':basis,'matched':abs(value-actual)<=tolerance+1e-9})
    return {'status':'matched' if all(c['matched'] for c in comparisons) else 'mismatch',
            'comparisons':comparisons,'note':'媒体参数仅辅助核对，素材身份由CSV及视频来源链校验。'}

def report(root):
    state=read(root/'status.json')
    receipt=read(root/'acquisition/receipt.json') if (root/'acquisition/receipt.json').exists() else None
    if state.get('status')=='video_ready':verify(root)
    selection=read(root/'acquisition/selection.json') if (root/'acquisition/selection.json').exists() else {}
    observed=read(root/'media/probe.json') if (root/'media/probe.json').exists() else {}
    video=root/'media/source-video.mp4';video_ref=None
    if video.exists() and (root/'media/download-receipt.json').exists():
        candidate=read(root/'media/download-receipt.json')
        if state.get('status')=='video_ready':
            confirmed=next(a for a in receipt['artifacts'] if a['path']=='media/source-video.mp4')
            if candidate==confirmed:video_ref=candidate
        elif artifact(video,root)==candidate:video_ref=candidate
    media=receipt.get('media') if receipt else (observed.get('media') if video_ref and observed.get('videoSha256')==video_ref['sha256'] else None)
    public={'stage':'A_acquisition','status':state['status'],'failureStage':state.get('stage') if state['status']=='failed' else None,
            'errorCode':state.get('errorCode'),'platform':read(ROOT/'config/platform.json')['platform'],
            'cvStatus':'not_implemented','media':media,'mediaValidation':observed.get('validation') if media else None,
            'firstBatchCsvGate':state.get('firstBatchCsvGate'),
            'memoryObservation':{**resource_summary(root),'samplesPath':'../resources.ndjson'},
            'videoPath':'../media/source-video.mp4' if video_ref else None,
            'videoArtifact':video_ref,'materialId':receipt.get('materialId') if receipt else (selection.get('material',{}).get('materialId') or selection.get('material',{}).get('material_id')),
            'acquisitionVerified':bool(receipt and state['status']=='video_ready')}
    public['memoryPolicy']=read(ROOT/'config/memory-policy.json')
    public['phaseMemory']=read(root/'phase-memory.json') if (root/'phase-memory.json').exists() else None
    public['cacheAdvicePath']='../cache-advice.ndjson' if (root/'cache-advice.ndjson').exists() else None
    public['runtime']=read(root/'environment.json') if (root/'environment.json').exists() else None
    tasks=read(root/'acquisition/tasks.json') if (root/'acquisition/tasks.json').exists() else {}
    public['tasks']=[{'phase':phase,**{k:task.get(k) for k in ('connectorCode','taskId','status')}} for phase,task in tasks.items()]
    from cv_probe import cv_status,verify_cv
    cv=cv_status(root);public['acquisitionStatus']=state['status'];public['cvStatus']=cv['status'];public['cv']=cv
    if cv['status']!='not_run':
        public['stage']='B_cv';public['status']=cv['status'];public['errorCode']=cv.get('errorCode')
        if cv['status']=='succeeded':verify_cv(root)
    target=root/'report';target.mkdir(exist_ok=True)
    write(target/'report.json',public)
    display={**public,'cv':{k:v for k,v in cv.items() if k!='shots'}}
    content='<html lang="zh-CN"><meta charset="utf-8"><title>视频与CV沙箱测试</title><style>body{font-family:system-ui;max-width:1100px;margin:24px auto;padding:16px}pre{white-space:pre-wrap;word-break:break-word;background:#f5f5f5;padding:16px}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}img{max-width:120px}button{cursor:pointer}</style><body><h1>视频与CV沙箱测试</h1><p>阶段：'+html.escape(public['stage'])+'；CV状态：'+html.escape(cv['status'])+'。资源可运行性与人工切分质量分别验收。</p><pre>'+html.escape(json.dumps(display,ensure_ascii=False,indent=2))+'</pre>'
    if video_ref:content+='<video controls style="max-width:360px" src="../media/source-video.mp4"></video>'
    if cv.get('shots'):
        content+='<h2>镜头与代表帧</h2><table><thead><tr><th>镜头</th><th>时间区间（秒）</th><th>代表帧</th><th>状态</th></tr></thead><tbody>'
        for shot in cv['shots']:
            frame='../cv/'+cv['attemptId']+'/'+shot['frameRef']
            image='<img loading="lazy" src="'+html.escape(frame,quote=True)+'">' if shot['representativeStatus']=='available' else '代表帧缺失'
            content+='<tr><td>'+html.escape(shot['shotId'])+'</td><td><button data-start="'+str(shot['startSec'])+'">'+f"{shot['startSec']:.3f}–{shot['endSec']:.3f}"+'</button></td><td>'+image+'</td><td>'+html.escape(shot['representativeStatus'])+'</td></tr>'
        content+='</tbody></table><script>document.querySelectorAll("button[data-start]").forEach(b=>b.onclick=()=>{const v=document.querySelector("video");if(v){v.currentTime=Number(b.dataset.start);v.play();}});</script>'
    content+='</body></html>'
    (target/'index.html').write_text(content,encoding='utf-8')
    write(target/'receipt.json',{'artifacts':[artifact(target/'index.html',root),artifact(target/'report.json',root)]})
    return {'status':public['status'],'reportPath':str(target/'index.html'),'cvStatus':cv['status']}

def acquire(request,root,*,resume=False,gateway_factory=Gateway,adapter=None,media_probe=probe_media):
    adapter=adapter or __import__('platform_adapter')
    root=external_root(root)
    normalized=adapter.validate(request)
    if resume:
        if not root.is_dir():raise ProbeError('run_missing')
    else:
        root.mkdir(parents=True,exist_ok=False)
    with run_lock(root):
        if resume:
            saved=read(root/'request.json')
            if fingerprint(saved)!=fingerprint(normalized):raise ProbeError('request_changed')
            state=read(root/'status.json')
            if state.get('status')=='video_ready':verify(root);return report(root)
            if state.get('firstBatchCsvGate',{}).get('status')=='blocked':raise ProbeError('rpa_paused')
        else:
            write(root/'request.json',normalized)
            write(root/'environment.json',{'python':sys.version,'platform':sys.platform,'dependencies':dependency_versions(),'stage':'A','packageVersion':read(ROOT/'config/platform.json').get('version'),'cgroup':cgroup_snapshot(),'runtimeFilesSha256':fingerprint({p.name:digest(p) for p in (ROOT/'scripts').glob('*.py')})})
        transition(root,'starting',errorCode=None)
        from runtime_memory import check_stage, release_completed
        with Resources(root) as resources:
            gateway=None
            try:
                gateway=gateway_factory(root)
                transition(root,'selection')
                check_stage(root,'selection')
                if (root/'acquisition/selection-source.json').exists():
                    ref=read(root/'acquisition/selection-source.json')
                    if digest(safe_file(root,ref['path']))!=ref['sha256']:raise ProbeError('selection_source_changed')
                binding=root/'acquisition/selection-binding.json'
                if binding.exists():
                    confirmed=read(binding)
                    if confirmed.get('requestSha256')!=digest(root/'request.json') or confirmed.get('source')!=read(root/'acquisition/selection-source.json') or confirmed.get('selection')!=artifact(root/'acquisition/selection.json',root):
                        raise ProbeError('selected_sample_changed')
                    saved=read(root/'acquisition/selection.json');material,params=saved['material'],saved['params']
                else:material,params=adapter.select(normalized,root,gateway)
                selected={'material':material,'params':params}
                saved_selection=root/'acquisition/selection.json'
                if saved_selection.exists() and fingerprint(read(saved_selection))!=fingerprint(selected):
                    raise ProbeError('selected_sample_changed')
                # Bind the selected sample before submitting any detail task.
                write(root/'acquisition/selection.json',{'material':material,'params':params})
                write(binding,{'requestSha256':digest(root/'request.json'),'source':read(root/'acquisition/selection-source.json'),'selection':artifact(root/'acquisition/selection.json',root)})
                transition(root,'detail_rpa',firstBatchCsvGate={'status':'unconfirmed'})
                release_completed(root,'selection_complete')
                check_stage(root,'detail_rpa')
                csv_path=gateway.csv('detail',adapter.DETAIL_CODE,params,normalized['rpa_shop'])
                transition(root,'detail_csv_validation')
                accepted=root/'acquisition/csv-receipt.json'
                if accepted.exists() and artifact(csv_path,root)!=read(accepted):raise ProbeError('accepted_csv_changed')
                try:
                    evidence=adapter.video_source(csv_path,material,params)
                except Exception as exc:
                    if getattr(exc,'code','').startswith('video_url_'):
                        write(accepted,artifact(csv_path,root))
                        transition(root,'video_source','failed',errorCode=exc.code,firstBatchCsvGate={'status':'passed'})
                        raise
                    transition(root,'detail_csv_validation','failed',errorCode='first_batch_no_valid_csv',firstBatchCsvGate={'status':'blocked'})
                    raise ProbeError('first_batch_no_valid_csv') from exc
                write(accepted,artifact(csv_path,root))
                write(root/'acquisition/source.json',evidence)
                transition(root,'video_download',firstBatchCsvGate={'status':'passed'})
                release_completed(root,'csv_complete')
                check_stage(root,'video_download')
                video=root/'media/source-video.mp4'
                if video.exists():
                    prior=read(root/'media/download-receipt.json')
                    if digest(video)!=prior['sha256']:raise ProbeError('artifact_changed')
                else:
                    download(evidence['url'],video,max_bytes=VIDEO_LIMIT,receipt_path=root/'media/download-receipt.json',receipt_root=root)
                transition(root,'media_probe')
                release_completed(root,'video_download_complete')
                check_stage(root,'media_probe')
                media=media_probe(video)
                validation=compare_media(evidence.get('expectedMedia',{}),media)
                write(root/'media/probe.json',{'videoSha256':read(root/'media/download-receipt.json')['sha256'],'media':media,'validation':validation})
                if validation['status']!='matched':raise ProbeError('video_identity_mismatch')
                artifacts=[artifact(p,root) for p in sorted((root/'acquisition').glob('*.csv'))]+[artifact(root/'acquisition/selection-source.json',root)]
                source_ref=read(root/'acquisition/selection-source.json');artifacts.append(artifact(safe_file(root,source_ref['path']),root))
                artifacts+=[artifact(root/'acquisition/selection.json',root),artifact(root/'acquisition/source.json',root),artifact(accepted,root),read(root/'media/download-receipt.json'),artifact(root/'media/download-receipt.json',root),artifact(root/'media/probe.json',root)]
                artifacts.append(artifact(binding,root))
                artifacts=list({item['path']:item for item in artifacts}.values())
                write(root/'acquisition/receipt.json',{'status':'video_ready','materialId':evidence['materialId'],'media':media,'requestSha256':digest(root/'request.json'),'artifacts':artifacts})
                transition(root,'acquisition','video_ready')
                release_completed(root,'acquisition_complete')
            except Exception as exc:
                state=read(root/'status.json');code=getattr(exc,'code','acquisition_failed')
                write(root/'failure.json',{'stage':state['stage'],'errorCode':code,'exceptionType':type(exc).__name__,'message':safe_error(exc)})
                # No valid CSV is a circuit breaker; download/probe failures are separate.
                if state['stage']=='detail_rpa' and code in ('csv_unavailable','csv_artifact_ambiguous','csv_file_url_ambiguous','download_empty','download_error_page'):
                    transition(root,state['stage'],'failed',errorCode='first_batch_no_valid_csv',firstBatchCsvGate={'status':'blocked'})
                else:transition(root,state['stage'],'paused' if code=='insufficient_stage_headroom' else 'failed',errorCode=code)
                resources.sample()
                report(root)
                raise
            finally:
                session=getattr(gateway,'session',None)
                if session is not None and hasattr(session,'close'):session.close()
                release_completed(root,'acquisition_exit')
        result=report(root)
        release_completed(root,'report_complete')
        return result

def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description='A获取一条视频，B离线CV切分与资源测试；无ASR/模型调用')
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('preflight');p.add_argument('--cv',action='store_true');p.add_argument('--backend',choices=['ffmpeg-scene','adaptive'],default='ffmpeg-scene')
    p=commands.add_parser('probe-cv');p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--profile',choices=['low-memory'],default='low-memory');p.add_argument('--attempt-id');p.add_argument('--config-file',type=Path);p.add_argument('--backend',choices=['ffmpeg-scene','adaptive'])
    p=commands.add_parser('verify-cv');p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--attempt-id')
    p=commands.add_parser('export-report');p.add_argument('--run-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    p=commands.add_parser('verify-export');p.add_argument('--bundle-dir',type=Path,required=True)
    for name in ('validate','acquire','resume'):
        p=commands.add_parser(name);p.add_argument('--request-file',type=Path,required=True)
        if name!='validate':p.add_argument('--output-dir',type=Path,required=True)
    for name in ('status','verify','render-report'):
        p=commands.add_parser(name);p.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='preflight':
            result=preflight()
            if args.cv:
                from cv_probe import dependency_status
                result['cv']=dependency_status(args.backend)
                if result['cv']['status']!='ready':result['status']='dependency_missing'
            print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='ready' else 2
        elif args.command=='probe-cv':
            from cv_probe import probe_cv
            result=probe_cv(external_root(args.run_dir),attempt_id=args.attempt_id,config_file=args.config_file,backend=args.backend)
        elif args.command=='verify-cv':
            from cv_probe import verify_cv
            receipt=verify_cv(external_root(args.run_dir),args.attempt_id);result={'status':'verified','cvStatus':receipt['status']}
        elif args.command=='export-report':
            from delivery import export_report
            result=export_report(external_root(args.run_dir),external_root(args.output_dir))
        elif args.command=='verify-export':
            from delivery import verify_export
            result=verify_export(external_root(args.bundle_dir))
        elif args.command=='validate':
            __import__('platform_adapter').validate(read(args.request_file));result={'status':'valid','stage':'A'}
        elif args.command in ('acquire','resume'):
            result=acquire(read(args.request_file),args.output_dir,resume=args.command=='resume')
        else:
            root=external_root(args.run_dir)
            if args.command=='status':
                state=read(root/'status.json');result={k:state.get(k) for k in ('status','stage','pid','updatedAt','errorCode','firstBatchCsvGate')}
                from cv_probe import cv_status
                cv=cv_status(root);result['cv']={k:cv.get(k) for k in ('status','attemptId','stage','errorCode','failureStage','shotCount')}
                result['nextAction']='export-report' if cv['status']=='succeeded' else ('probe-cv' if state['status']=='video_ready' and cv['status']=='not_run' else 'inspect_saved_state_no_automatic_retry')
            elif args.command=='verify':
                receipt=verify(root);result={'status':'verified','materialId':receipt['materialId'],'stage':'A_acquisition'}
            else:result=report(root)
        print(json.dumps(result,ensure_ascii=False));return 1 if result.get('status') in ('failed','interrupted') else 0
    except Exception as exc:
        print(json.dumps({'status':'failed','errorCode':getattr(exc,'code','input_or_contract_error'),'message':safe_error(exc)},ensure_ascii=False));return 1
