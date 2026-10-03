"""FFmpeg-native scene detection. Python receives metadata, never pixel arrays."""
from importlib.metadata import version
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
from probe_core import ProbeError,read,write,digest,safe_error
from cv_worker import shots_from_timeline,state

def jpeg_dimensions(path):
    try:
        if not 4<=path.stat().st_size<=1048576:return None
        with path.open('rb') as f:
            if f.read(2)!=b'\xff\xd8':return None
            f.seek(-2,2)
            if f.read(2)!=b'\xff\xd9':return None
            f.seek(2)
            while True:
                if f.read(1)!=b'\xff':return None
                marker=f.read(1)
                while marker==b'\xff':marker=f.read(1)
                if not marker:return None
                code=marker[0]
                if code in (0xd9,0xda):return None
                if code==0x01 or 0xd0<=code<=0xd7:continue
                raw=f.read(2)
                if len(raw)!=2:return None
                size=struct.unpack('>H',raw)[0]
                if size<2:return None
                if code in (0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf):
                    raw=f.read(5)
                    if len(raw)!=5:return None
                    h,w=struct.unpack('>HH',raw[1:]);return (w,h) if min(w,h)>0 else None
                f.seek(size-2,1)
    except OSError:return None

def decode(video,root,config,ffmpeg,media):
    scale=f"scale=w='min({config['maxDimension']},iw)':h='min({config['maxDimension']},ih)':force_original_aspect_ratio=decrease,setsar=1,format=yuv420p"
    filters=scale+f",scdet=threshold={config['sceneThreshold']},metadata=mode=print:key=lavfi.scd.score:file=-,showinfo"
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','info','-nostats','-xerror','-threads','1','-copyts','-i',str(video),'-map','0:v:0','-an','-sn','-dn','-filter_threads','1','-vf',filters,'-vsync','0','-threads','1','-f','null',os.devnull]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
    observation={'last':None,'count':0};reader_errors=[]
    def drain():
        try:
            with (root/'decoder.stderr.log').open('w',encoding='utf-8') as log:
                for line in process.stderr:
                    log.write(line)
                    if 'showinfo' not in line:continue
                    n=re.search(r'\bn:\s*(\d+)',line);pts=re.search(r'\bpts_time:([\d.eE+-]+)',line);size=re.search(r'\bs:(\d+)x(\d+)',line);duration=re.search(r'\bduration_time:([\d.eE+-]+)',line)
                    if n and pts and size:
                        observation['last']={'n':int(n[1]),'pts':float(pts[1]),'size':(int(size[1]),int(size[2])),'duration':float(duration[1]) if duration else None};observation['count']+=1
        except Exception as exc:reader_errors.append(type(exc).__name__)
    thread=threading.Thread(target=drain,daemon=True);thread.start()
    count=0;first=last=None;interval=None;variable=False;cuts=[(0,0.0)];pending=None
    try:
        with (root/'timeline.ndjson').open('w') as timeline:
            for line in process.stdout:
                if line.startswith('frame:'):
                    if pending is not None:raise ProbeError('decode_timeline_incomplete')
                    match=re.search(r'frame:(\d+).*pts_time:([\d.eE+-]+)',line)
                    if not match:raise ProbeError('decode_timeline_invalid')
                    pending=(int(match[1]),float(match[2]))
                elif line.startswith('lavfi.scd.score='):
                    if pending is None:raise ProbeError('decode_timeline_invalid')
                    n,pts=pending;pending=None;score=float(line.split('=',1)[1])
                    if n!=count or not math.isfinite(pts) or not math.isfinite(score):raise ProbeError('decode_timeline_invalid')
                    if first is None:first=pts
                    if last is not None:
                        if pts<=last:raise ProbeError('decode_pts_not_increasing')
                        gap=pts-last
                        if interval is not None and abs(gap-interval)>1e-5:variable=True
                        interval=gap
                    sec=pts-first
                    timeline.write(__import__('json').dumps({'frame':count,'sourcePtsSec':pts,'relativeSec':sec,'sceneScore':score})+'\n')
                    if count and score>=config['sceneThreshold'] and sec-cuts[-1][1]+1e-9>=config['minShotSec']:
                        cuts.append((count,sec))
                        with (root/'boundaries.ndjson').open('a') as log:log.write(__import__('json').dumps({'startFrame':count,'startSec':sec,'sceneScore':score})+'\n')
                        if len(cuts)>config['maxShots']:raise ProbeError('output_limit')
                    count+=1;last=pts
                    if count>10801:raise ProbeError('input_frame_limit')
                    if count%30==0:state(root,'cv_decode_detect',decodedFrames=count,lastRelativeSec=sec)
            code=process.wait();thread.join(timeout=2)
            if code!=0:raise ProbeError('decode_failed')
            if reader_errors or thread.is_alive():raise ProbeError('decoder_log_reader_failed')
            packet=observation['last']
            if pending or observation['count']!=count or not packet or packet['n']!=count-1 or abs(packet['pts']-last)>1e-5:raise ProbeError('decode_timeline_incomplete')
            if min(packet['size'])<=0 or max(packet['size'])>config['maxDimension']:raise ProbeError('decode_frame_shape_invalid')
            duration=packet['duration'] if packet['duration'] and packet['duration']>0 else (interval or 1/media['fps'])
            end=last-first+duration
            if end<=0 or abs(end-(media.get('videoDurationSec') or media['durationSec']))>max(.1,2/media['fps']):raise ProbeError('decode_duration_incomplete')
            return {'cuts':cuts,'frameCount':count,'durationSec':end,'sourceStartPtsSec':first,'variableFrameIntervalsObserved':variable,'timeMapping':'native_pts_no_cfr_conversion','scaledDimensions':list(packet['size'])}
    finally:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close();process.stderr.close();thread.join(timeout=2)

def run(job):
    root=Path(job['attemptDir']);config=job['config'];video=Path(job['videoPath']);state(root,'cv_dependency_import')
    import imageio_ffmpeg
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    binary=subprocess.run([ffmpeg,'-version'],capture_output=True,text=True,timeout=10)
    supported=subprocess.run([ffmpeg,'-hide_banner','-h','filter=scdet'],capture_output=True,text=True,timeout=10)
    if binary.returncode:raise ProbeError('ffmpeg_unavailable')
    if 'Detect video scene change' not in supported.stdout+supported.stderr:raise ProbeError('ffmpeg_scdet_unavailable')
    write(root/'worker-environment.json',{'backend':'ffmpeg-scene','dependencies':{'imageio-ffmpeg':version('imageio-ffmpeg')},'ffmpegVersion':binary.stdout.splitlines()[0],'ffmpegSha256':digest(ffmpeg),'pixelArraysInPython':False,'heavyCvImports':False,'ffmpegThreads':1})
    if digest(video)!=job['videoSha256']:raise ProbeError('artifact_changed')
    state(root,'cv_decode_detect');decoded=decode(video,root,config,ffmpeg,job['media']);shots=shots_from_timeline(root,decoded)
    data={**{k:v for k,v in decoded.items() if k!='cuts'},'detector':'FFmpeg scdet','backend':'ffmpeg-scene','detectionStatus':'succeeded','representativeStatus':'pending','shots':shots};write(root/'shots.json',data)
    state(root,'cv_representative_frames',shotCount=len(shots));frames=root/'frames';frames.mkdir()
    selection='+'.join(f'eq(n,{s["representativeFrame"]})' for s in shots)
    scale=f"scale=w='min({config['maxDimension']},iw)':h='min({config['maxDimension']},ih)':force_original_aspect_ratio=decrease,setsar=1"
    command=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-xerror','-threads','1','-i',str(video),'-map','0:v:0','-an','-sn','-dn','-filter_threads','1','-vf',f"select='{selection}',{scale}",'-vsync','0','-threads','1','-q:v','3',str(frames/'shot-%03d.jpg')]
    with (root/'frames.stderr.log').open('w') as log:result=subprocess.run(command,stdout=subprocess.DEVNULL,stderr=log)
    valid=result.returncode==0
    for shot in shots:
        size=jpeg_dimensions(root/shot['frameRef'])
        if size is None or max(size)>config['maxDimension']:shot['representativeStatus']='missing';valid=False
        else:shot['representativeStatus']='available'
    data['representativeStatus']='succeeded' if valid else 'failed';write(root/'shots.json',data)
    if not valid:raise ProbeError('representative_frames_failed')
    state(root,'cv_worker_complete',workerResult='succeeded',decodedFrames=decoded['frameCount'],shotCount=len(shots))

if __name__=='__main__':
    job=read(sys.argv[1]);root=Path(job['attemptDir'])
    try:run(job)
    except Exception as exc:
        code=getattr(exc,'code','cv_import_failed' if isinstance(exc,ImportError) else 'cv_worker_failed');write(root/'worker-failure.json',{'stage':read(root/'status.json').get('stage'),'errorCode':code,'exceptionType':type(exc).__name__,'message':safe_error(exc)});state(root,'cv_worker_failed',workerResult='failed',errorCode=code);sys.exit(1)
