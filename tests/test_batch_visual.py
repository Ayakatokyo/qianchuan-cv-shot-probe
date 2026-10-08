import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import platform_adapter as adapter
import batch_acquisition as batch
import visual_report as visual
import serial_probe
import test_cv
import test_acquisition as intake

class BatchVisualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):test_cv.CvTests.setUpClass()
    @classmethod
    def tearDownClass(cls):test_cv.CvTests.tearDownClass()
    def setUp(self):
        self.helper=test_cv.CvTests(methodName='runTest');self.helper.setUp()
        self.parent=self.helper.root.parent;self.state=self.helper.helper.state
        self.state['materialCount']=3;self.state['secondVideo']=self.helper.multi.read_bytes()
    def tearDown(self):self.helper.tearDown()
    def request(self,count=3):
        request=intake.request();request['query_spec']['collection']={'targetTopN':count} if intake.PLATFORM=='qianchuan' else {'target_top_n':count}
        return request
    def test_all_three_acquisitions_finish_before_serial_cv_and_one_html(self):
        import cv_probe
        output=self.parent/'batch';original=cv_probe.probe_cv;order=[]
        def ordered_cv(root,**kwargs):
            self.assertEqual(len(self.state['detailIds']),3)
            self.assertEqual(self.state['videoGets'],3)
            saved=core.read(output/'batch.json')
            self.assertEqual(saved['acquiredCount'],3);self.assertEqual(saved['stage'],'cv')
            for item in saved['entries']:
                self.assertEqual(item['acquisitionStatus'],'video_ready')
                self.assertEqual(core.read(Path(item['runDir'])/'status.json')['status'],'video_ready')
            for previous in saved['entries'][:len(order)]:
                self.assertEqual(previous['processCleanup']['status'],'completed')
            order.append(Path(root).name)
            return original(root,**kwargs)
        with patch.object(cv_probe,'probe_cv',side_effect=ordered_cv):
            result=batch.run_batch(self.request(),output)
        self.assertEqual(order,['item-1','item-2','item-3'])
        self.assertEqual(result['stage'],'complete');self.assertEqual(result['acquiredCount'],3)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['completedCount'],3);self.assertEqual(self.state['selectionCalls'],1)
        self.assertEqual(len(set(self.state['detailIds'])),3);self.assertEqual(result['distinctVideoCount'],2)
        self.assertFalse(list(output.rglob('*.zip')))
        source_ref=core.read(output/'selection/acquisition/selection-source.json')
        source=output/'selection'/source_ref['path']
        for entry in result['entries']:
            self.assertEqual(entry['processCleanup']['status'],'completed')
            run=Path(entry['runDir']);receipt=core.verify(run)
            source_link=next(a for a in receipt['artifacts'] if a['path'].startswith('acquisition/batch-source.'))
            self.assertEqual((run/source_link['path']).stat().st_ino,source.stat().st_ino)
        report=Path(result['report']['htmlPath']).read_text()
        self.assertEqual(report.count('class="material-panel"'),3)
        self.assertIn('data:image/jpeg;base64,',report);self.assertNotIn('<video',report)
        self.assertNotIn('fake-test-secret',report);self.assertNotIn('rpa_shop',report)
        self.assertLess(Path(result['report']['htmlPath']).stat().st_size,2*1024*1024)
        # The shared source remains bound; modifying it invalidates every child.
        with source.open('ab') as handle:handle.write(b'tamper')
        with self.assertRaises(core.ProbeError):core.verify(Path(result['entries'][0]['runDir']))
    def test_bad_first_wave_csv_drains_three_without_new_wave_or_retry(self):
        self.state['wrongId']=True;output=self.parent/'failed'
        result=batch.run_batch(self.request(),output)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['completedCount'],0)
        self.assertEqual(len(self.state['detailIds']),3);self.assertEqual(self.state['videoGets'],0)
        self.assertEqual([e['status'] for e in result['entries']],['failed','failed','failed'])
        self.assertEqual(result['firstBatchCsvGate']['status'],'blocked')
        run=Path(result['entries'][0]['runDir'])
        self.assertEqual(core.read(run/'status.json')['firstBatchCsvGate']['status'],'blocked')
    def test_shortage_is_visible_and_does_not_backfill(self):
        self.state['materialCount']=1;result=batch.run_batch(self.request(),self.parent/'short')
        self.assertEqual(result['status'],'partial',result);self.assertEqual(result['shortageCount'],2)
        self.assertEqual(result['completedCount'],1);self.assertEqual(self.state['selectionCalls'],1)
        self.assertIn('筛选后素材不足',Path(result['report']['htmlPath']).read_text())
    def test_validate_bounds_and_single_acquire_cannot_silently_reduce_batch(self):
        for count in (0,11,True):
            with self.assertRaises(Exception):adapter.validate(self.request(count))
        with self.assertRaises(core.ProbeError) as error:core.acquire(self.request(),self.parent/'wrong-entry')
        self.assertEqual(error.exception.code,'use_run_batch');self.assertEqual(self.state['submits'],0)
    def test_light_export_reads_no_video_or_csv_and_detects_frame_tamper(self):
        self.helper.acquire();import cv_probe
        cv_probe.probe_cv(self.helper.root,attempt_id='visual')
        original=core.digest;seen=[]
        def bounded(path):
            seen.append(str(path));self.assertNotIn(Path(path).suffix,('.mp4','.csv'));return original(path)
        with patch.object(core,'digest',side_effect=bounded):
            result=visual.export_html(self.helper.root,self.parent/'light.html')
        self.assertFalse(result['containsVideo']);self.assertTrue(result['selfContained']);self.assertGreater(len(seen),0)
        frame=next((self.helper.root/'cv/visual/frames').glob('*.jpg'));frame.write_bytes(b'changed')
        with self.assertRaises(core.ProbeError):visual.export_html(self.helper.root,self.parent/'bad.html')
        self.assertFalse((self.parent/'bad.html').exists())
    def test_repeated_existing_A_produces_one_html_bound_to_each_attempt(self):
        self.helper.acquire();manifest=self.parent/'inputs.json'
        core.write(manifest,{'schemaVersion':1,'runs':[{'runDir':str(self.helper.root)}]*2})
        with patch.object(core.Gateway,'post',side_effect=AssertionError('no acquisition')):
            result=serial_probe.probe_batch(manifest,self.parent/'reused')
        self.assertEqual(result['status'],'succeeded',result);self.assertEqual(result['completedCount'],2)
        report=Path(result['report']['htmlPath']).read_text()
        for entry in result['entries']:self.assertIn(entry['attemptId'],report)
        self.assertFalse(list((self.parent/'reused').rglob('*.zip')))
    def test_visual_escapes_titles_and_caps_images_without_hiding_missing_frames(self):
        self.helper.acquire();import cv_probe
        cv_probe.probe_cv(self.helper.root,attempt_id='escape')
        public,folder=visual.load_public(self.helper.root);public['materialName']='<script>alert(1)</script>'
        with patch.object(visual,'IMAGE_BUDGET',0):stats=visual.write_visual(self.parent/'capped.html',[(public,folder)])
        report=(self.parent/'capped.html').read_text()
        self.assertNotIn('<script>alert(1)</script>',report);self.assertIn('&lt;script&gt;',report)
        self.assertEqual(stats['embeddedFrames'],0);self.assertGreater(stats['omittedFrames'],0)
        self.assertIn('已达到轻量报告图片容量上限',report)

    def test_html_guard_stop_preserves_diagnostics_without_publishing(self):
        self.helper.acquire();import cv_probe
        cv_probe.probe_cv(self.helper.root,attempt_id='guard-html')
        from test_memory_guard import snapshot
        pressured=False;original=visual.write_visual
        def pressure(*args,**kwargs):
            nonlocal pressured
            pressured=True
            return original(*args,**kwargs)
        with patch.object(core,'cgroup_snapshot',side_effect=lambda:snapshot(usage=850 if pressured else 600,inactive=0)),patch.object(visual,'write_visual',side_effect=pressure):
            with self.assertRaises(core.ProbeError):visual.export_html(self.helper.root,self.parent/'blocked.html')
        self.assertFalse((self.parent/'blocked.html').exists());self.assertFalse(list(self.parent.glob('.visual-report-*')))
        receipt=core.read(self.helper.root/'html-exports/blocked/receipt.json')
        self.assertEqual(receipt['status'],'failed');self.assertFalse(receipt['outputPublished'])
        self.assertEqual(receipt['guardStopReason'],'working_set_ceiling')

    def test_shared_selection_changed_during_cv_stops_before_second_worker(self):
        import cv_probe
        original=cv_probe.probe_cv
        def mutate(root,**kwargs):
            result=original(root,**kwargs)
            source=Path(root)/'acquisition'
            path=next(source.glob('batch-source.*'))
            with path.open('ab') as handle:handle.write(b'changed')
            return result
        with patch.object(cv_probe,'probe_cv',side_effect=mutate):result=batch.run_batch(self.request(),self.parent/'changed')
        self.assertEqual(result['status'],'failed');self.assertEqual(result['completedCount'],1)
        self.assertEqual(result['errorCode'],'artifact_changed');self.assertEqual(len(self.state['detailIds']),3)
        self.assertEqual(result['acquiredCount'],3)
        self.assertEqual(result['entries'][2]['status'],'pending')

    def test_second_bad_csv_keeps_first_input_but_launches_no_cv(self):
        original=batch.acquire;calls=0
        def acquire(root_request,root,**kwargs):
            nonlocal calls
            if kwargs.get('_rpa_phase')=='collect':
                calls+=1;self.state['wrongId']=calls==2
            return original(root_request,root,**kwargs)
        with patch.object(batch,'acquire',side_effect=acquire),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'second-bad-csv')
        cv.assert_not_called()
        self.assertEqual(result['status'],'failed');self.assertEqual(result['stage'],'acquisition')
        self.assertEqual(result['acquiredCount'],1);self.assertEqual(result['completedCount'],0)
        self.assertEqual(len(self.state['detailIds']),3)
        self.assertEqual([e['acquisitionStatus'] for e in result['entries']],['video_ready','failed','csv_ready'])
        self.assertEqual([e['cvStatus'] for e in result['entries']],['pending']*3)
        core.verify(Path(result['entries'][0]['runDir']))

    def test_shared_selection_changed_during_acquisition_launches_no_cv(self):
        original=batch.acquire
        def mutate(root_request,root,**kwargs):
            result=original(root_request,root,**kwargs)
            source=next((Path(root)/'acquisition').glob('batch-source.*'))
            with source.open('ab') as handle:handle.write(b'changed')
            return result
        with patch.object(batch,'acquire',side_effect=mutate),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'changed-A')
        cv.assert_not_called()
        self.assertEqual(result['errorCode'],'selection_source_changed')
        self.assertEqual(len(self.state['detailIds']),1);self.assertEqual(result['acquiredCount'],0)

    def test_second_cv_failure_keeps_all_inputs_and_first_result(self):
        import cv_probe
        original=cv_probe.probe_cv;calls=0
        def fail_second(root,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise core.ProbeError('synthetic_cv_failure')
            return original(root,**kwargs)
        with patch.object(cv_probe,'probe_cv',side_effect=fail_second):
            result=batch.run_batch(self.request(),self.parent/'failed-B')
        self.assertEqual(calls,2);self.assertEqual(len(self.state['detailIds']),3)
        self.assertEqual(result['acquiredCount'],3);self.assertEqual(result['completedCount'],1)
        self.assertEqual(result['stage'],'cv');self.assertEqual(result['errorCode'],'synthetic_cv_failure')
        self.assertEqual([e['status'] for e in result['entries']],['succeeded','failed','pending'])
        self.assertEqual([e['cvStatus'] for e in result['entries']],['succeeded','failed','pending'])
        for entry in result['entries']:core.verify(Path(entry['runDir']))

    def test_unconfirmed_cv_cleanup_stops_before_next_worker(self):
        import cv_probe
        original=cv_probe.probe_cv
        def unconfirmed(root,**kwargs):
            result=original(root,**kwargs)
            path=Path(root)/'cv'/kwargs['attempt_id']/'receipt.json'
            receipt=core.read(path);receipt['processCleanup']['status']='unconfirmed'
            core.write(path,receipt)
            return result
        with patch.object(cv_probe,'probe_cv',side_effect=unconfirmed) as cv:
            result=batch.run_batch(self.request(),self.parent/'cleanup-B')
        self.assertEqual(cv.call_count,1);self.assertEqual(result['acquiredCount'],3)
        self.assertEqual(result['errorCode'],'process_cleanup_unconfirmed')
        self.assertEqual(result['completedCount'],0)
        self.assertEqual([e['status'] for e in result['entries']],['failed','pending','pending'])

    def test_memory_guard_at_phase_barrier_launches_no_worker(self):
        original=batch.StageMonitor.checkpoint
        def checkpoint(monitor,stage):
            if stage=='batch_all_acquisitions_complete':
                raise core.ProbeError('synthetic_phase_guard')
            return original(monitor,stage)
        with patch.object(batch.StageMonitor,'checkpoint',new=checkpoint),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'phase-guard')
        cv.assert_not_called()
        self.assertEqual(result['acquiredCount'],3);self.assertEqual(result['completedCount'],0)
        self.assertEqual(result['errorCode'],'synthetic_phase_guard')
        self.assertEqual([e['cvStatus'] for e in result['entries']],['pending']*3)
