"""One-video CV worker: bounded frame pipe, native PTS, serial representative frames."""
from collections import deque
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

from probe_core import ProbeError, read, write, digest, safe_error

EXPECTED={'numpy':'2.2.6','opencv-python-headless':'4.11.0.86','scenedetect':'0.6.7.1'}

def state(root,phase,**extra):
    value=read(root/'status.json');value.update(stage=phase,**extra);write(root/'status.json',value)

def exact_frame(stream,size):
    data=bytearray(size);view=memoryview(data);offset=0
    while offset<size:
        amount=stream.readinto(view[offset:])
        if not amount:raise ProbeError('decode_short_frame')
        offset+=amount
    return data

def decode(video,root,config,ffmpeg,media,cv2,np,detector):
    packets=queue.Queue(maxsize=8);stop=threading.Event();reader_errors=[]
    limit=config['maxDimension']
    scale=f"scale=w='min({limit},iw)':h='min({limit},ih)':force_original_aspect_ratio=decrease,setsar=1,format=bgr24,showinfo"
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','info','-nostats','-xerror','-threads','1','-copyts','-i',str(video),'-map','0:v:0','-an','-sn','-dn','-filter_threads','1','-vf',scale,'-vsync','0','-threads','1','-pix_fmt','bgr24','-f','rawvideo','pipe:1']
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    def put(value):
        while not stop.is_set():
            try:packets.put(value,timeout=0.2);return
            except queue.Full:pass
    def drain():
        try:
            with (root/'decoder.stderr.log').open('w',encoding='utf-8') as log:
                for raw in iter(process.stderr.readline,b''):
                    line=raw.decode(errors='replace');log.write(line)
                    n=re.search(r'\bn:\s*(\d+)',line);pts=re.search(r'\bpts_time:([\d.eE+-]+)',line);size=re.search(r'\bs:(\d+)x(\d+)',line)
                    if 'showinfo' in line and n and pts and size:
                        duration=re.search(r'\bduration_time:([\d.eE+-]+)',line)
                        put({'n':int(n[1]),'pts':float(pts[1]),'width':int(size[1]),'height':int(size[2]),'duration':float(duration[1]) if duration else None})
        except Exception as exc:reader_errors.append(type(exc).__name__)
        finally:put(None)
    thread=threading.Thread(target=drain,daemon=True);thread.start()
    first=None;last=None;count=0;ring={};cuts=[(0,0.0)];last_image=None;variable=False;interval=None
    def register(events):
        for event in events:
            event=int(event)
            if event<=cuts[-1][0] or event>=count:continue
            if event not in ring:raise ProbeError('boundary_pts_missing')
            seconds=ring[event]-first
            if seconds-cuts[-1][1]+1e-9<config['minShotSec']:continue
            with (root/'boundaries.ndjson').open('a',encoding='utf-8') as handle:
                handle.write(json.dumps({'startFrame':event,'startSec':seconds})+'\n');handle.flush()
            cuts.append((event,seconds))
            if len(cuts)>config['maxShots']:raise ProbeError('output_limit')
    try:
        with (root/'timeline.ndjson').open('w',encoding='utf-8') as timeline:
            while True:
                packet=packets.get()
                if packet is None:break
                if packet['n']!=count or not math.isfinite(packet['pts']):raise ProbeError('decode_timeline_invalid')
                if first is None:first=packet['pts']
                if last and packet['pts']<=last['pts']:raise ProbeError('decode_pts_not_increasing')
                if last:
                    gap=packet['pts']-last['pts']
                    if interval is not None and abs(gap-interval)>1e-5:variable=True
                    interval=gap
                w,h=packet['width'],packet['height']
                if min(w,h)<=0 or max(w,h)>limit:raise ProbeError('decode_frame_shape_invalid')
                data=exact_frame(process.stdout,w*h*3)
                image=np.frombuffer(data,dtype=np.uint8).reshape(h,w,3)
                timeline.write(json.dumps({'frame':count,'sourcePtsSec':packet['pts'],'relativeSec':packet['pts']-first,'durationSec':packet['duration']})+'\n')
                ring[count]=packet['pts'];count+=1
                if len(ring)>64:del ring[min(ring)]
                register(detector.process_frame(count-1,image))
                last=packet;last_image=image
                if count>10801:raise ProbeError('input_frame_limit')
                if count%30==0:state(root,'cv_decode_detect',decodedFrames=count,lastRelativeSec=packet['pts']-first)
            if reader_errors:raise ProbeError('decoder_log_reader_failed')
            if process.stdout.read(1):raise ProbeError('decode_timeline_incomplete')
            if process.wait()!=0:raise ProbeError('decode_failed')
            if count==0:raise ProbeError('decode_empty')
            # Flush the finite detector window using the last image; these are not decoded input frames.
            for offset in range(config['windowWidth']):register(detector.process_frame(count+offset,last_image))
        duration=last['duration'] if last['duration'] and last['duration']>0 else (interval or 1/media['fps'])
        end=last['pts']-first+duration
        expected=media.get('videoDurationSec') or media['durationSec']
        if end<=0 or abs(end-expected)>max(0.1,2/media['fps']):raise ProbeError('decode_duration_incomplete')
        return {'cuts':cuts,'frameCount':count,'durationSec':end,'sourceStartPtsSec':first,'variableFrameIntervalsObserved':variable,'timeMapping':'native_pts_no_cfr_conversion','scaledDimensions':[last['width'],last['height']]}
    finally:
        stop.set()
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close();process.stderr.close();thread.join(timeout=2)

def shots_from_timeline(root,decoded):
    cuts=decoded['cuts'];bounds=cuts+[(decoded['frameCount'],decoded['durationSec'])];shots=[]
    for index,((start,sec),(end,end_sec)) in enumerate(zip(bounds,bounds[1:]),1):
        shots.append({'shotId':f'shot-{index:03d}','startFrame':start,'endFrame':end,'startSec':sec,'endSec':end_sec,'durationSec':end_sec-sec,'targetRepresentativeTimeSec':(sec+end_sec)/2,'representativeFrame':start,'representativeTimeSec':sec,'frameRef':f'frames/shot-{index:03d}.jpg','representativeStatus':'pending'})
    cursor=0
    with (root/'timeline.ndjson').open() as handle:
        for line in handle:
            frame=json.loads(line)
            while cursor<len(shots)-1 and frame['frame']>=shots[cursor]['endFrame']:cursor+=1
            shot=shots[cursor]
            if frame['relativeSec']<=shot['targetRepresentativeTimeSec'] and frame['frame']>=shot['startFrame']:
                shot.update(representativeFrame=frame['frame'],representativeTimeSec=frame['relativeSec'])
    return shots

def run(job):
    root=Path(job['attemptDir']);config=job['config'];video=Path(job['videoPath'])
    state(root,'cv_dependency_import')
    import cv2
    import numpy as np
    import imageio_ffmpeg
    from scenedetect.detectors import AdaptiveDetector
    packages={name:version(name) for name in EXPECTED}
    if packages!=EXPECTED:raise ProbeError('cv_dependency_version_mismatch')
    cv2.setNumThreads(1);cv2.ocl.setUseOpenCL(False)
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    binary=subprocess.run([ffmpeg,'-version'],capture_output=True,text=True,timeout=10)
    if binary.returncode:raise ProbeError('ffmpeg_unavailable')
    write(root/'worker-environment.json',{'dependencies':packages,'cv2Threads':cv2.getNumThreads(),'ffmpegVersion':binary.stdout.splitlines()[0],'ffmpegSha256':digest(ffmpeg),'threadEnvironment':{k:os.environ.get(k) for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS')}})
    if digest(video)!=job['videoSha256']:raise ProbeError('artifact_changed')
    detector=AdaptiveDetector(adaptive_threshold=config['adaptiveThreshold'],window_width=config['windowWidth'],min_content_val=config['minContentVal'],min_scene_len=max(1,math.ceil(job['media']['fps']*config['minShotSec'])))
    state(root,'cv_decode_detect')
    decoded=decode(video,root,config,ffmpeg,job['media'],cv2,np,detector)
    shots=shots_from_timeline(root,decoded)
    data={**{k:v for k,v in decoded.items() if k!='cuts'},'detector':'AdaptiveDetector','detectionStatus':'succeeded','representativeStatus':'pending','shots':shots}
    write(root/'shots.json',data)
    state(root,'cv_representative_frames',shotCount=len(shots))
    frames=root/'frames';frames.mkdir()
    selection='+'.join(f'eq(n,{s["representativeFrame"]})' for s in shots)
    scale=f"scale=w='min({config['maxDimension']},iw)':h='min({config['maxDimension']},ih)':force_original_aspect_ratio=decrease,setsar=1"
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-xerror','-threads','1','-i',str(video),'-map','0:v:0','-an','-sn','-dn','-filter_threads','1','-vf',f"select='{selection}',{scale}",'-vsync','0','-threads','1','-q:v','3',str(frames/'shot-%03d.jpg')]
    with (root/'frames.stderr.log').open('w') as log:
        result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=log)
    valid=result.returncode==0
    for shot in shots:
        path=root/shot['frameRef'];image=cv2.imread(str(path)) if path.exists() else None
        if image is None or max(image.shape[:2])>config['maxDimension']:shot['representativeStatus']='missing';valid=False
        else:shot['representativeStatus']='available'
    data['representativeStatus']='succeeded' if valid else 'failed';write(root/'shots.json',data)
    if not valid:raise ProbeError('representative_frames_failed')
    state(root,'cv_worker_complete',workerResult='succeeded',decodedFrames=decoded['frameCount'],shotCount=len(shots))

if __name__=='__main__':
    job=read(sys.argv[1]);root=Path(job['attemptDir'])
    try:run(job)
    except Exception as exc:
        code=getattr(exc,'code','cv_import_failed' if isinstance(exc,ImportError) else 'cv_worker_failed')
        write(root/'worker-failure.json',{'stage':read(root/'status.json').get('stage'),'errorCode':code,'exceptionType':type(exc).__name__,'message':safe_error(exc)})
        state(root,'cv_worker_failed',workerResult='failed',errorCode=code)
        sys.exit(1)
