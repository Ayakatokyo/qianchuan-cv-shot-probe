"""Self-contained visual delivery. No video copies, ZIPs, or remote dependencies."""
import base64
import html
import os
from pathlib import Path
import tempfile
from probe_core import ROOT, ProbeError, read, write, artifact, digest, safe_file, run_lock
from runtime_memory import StageMonitor, check_stage, release_completed, release_owned

IMAGE_LIMIT=256*1024
IMAGE_BUDGET=12*1024*1024
REPORT_LIMIT=2*1024*1024
LABELS={'succeeded':'已完成','video_ready':'视频已就绪','not_run':'尚未分镜','failed':'执行失败','paused':'已暂停','pending':'未执行','partial':'部分完成','running':'执行中','interrupted':'执行中断'}

def esc(value):return html.escape(str(value if value is not None else '未记录'),quote=True)
def label(value):return LABELS.get(value,value or '未记录')
def seconds(value):return f'{float(value or 0):.2f}'

CSS=r'''
:root{color-scheme:light;--bg:oklch(1 0 0);--surface:oklch(.975 .003 250);--ink:oklch(.24 .025 255);--muted:oklch(.46 .025 255);--line:oklch(.88 .015 255);--primary:oklch(.48 .17 260);--soft:oklch(.95 .025 260);--success:oklch(.38 .09 155);--warning:oklch(.4 .09 50);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;color:var(--ink);background:var(--bg)}
*{box-sizing:border-box}body{margin:0}button,input,select{font:inherit}button,select{cursor:pointer}button{color:inherit;border:1px solid var(--line);border-radius:6px;background:white;padding:9px 14px}button:hover{border-color:var(--primary);background:var(--soft)}button:disabled{opacity:.5;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid var(--primary);outline-offset:3px}header{padding:24px 32px 20px;border-bottom:1px solid var(--line)}.brand{font-weight:650;color:var(--primary);margin:0 0 12px}h1{font-size:26px;line-height:1.4;margin:0 0 8px}h2{font-size:20px;margin:0 0 14px}h3{font-size:16px;margin:0 0 10px}p{line-height:1.65}.muted,.caption{color:var(--muted)}.intro{margin:0;max-width:75ch}.counts{display:flex;gap:32px;margin:22px 0 0;flex-wrap:wrap}.count strong{font-size:24px;display:block}.count span{font-size:13px;color:var(--muted)}.layout{display:grid;grid-template-columns:248px minmax(0,1fr);min-height:80vh}aside{padding:24px 16px;border-right:1px solid var(--line);background:var(--surface)}.search{width:100%;padding:10px;border:1px solid var(--line);border-radius:6px;margin-bottom:16px;background:white}.material-nav{display:flex;flex-direction:column;gap:8px}.material-nav button{text-align:left;width:100%;line-height:1.5}.material-nav button.active{color:var(--primary);background:var(--soft);border-color:var(--primary)}.material-nav small{display:block;color:var(--muted);font-size:12px;margin-top:4px}.empty-search{font-size:13px;color:var(--muted)}main{padding:28px 32px;min-width:0}.material-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.material-heading h2{margin-bottom:5px;overflow-wrap:anywhere}.badge{font-size:13px;white-space:nowrap;background:var(--soft);color:var(--primary);padding:6px 10px;border-radius:4px}.meta{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0 22px;font-size:14px;color:var(--muted)}.viewer{display:grid;grid-template-columns:minmax(220px,.9fr) minmax(240px,1.1fr);gap:28px;margin-bottom:24px}.stage{background:oklch(.17 .008 255);display:flex;align-items:center;justify-content:center;min-height:320px;max-height:470px;border-radius:8px;overflow:hidden}.stage img{max-width:100%;width:auto;max-height:470px;object-fit:contain}.stage .empty-frame{color:oklch(.9 0 0);padding:32px;text-align:center}.shot-detail{padding:16px 0}.shot-number{font-size:14px;color:var(--primary);font-weight:650}.time-value{font-size:30px;font-variant-numeric:tabular-nums;line-height:1.4;margin:14px 0 8px}.shot-detail .controls{display:flex;gap:10px;margin:22px 0}.note{font-size:13px;color:var(--muted);max-width:60ch}.timeline{display:flex;gap:3px;height:36px;margin:14px 0 6px;overflow:hidden}.timeline button{min-width:3px;flex-basis:0;border:0;border-radius:3px;background:var(--soft);padding:0}.timeline button.active{background:var(--primary)}.timeline button:hover{background:oklch(.78 .1 260)}.scale{display:flex;justify-content:space-between;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}.section-bar{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-top:30px}.section-bar h3{margin:0}.section-bar select{padding:8px;border:1px solid var(--line);border-radius:5px;background:white}.filmstrip{display:grid;grid-template-columns:repeat(auto-fill,minmax(125px,1fr));gap:14px;margin-top:16px}.shot{padding:0;text-align:left;overflow:hidden;border-radius:6px}.shot.active{border:2px solid var(--primary)}.shot-image{display:flex;align-items:center;justify-content:center;height:160px;background:var(--surface);overflow:hidden}.shot img{height:100%;width:100%;object-fit:contain}.shot .text{padding:10px;display:block}.shot small{display:block;color:var(--muted);font-size:12px;margin-top:5px;font-variant-numeric:tabular-nums}.missing{font-size:12px;color:var(--muted);text-align:center;padding:10px}.technical{margin-top:30px;border-top:1px solid var(--line);padding-top:20px}.technical summary{cursor:pointer;color:var(--muted)}.technical dl{display:grid;grid-template-columns:140px minmax(0,1fr);gap:12px;font-size:13px}.technical dt{color:var(--muted)}.technical dd{margin:0;overflow-wrap:anywhere}.alert{border-left:3px solid var(--warning);padding:12px 16px;background:var(--surface);line-height:1.6}.hidden,[hidden]{display:none!important}footer{border-top:1px solid var(--line);padding:20px 32px;color:var(--muted);font-size:12px;line-height:1.8}.no-js{background:var(--soft);padding:16px}
@media(max-width:900px){.layout{grid-template-columns:200px minmax(0,1fr)}main{padding:24px 20px}.viewer{grid-template-columns:1fr}.stage{min-height:280px;max-height:360px}.stage img{max-height:360px}.shot-detail{padding:0}.time-value{font-size:26px}}
@media(max-width:600px){header{padding:20px}h1{font-size:23px}.counts{gap:20px}.layout{display:block}aside{border-right:0;border-bottom:1px solid var(--line);padding:16px}.material-nav{flex-direction:row;overflow-x:auto}.material-nav button{min-width:180px;max-width:220px}.search{margin-bottom:10px}.material-heading{display:block}.badge{display:inline-block;margin-top:10px}main{padding:20px}.meta{gap:12px}.filmstrip{grid-template-columns:repeat(2,minmax(0,1fr))}.timeline{height:44px}.technical dl{grid-template-columns:1fr;gap:6px}.technical dd{margin-bottom:12px}}
@media print{.layout{display:block}aside,.viewer,.controls,.timeline,.section-bar select,.technical{display:none}main{padding:0}.material-panel[hidden]{display:block!important}.material-panel{break-before:page}.filmstrip{grid-template-columns:repeat(4,1fr)}.shot{break-inside:avoid}.shot-image{height:140px}.shot[hidden]{display:block!important}header,footer{padding:16px}.section-bar{margin-top:12px}}
'''
JS=r'''
(()=>{const panels=[...document.querySelectorAll('.material-panel')],nav=[...document.querySelectorAll('[data-material]')];
function chooseShot(panel,index){const shots=[...panel.querySelectorAll('.shot')];if(!shots.length)return;index=Math.max(0,Math.min(shots.length-1,index));panel.dataset.selected=index;shots.forEach((s,i)=>{s.classList.toggle('active',i===index);s.setAttribute('aria-pressed',i===index)});panel.querySelectorAll('.timeline button').forEach((b,i)=>{b.classList.toggle('active',i===index);b.setAttribute('aria-pressed',i===index)});const shot=shots[index],image=shot.querySelector('img'),stage=panel.querySelector('.stage');stage.replaceChildren();if(image){const large=image.cloneNode();large.removeAttribute('loading');stage.append(large)}else{const e=document.createElement('p');e.className='empty-frame';e.textContent=shot.querySelector('.missing').textContent;stage.append(e)}panel.querySelector('.shot-number').textContent='镜头 '+String(index+1).padStart(2,'0')+' / '+shots.length;panel.querySelector('.time-value').textContent=shot.dataset.start+' – '+shot.dataset.end+' 秒';panel.querySelector('.duration').textContent='持续 '+Number(shot.dataset.duration).toFixed(2)+' 秒 · 代表帧 '+shot.dataset.representative+' 秒';panel.querySelector('[data-prev]').disabled=index===0;panel.querySelector('[data-next]').disabled=index===shots.length-1}
function chooseMaterial(id){panels.forEach(p=>p.hidden=p.id!==id);nav.forEach(b=>{const selected=b.dataset.material===id;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',selected)});const panel=document.getElementById(id);chooseShot(panel,Number(panel.dataset.selected||0))}
nav.forEach(b=>b.addEventListener('click',()=>chooseMaterial(b.dataset.material)));panels.forEach(p=>{p.querySelectorAll('[data-shot]').forEach(b=>b.addEventListener('click',()=>chooseShot(p,Number(b.dataset.shot))));p.querySelectorAll('[data-prev],[data-next]').forEach(b=>b.addEventListener('click',()=>chooseShot(p,Number(p.dataset.selected||0)+(b.hasAttribute('data-next')?1:-1))));p.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName))return;if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();chooseShot(p,Number(p.dataset.selected||0)+(e.key==='ArrowRight'?1:-1))}});const filter=p.querySelector('.duration-filter');if(filter)filter.addEventListener('change',()=>{const minimum=Number(filter.value);let visible=0;p.querySelectorAll('.shot').forEach(s=>{s.hidden=Number(s.dataset.duration)<minimum;if(!s.hidden)visible++});p.querySelector('.visible-count').textContent='显示 '+visible+' / '+p.querySelectorAll('.shot').length+' 镜头'})});
const search=document.querySelector('.search');search.addEventListener('input',()=>{const q=search.value.trim().toLowerCase();nav.forEach(b=>b.hidden=!b.textContent.toLowerCase().includes(q));document.querySelector('.empty-search').hidden=nav.some(b=>!b.hidden)});if(panels.length)chooseMaterial(panels[0].id);
})();
'''


def write_visual(target, items, summary=None, monitor=None):
    """Stream sections + each bounded JPEG. items=(public report, CV frame root)."""
    summary=summary or {};budget=IMAGE_BUDGET;embedded=omitted=0
    def check():
        if monitor:monitor.check()
    with Path(target).open('w',encoding='utf-8') as out:
        def emit(value):check();out.write(value)
        platforms={p.get('platform') for p,_ in items}
        title='双平台' if len(platforms)>1 else ('千川' if 'qianchuan' in platforms else '云图')
        completed=sum(p.get('cv',{}).get('status')=='succeeded' for p,_ in items)
        shot_count=sum(len(p.get('cv',{}).get('shots',[])) for p,_ in items)
        requested=summary.get('requestedCount',len(items))
        emit('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+title+'视频分镜工作台</title><style>'+CSS+'</style></head><body>')
        emit('<header><p class="brand">'+title+' · 素材工作台</p><h1>视频分镜 · 从素材到每个镜头</h1><p class="intro muted">查看代表帧、比较镜头节奏，定位值得复盘的画面。点击时间轴或镜头缩略图，切换大图。</p><div class="counts">')
        for value,text in ((requested,'请求素材'),(completed,'完成分镜'),(shot_count,'镜头总数')):emit('<div class="count"><strong>'+str(value)+'</strong><span>'+text+'</span></div>')
        emit('</div></header><noscript><p class="no-js">浏览器未启用脚本。以下仍可阅读全部镜头与代表帧；素材切换和筛选需要启用 JavaScript。</p></noscript><div class="layout"><aside><h3>素材列表</h3><input class="search" type="search" aria-label="搜索素材名称或ID" placeholder="搜索名称或素材ID"><nav class="material-nav" aria-label="素材选择">')
        for i,(public,_) in enumerate(items):
            cv=public.get('cv',{});name=public.get('materialName') or ('素材 '+str(public.get('materialId') or i+1))
            emit('<button data-material="material-'+str(i)+'" aria-pressed="false">'+esc(name)+'<small>'+esc(public.get('materialId'))+' · '+esc(label(cv.get('status')))+' · '+str(len(cv.get('shots',[])))+' 镜头</small></button>')
        emit('</nav><p class="empty-search" hidden>没有匹配的素材，请调整关键词。</p><p class="note">单次最多 10 条 · 串行处理<br>CV 结果为算法候选，镜头质量需人工核对。</p></aside><main>')
        if summary.get('status') in ('failed','partial'):
            emit('<p class="alert">本次'+esc(label(summary['status']))+'。已完成 '+str(summary.get('completedCount',completed))+' / '+str(requested)+' 条；未执行或失败结果不会计为完成。'+esc(summary.get('errorCode') or '筛选后素材不足，未自动补位')+'</p>')
        if not items:emit('<p class="alert">尚无可展示的分镜结果。请查看运行目录中的 batch.json 与阶段诊断，修正后再明确发起新运行。</p>')
        for i,(public,frame_root) in enumerate(items):
            cv=public.get('cv',{});shots=cv.get('shots') or [];media=public.get('media') or {};name=public.get('materialName') or ('素材 '+str(public.get('materialId') or i+1))
            emit('<section class="material-panel" id="material-'+str(i)+'" tabindex="0"><div class="material-heading"><div><h2>'+esc(name)+'</h2><span class="caption">素材ID '+esc(public.get('materialId'))+'</span></div><span class="badge">'+esc(label(cv.get('status')))+'</span></div><div class="meta">')
            for text in (str(len(shots))+' 个镜头',seconds(cv.get('durationSec') or media.get('durationSec'))+' 秒',str(media.get('width','?'))+' × '+str(media.get('height','?')),str(media.get('fps','?'))+' fps',public.get('periodLabel') or '日期范围未记录'):emit('<span>'+esc(text)+'</span>')
            emit('</div>')
            if shots:
                emit('<div class="viewer"><div class="stage"><p class="empty-frame">选择镜头查看大图</p></div><div class="shot-detail"><div class="shot-number" aria-live="polite">镜头 01</div><div class="time-value"></div><p class="duration muted"></p><div class="controls"><button data-prev aria-label="上一个镜头">← 上一镜头</button><button data-next aria-label="下一个镜头">下一镜头 →</button></div><p class="note">这是镜头内的代表帧。时间区间来自 CV 检测，代表帧不等同于完整画面内容；未进行转写或 AI 内容判断。</p><p class="note">键盘 ← / → 可切换镜头。完整视频保留在原运行目录，HTML 中仅内嵌代表帧。</p></div></div><h3>镜头节奏</h3><div class="timeline" aria-label="镜头时间轴">')
                for n,shot in enumerate(shots):
                    duration=float(shot['endSec'])-float(shot['startSec'])
                    emit('<button data-shot="'+str(n)+'" style="flex-grow:'+str(max(duration,.001))+'" aria-label="镜头 '+str(n+1)+'，'+seconds(shot['startSec'])+' 至 '+seconds(shot['endSec'])+' 秒" aria-pressed="false" title="镜头 '+str(n+1)+' · '+seconds(duration)+'秒"></button>')
                emit('</div><div class="scale"><span>0.00 秒</span><span>'+seconds(shots[-1]['endSec'])+' 秒</span></div><div class="section-bar"><h3>镜头画廊 <small class="visible-count muted">显示 '+str(len(shots))+' / '+str(len(shots))+' 镜头</small></h3><label>时长 <select class="duration-filter" aria-label="按镜头时长筛选"><option value="0">全部镜头</option><option value="1">至少 1 秒</option><option value="3">至少 3 秒</option><option value="5">至少 5 秒</option></select></label></div><div class="filmstrip">')
                for n,shot in enumerate(shots):
                    duration=float(shot['endSec'])-float(shot['startSec'])
                    emit('<button class="shot" data-shot="'+str(n)+'" data-start="'+seconds(shot['startSec'])+'" data-end="'+seconds(shot['endSec'])+'" data-duration="'+str(duration)+'" data-representative="'+seconds(shot['representativeTimeSec'])+'" aria-pressed="false"><span class="shot-image">')
                    reason=None;frame=None
                    if shot.get('representativeStatus')!='available':reason='代表帧未生成'
                    elif frame_root is None:reason='代表帧资源未提供'
                    else:
                        frame=safe_file(frame_root,shot['frameRef']);size=frame.stat().st_size
                        if size>IMAGE_LIMIT:reason='代表帧超过轻量报告单帧限制'
                        elif size>budget:reason='已达到轻量报告图片容量上限'
                    if reason:omitted+=1;emit('<span class="missing">'+reason+'</span>')
                    else:
                        check();raw=frame.read_bytes()
                        if not raw.startswith(b'\xff\xd8\xff'):raise ProbeError('report_frame_not_jpeg')
                        budget-=len(raw);embedded+=1
                        emit('<img loading="lazy" alt="镜头 '+str(n+1)+' 代表帧，'+seconds(shot['representativeTimeSec'])+' 秒" src="data:image/jpeg;base64,'+base64.b64encode(raw).decode('ascii')+'">')
                    emit('</span><span class="text">镜头 '+str(n+1).zfill(2)+'<small>'+seconds(shot['startSec'])+' – '+seconds(shot['endSec'])+' 秒 · '+seconds(duration)+'s</small></span></button>')
                emit('</div>')
            else:emit('<p class="alert">'+esc(public.get('errorCode') or '该素材尚未完成 CV 分镜，不能据此判断镜头质量。')+'</p>')
            obs=cv.get('memoryObservation') or public.get('memoryObservation') or {}
            emit('<details class="technical"><summary>查看执行与来源摘要</summary><dl>')
            for key,val in (('执行版本',cv.get('packageVersion') or (public.get('runtime') or {}).get('packageVersion')),('CV attempt',cv.get('attemptId')),('视频 SHA-256',(public.get('videoArtifact') or {}).get('sha256')),('worker 退出码',cv.get('exitCode')),('进程清理',(cv.get('processCleanup') or {}).get('status')),('观测采样数',obs.get('sampleCount')),('原始占用峰值 MiB',round((obs.get('peaks') or {})['rawUsageBytes']/1048576,2) if 'rawUsageBytes' in (obs.get('peaks') or {}) else None),('人工质量验收','待人工核对'),('内容分析','未进行 ASR / 模型分析')):
                emit('<dt>'+esc(key)+'</dt><dd>'+esc(val)+'</dd>')
            emit('</dl><p class="note">完整采样与诊断保留在原运行目录。采样可能漏掉瞬时峰值，资源完成与人工质量分别验收。</p></details></section>')
        emit('</main></div><footer>轻量交付 · 内嵌 '+str(embedded)+' 张代表帧'+(' · '+str(omitted)+' 张未嵌入（见对应占位原因）' if omitted else '')+' · 图片预算 12 MiB / 单帧 256 KiB<br>本文件可离线打开。源视频、CSV、账号、签名 URL 与逐条资源日志留在原运行目录；需审计时可显式导出技术包。</footer><script>'+JS+'</script></body></html>')
        out.flush();os.fsync(out.fileno())
    return {'embeddedFrames':embedded,'omittedFrames':omitted,'containsVideo':False,'selfContained':True}


def load_public(root, expected_attempt=None, artifact_root=None):
    """Verify the completed report snapshot + CV frames, without rereading MP4/CSV."""
    root=Path(root);path=safe_file(root,'report/report.json')
    if path.stat().st_size>REPORT_LIMIT:raise ProbeError('report_size_limit')
    receipt=read(safe_file(root,'report/receipt.json'))
    ref=next((a for a in receipt['artifacts'] if a['path']=='report/report.json'),None)
    if ref!=artifact(path,root):raise ProbeError('report_snapshot_changed')
    public=read(path);cv=public.get('cv',{});attempt=cv.get('attemptId')
    if len(cv.get('shots') or [])>300:raise ProbeError('report_shot_limit')
    if expected_attempt is not None and attempt!=expected_attempt:raise ProbeError('delivery_cv_attempt_changed')
    if cv.get('status')!='succeeded':return public,None
    folder=safe_file(artifact_root or root,'cv/'+attempt+'/receipt.json').parent;cv_receipt=read(folder/'receipt.json')
    if cv_receipt.get('status')!='succeeded' or cv_receipt.get('inputVideoSha256')!=(public.get('videoArtifact') or {}).get('sha256'):raise ProbeError('cv_input_changed')
    refs={a['path']:a for a in cv_receipt['artifacts']}
    for relative in ['shots.json']+[s['frameRef'] for s in cv.get('shots',[])]:
        if refs.get(relative)!=artifact(safe_file(folder,relative),folder):raise ProbeError('cv_artifact_changed')
    if read(folder/'shots.json')['shots']!=cv.get('shots'):raise ProbeError('report_snapshot_changed')
    return public,folder


def _export(items_factory,destination,observation_root):
    destination=Path(destination)
    if destination.suffix.lower()!='.html':raise ProbeError('report_html_extension_required')
    if destination.exists():raise ProbeError('delivery_output_exists')
    destination.parent.mkdir(parents=True,exist_ok=True)
    evidence=Path(observation_root)/'html-exports'/destination.stem
    evidence.mkdir(parents=True,exist_ok=False);monitor=StageMonitor(evidence,'html_precheck')
    staging=None;published=False;result=None;error=None
    try:
        with monitor:
            check_stage(evidence,'export');monitor.checkpoint('html_verify_snapshot')
            items,summary=items_factory()
            fd,staging=tempfile.mkstemp(prefix='.visual-report-',suffix='.html',dir=destination.parent);os.close(fd)
            monitor.checkpoint('html_render')
            stats=write_visual(staging,items,summary,monitor)
            monitor.checkpoint('html_cache_advice')
            release_owned(evidence,'html_written',destination.parent,[Path(staging)])
            monitor.checkpoint('html_publish')
            sha=digest(staging);monitor.check();os.link(staging,destination);published=True
            result={'status':'exported','htmlPath':str(destination),'htmlSha256':sha,'sizeBytes':destination.stat().st_size,**stats}
        monitor.check()
    except Exception as exc:
        error=getattr(exc,'code','html_export_failed');raise
    finally:
        if staging:Path(staging).unlink(missing_ok=True)
        write(evidence/'receipt.json',{'status':'failed' if error else 'exported','errorCode':error,'outputPublished':published,'result':result,'guardStopReason':monitor.failure})
    return result


def export_html(root,destination,*,expected_attempt=None):
    root=Path(root)
    if destination.resolve().is_relative_to(root.resolve()):raise ProbeError('delivery_output_overlap')
    with run_lock(root):
        release_completed(root,'html_export_precheck')
        return _export(lambda:([load_public(root,expected_attempt)],{}),destination,root)


def export_batch_html(root,destination):
    root=Path(root)
    def collect():
        batch=read(root/'batch.json');items=[]
        for entry in batch['entries']:
            if not entry.get('runDir'):continue
            run=Path(entry['runDir'])
            if batch.get('acquisition')!='reused_A_only' and not run.resolve().is_relative_to(root.resolve()):raise ProbeError('batch_output_overlap')
            if entry.get('reportSnapshot') and not Path(entry['reportSnapshot']).resolve().is_relative_to(root.resolve()):raise ProbeError('batch_output_overlap')
            if (run/'report/report.json').exists():
                with run_lock(run):items.append(load_public(Path(entry.get('reportSnapshot') or run),entry.get('attemptId') if entry.get('cvStatus')=='succeeded' else None,run))
        return items,batch
    return _export(collect,destination,root)
