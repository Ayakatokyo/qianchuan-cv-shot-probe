import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import probe_core as core
import cv_probe as cv
import test_acquisition as intake

class CvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import imageio_ffmpeg
        cls.temp=tempfile.TemporaryDirectory();cls.ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        intake.AcquisitionTests.setUpClass();cls.tiny=intake.AcquisitionTests.video.read_bytes()
        cls.multi=Path(cls.temp.name)/'16-cuts.mp4'
        cmd=[cls.ffmpeg,'-hide_banner','-loglevel','error']
        for index in range(16):cmd+=['-f','lavfi','-i',f"color=c={'red' if index%2==0 else 'blue'}:s=160x90:r=25:d=0.4"]
        chain=''.join(f'[{i}:v]' for i in range(16))+f'concat=n=16:v=1:a=0[v]'
        cmd+=['-filter_complex_threads','1','-filter_complex',chain,'-map','[v]','-an','-c:v','libx264','-preset','ultrafast','-threads','1','-pix_fmt','yuv420p',str(cls.multi)]
        subprocess.run(cmd,check=True)
        cls.vfr=Path(cls.temp.name)/'vfr.mp4'
        subprocess.run([cls.ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=red:s=160x90:r=10:d=1','-f','lavfi','-i','color=c=blue:s=160x90:r=25:d=1','-filter_complex_threads','1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-an','-c:v','libx264','-threads','1','-enc_time_base','1:1000','-vsync','vfr',str(cls.vfr)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup();intake.AcquisitionTests.tearDownClass()
    def setUp(self):
        intake.AcquisitionTests.video.write_bytes(self.tiny)
        self.helper=intake.AcquisitionTests(methodName='runTest');self.helper.setUp()
        self.root=Path(self.helper.root_temp.name)/'run'
    def tearDown(self):self.helper.tearDown()
    def acquire(self,video=None):
        if video:
            # The local fixture server captured its original file; replace only the synthetic fixture contents.
            self.helper.video.write_bytes(Path(video).read_bytes())
        core.acquire(intake.request(),self.root)
    def config(self,**overrides):
        data=cv.config_value();data.update(overrides);p=Path(self.helper.root_temp.name)/'cv-config.json';core.write(p,data);return p
    def test_full_hard_cuts_more_than_12_native_timing_and_cache(self):
        self.acquire(self.multi);submits=self.helper.state['submits']
        with patch.object(core.Gateway,'post',side_effect=AssertionError('CV must not invoke gateway')):
            result=cv.probe_cv(self.root,attempt_id='hardcuts')
        self.assertEqual(result['status'],'succeeded',core.read(self.root/'cv/hardcuts/status.json'))
        cv.verify_cv(self.root);data=core.read(self.root/'cv/hardcuts/shots.json')
        self.assertEqual(len(data['shots']),16);self.assertEqual(data['frameCount'],160)
        self.assertEqual([s['startFrame'] for s in data['shots']],[i*10 for i in range(16)])
        self.assertTrue(all(s['representativeStatus']=='available' for s in data['shots']))
        self.assertEqual(data['timeMapping'],'native_pts_no_cfr_conversion')
        result=cv.probe_cv(self.root);self.assertTrue(result['reusedCvAttempt'])
        self.assertEqual(self.helper.state['submits'],submits);self.assertEqual(self.helper.state['videoGets'],1)
        from delivery import export_report,verify_export
        exported=Path(self.helper.root_temp.name)/'export';export_report(self.root,exported);verify_export(exported)
        self.assertEqual(len(list(exported.rglob('*.jpg'))),16)
    def test_legal_single_shot_is_not_failure_fallback(self):
        self.acquire();result=cv.probe_cv(self.root,attempt_id='single')
        self.assertEqual(result['status'],'succeeded')
        data=core.read(self.root/'cv/single/shots.json');self.assertEqual(len(data['shots']),1)
        self.assertGreater(data['frameCount'],0)
    def test_vfr_preserves_native_pts_without_cfr_resampling(self):
        self.acquire(self.vfr);result=cv.probe_cv(self.root,attempt_id='vfr')
        self.assertEqual(result['status'],'succeeded',core.read(self.root/'cv/vfr/status.json'))
        data=core.read(self.root/'cv/vfr/shots.json');self.assertTrue(data['variableFrameIntervalsObserved'])
        self.assertEqual(data['timeMapping'],'native_pts_no_cfr_conversion')
        self.assertAlmostEqual(data['durationSec'],2,places=2)
    def test_timeout_does_not_create_success_or_full_shot_fallback(self):
        self.acquire();result=cv.probe_cv(self.root,attempt_id='timeout',config_file=self.config(timeoutSec=.05))
        self.assertEqual(result['status'],'failed')
        state=core.read(self.root/'cv/timeout/status.json');self.assertEqual(state['errorCode'],'cv_timeout')
        self.assertEqual(core.read(self.root/'cv/timeout/receipt.json')['status'],'failed')
        with self.assertRaises(core.ProbeError):cv.verify_cv(self.root)
        self.assertFalse(cv.recorded_process_alive(state))
        self.assertEqual(core.verify(self.root)['status'],'video_ready')
    def test_shared_cgroup_low_headroom_blocks_before_worker(self):
        self.acquire();snapshot={'status':'available','version':1,'memory.usage_in_bytes':str(900*1048576),'memory.limit_in_bytes':str(1024*1048576),'memory.failcnt':'1000'}
        with patch.object(cv,'cgroup_snapshot',return_value=snapshot):result=cv.probe_cv(self.root,attempt_id='guard')
        self.assertEqual(result['status'],'failed');receipt=core.read(self.root/'cv/guard/receipt.json')
        self.assertEqual(receipt['errorCode'],'insufficient_headroom');self.assertIsNone(receipt['workerExitCode'])
        self.assertEqual(receipt['cgroupCounterDelta']['memory.failcnt'],0)
    def test_output_limit_retains_diagnostic_boundaries_without_success(self):
        self.acquire(self.multi);result=cv.probe_cv(self.root,attempt_id='limit',config_file=self.config(maxShots=2))
        self.assertEqual(result['status'],'failed');state=core.read(self.root/'cv/limit/status.json')
        self.assertEqual(state['errorCode'],'output_limit');self.assertTrue((self.root/'cv/limit/boundaries.ndjson').exists())
        self.assertFalse((self.root/'cv/limit/shots.json').exists())
    def test_corrupted_representative_blocks_verify_and_delivery(self):
        self.acquire();self.assertEqual(cv.probe_cv(self.root,attempt_id='tamper')['status'],'succeeded')
        (self.root/'cv/tamper/frames/shot-001.jpg').write_bytes(b'changed')
        with self.assertRaises(core.ProbeError):cv.verify_cv(self.root)
        with self.assertRaises(core.ProbeError):core.report(self.root)
    def test_interrupted_worker_is_reported_without_fabricated_receipt(self):
        self.acquire();attempt=self.root/'cv/dead';attempt.mkdir(parents=True)
        core.write(self.root/'cv/latest.json',{'attemptId':'dead'})
        core.write(attempt/'status.json',{'status':'running','stage':'cv_decode_detect','workerPid':99999999})
        core.write(attempt/'config.json',cv.config_value())
        self.assertEqual(cv.cv_status(self.root)['status'],'interrupted')
        self.assertFalse((attempt/'receipt.json').exists())
    def test_sigkill_is_not_automatically_diagnosed_as_oom(self):
        self.acquire();original=cv.subprocess.Popen
        def killed(args,*positional,**kwargs):
            if isinstance(args,list) and any(str(a).endswith('cv_worker.py') for a in args):
                args=[sys.executable,'-c','import os,signal;os.kill(os.getpid(),signal.SIGKILL)']
            return original(args,*positional,**kwargs)
        with patch.object(cv.subprocess,'Popen',side_effect=killed):result=cv.probe_cv(self.root,attempt_id='killed')
        self.assertEqual(result['status'],'failed');receipt=core.read(self.root/'cv/killed/receipt.json')
        self.assertEqual(receipt['signal'],9);self.assertEqual(receipt['errorCode'],'signal_terminated_unknown')
        self.assertFalse((self.root/'cv/killed/shots.json').exists())
    def test_memory_guard_aborts_its_running_worker(self):
        self.acquire()
        low={'status':'available','version':1,'memory.usage_in_bytes':str(100*1048576),'memory.limit_in_bytes':str(1024*1048576)}
        high={**low,'memory.usage_in_bytes':str(900*1048576)}
        calls=[]
        def snapshot():calls.append(1);return low if len(calls)==1 else high
        with patch.object(cv,'cgroup_snapshot',side_effect=snapshot):result=cv.probe_cv(self.root,attempt_id='runtime-guard')
        self.assertEqual(result['status'],'failed')
        state=core.read(self.root/'cv/runtime-guard/status.json');self.assertEqual(state['errorCode'],'memory_guard_aborted')
        self.assertFalse(cv.recorded_process_alive(state))
    def test_truncated_video_fails_decoding_without_whole_video_fallback(self):
        temp=Path(self.helper.root_temp.name);fast=temp/'fast.mp4';broken=temp/'truncated.mp4'
        subprocess.run([self.ffmpeg,'-hide_banner','-loglevel','error','-i',str(self.helper.video),'-c','copy','-movflags','+faststart',str(fast)],check=True)
        broken.write_bytes(fast.read_bytes()[:-80]);self.acquire(broken)
        result=cv.probe_cv(self.root,attempt_id='broken')
        self.assertEqual(result['status'],'failed')
        self.assertIn(core.read(self.root/'cv/broken/status.json')['errorCode'],('decode_failed','decode_duration_incomplete','decode_short_frame'))
        self.assertFalse((self.root/'cv/broken/shots.json').exists())
    def test_representative_extraction_failure_keeps_detection_separate(self):
        self.acquire();wrapper=Path(self.helper.root_temp.name)/'ffmpeg-test-wrapper'
        code='#!'+sys.executable+'\nimport os,sys\n'
        code+='if sys.argv[-1].endswith("shot-%03d.jpg"): sys.exit(1)\n'
        code+='os.execv('+repr(self.ffmpeg)+', ['+repr(self.ffmpeg)+']+sys.argv[1:])\n'
        wrapper.write_text(code);wrapper.chmod(0o755)
        with patch.dict('os.environ',{'IMAGEIO_FFMPEG_EXE':str(wrapper)}):result=cv.probe_cv(self.root,attempt_id='frames-failed')
        self.assertEqual(result['status'],'failed')
        data=core.read(self.root/'cv/frames-failed/shots.json')
        self.assertEqual(data['detectionStatus'],'succeeded');self.assertEqual(data['representativeStatus'],'failed')
        with self.assertRaises(core.ProbeError):cv.verify_cv(self.root)
    def test_config_limits_and_unknown_cgroup(self):
        with self.assertRaises(core.ProbeError):cv.config_value(self.config(maxDimension=1920))
        self.assertIsNone(cv.memory_usage({'status':'unavailable'}))
        self.assertIsNone(cv.memory_usage({'status':'available','version':2,'memory.current':'42','memory.max':'max'}))

    def test_adaptive_backend_remains_explicit_and_does_not_reuse_native(self):
        self.acquire(self.multi)
        self.assertEqual(cv.probe_cv(self.root,attempt_id='native')['status'],'succeeded')
        result=cv.probe_cv(self.root,backend='adaptive',attempt_id='adaptive')
        self.assertEqual(result['status'],'succeeded')
        data=core.read(self.root/'cv/adaptive/shots.json');self.assertEqual(len(data['shots']),16)
        self.assertEqual(data['detector'],'AdaptiveDetector')
    def test_native_run_with_cache_heavy_logged_baseline_and_guard_evidence(self):
        self.acquire(self.multi)
        from test_memory_guard import snapshot
        observed=snapshot()
        with patch.object(cv,'cgroup_snapshot',return_value=observed):result=cv.probe_cv(self.root,attempt_id='cached-native')
        self.assertEqual(result['status'],'succeeded')
        r=cv.verify_cv(self.root);self.assertEqual(r['memoryGuard']['policyOrigin'],'skill')
        env=core.read(self.root/'cv/cached-native/worker-environment.json');self.assertFalse(env['heavyCvImports'])
        self.assertEqual(len(core.read(self.root/'cv/cached-native/shots.json')['shots']),16)
    def test_native_missing_filter_fails_without_adaptive_fallback(self):
        self.acquire();wrapper=Path(self.helper.root_temp.name)/'ffmpeg-no-scdet'
        wrapper.write_text('#!'+sys.executable+'\nimport os,sys\nif "filter=scdet" in sys.argv: print("Unknown filter");sys.exit(0)\nos.execv('+repr(self.ffmpeg)+', ['+repr(self.ffmpeg)+']+sys.argv[1:])\n');wrapper.chmod(0o755)
        with patch.dict('os.environ',{'IMAGEIO_FFMPEG_EXE':str(wrapper)}):result=cv.probe_cv(self.root,attempt_id='no-filter')
        self.assertEqual(result['status'],'failed');self.assertEqual(core.read(self.root/'cv/no-filter/status.json')['errorCode'],'ffmpeg_scdet_unavailable')
