import unittest
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import batch_acquisition as batch
import test_cv
import test_batch_visual as visual_tests

class RpaWaveTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:test_cv.CvTests.setUpClass())
    tearDownClass=classmethod(lambda cls:test_cv.CvTests.tearDownClass())
    setUp=visual_tests.BatchVisualTests.setUp
    tearDown=visual_tests.BatchVisualTests.tearDown
    request=visual_tests.BatchVisualTests.request

    def ids(self,count):
        ids=[str(101+i) for i in range(count)]
        return ids[::-1] if visual_tests.intake.PLATFORM=='qianchuan' else ids

    def test_seven_materials_submit_3_3_1_before_poll_and_media_then_serial_cv(self):
        self.state['materialCount']=7
        original=core.Gateway.post;events=[];active=set();peak=0
        def post(gateway,path,payload):
            nonlocal peak
            detail=path.endswith('/tasks') and not payload['function_code'].endswith('.list')
            if detail:
                mid=payload['business_params']['material_id'];active.add(mid);peak=max(peak,len(active))
                self.assertLessEqual(len(active),3);events.append(('submit',mid))
            if path.endswith('/tasks/status') and payload['task_group_id'].startswith('detail'):
                mid=payload['task_group_id'].removeprefix('detail-')
                ids=self.ids(7);expected=3 if mid in (ids[0],ids[3]) else 1 if mid==ids[6] else None
                if expected is not None:self.assertEqual(len(active),expected,'poll began before entire wave submitted')
                events.append(('poll',mid))
            result=original(gateway,path,payload)
            if path.endswith('/tasks/status') and payload['task_group_id'].startswith('detail') and result.get('status')=='completed':active.discard(mid)
            return result
        download=core.download
        def serial_media(url,target,**kwargs):
            if Path(target).suffix=='.mp4':
                self.assertFalse(active,'video started before wave drained');events.append(('video',Path(target).parent.parent.name))
            return download(url,target,**kwargs)
        with patch.object(core.Gateway,'post',new=post),patch.object(core,'download',side_effect=serial_media):
            result=batch.run_batch(self.request(7),self.parent/'waves')
        self.assertEqual(result['status'],'succeeded',result);self.assertEqual(peak,3)
        self.assertEqual(result['rpaSubmissionCount'],7);self.assertEqual(result['acquiredCount'],7);self.assertEqual(result['completedCount'],7)
        self.assertEqual([len(w['materialIds']) for w in result['rpaWaves']],[3,3,1])
        self.assertEqual([w['status'] for w in result['rpaWaves']],['completed']*3)
        self.assertEqual(result['firstBatchCsvGate']['status'],'passed')
        self.assertEqual(events[:6],[('submit',mid) for mid in self.ids(7)[:3]]+[('poll',mid) for mid in self.ids(7)[:3]])
        self.assertEqual(len([e for e in events if e[0]=='poll']),7,'media phase repolled RPA')
        self.assertEqual(result['rpaConcurrency'],3);self.assertEqual(result['mediaConcurrency'],1);self.assertEqual(result['cvConcurrency'],1)

    def test_first_wave_bad_csv_drains_only_three_and_no_cv_or_next_wave(self):
        self.state.update(materialCount=7,wrongId=True)
        with patch('cv_probe.probe_cv') as cv:result=batch.run_batch(self.request(7),self.parent/'bad-wave')
        cv.assert_not_called();self.assertEqual(self.state['detailIds'],self.ids(7)[:3])
        self.assertEqual(result['firstBatchCsvGate']['status'],'blocked');self.assertEqual(result['acquiredCount'],0)
        self.assertEqual([e['status'] for e in result['entries']],['failed']*3+['pending']*4)
        for e in result['entries'][:3]:
            task=core.read(Path(e['runDir'])/'acquisition/tasks.json')['detail'];self.assertEqual(task['status'],'completed')

    def test_known_failed_rpa_drains_other_tasks_but_stops_next_wave(self):
        self.state['materialCount']=7;original=core.Gateway.post
        def post(gateway,path,payload):
            if path.endswith('/tasks/status') and payload['task_group_id']=='detail-'+self.ids(7)[0]:return {'status':'failed'}
            return original(gateway,path,payload)
        with patch.object(core.Gateway,'post',new=post),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(7),self.parent/'failed-task')
        cv.assert_not_called();self.assertEqual(result['errorCode'],'rpa_failed')
        self.assertEqual(self.state['detailIds'],self.ids(7)[:3]);self.assertEqual(result['acquiredCount'],0)
        self.assertEqual(result['firstBatchCsvGate']['status'],'passed')
        self.assertEqual(result['firstBatchCsvGate']['validCsvCount'],2)
        self.assertEqual([e['acquisitionStatus'] for e in result['entries'][:3]],['failed','csv_ready','csv_ready'])

    def test_uncertain_second_submit_never_retries_and_collects_known_first(self):
        self.state['materialCount']=7;original=core.Gateway.post
        def post(gateway,path,payload):
            result=original(gateway,path,payload)
            if path.endswith('/tasks') and payload.get('business_params',{}).get('material_id')==self.ids(7)[1]:raise core.ProbeError('transport_lost')
            return result
        with patch.object(core.Gateway,'post',new=post),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(7),self.parent/'unknown-submit')
        cv.assert_not_called();self.assertEqual(self.state['detailIds'],self.ids(7)[:2])
        self.assertEqual(result['firstBatchCsvGate']['status'],'unconfirmed');self.assertEqual(result['rpaSubmissionCount'],1)
        second=Path(result['entries'][1]['runDir']);task=core.read(second/'acquisition/tasks.json')['detail']
        self.assertEqual(task['status'],'submission_intent');self.assertIsNone(task['taskId'])
        with self.assertRaises(core.ProbeError) as error:core.acquire(self.request(7),second,resume=True,_rpa_phase='collect')
        self.assertEqual(error.exception.code,'submission_unconfirmed');self.assertEqual(self.state['detailIds'],self.ids(7)[:2])
        core.verify(Path(result['entries'][0]['runDir']))

    def test_memory_guard_mid_submit_leaves_known_tasks_no_new_work(self):
        original=batch.StageMonitor.checkpoint
        def checkpoint(monitor,stage):
            if stage=='batch_A_3':raise core.ProbeError('synthetic_wave_guard')
            return original(monitor,stage)
        with patch.object(batch.StageMonitor,'checkpoint',new=checkpoint),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'guard-wave')
        cv.assert_not_called();self.assertEqual(self.state['detailIds'],self.ids(3)[:2])
        self.assertEqual(result['rpaSubmissionCount'],2);self.assertEqual(result['acquiredCount'],0)
        self.assertEqual(result['entries'][0]['acquisitionStatus'],'submitted')

    def test_csv_tamper_before_media_is_rejected_without_repoll_or_download(self):
        original=batch.acquire
        def acquire(request,root,**kwargs):
            if kwargs.get('_rpa_phase')=='media':
                with (Path(root)/'acquisition/detail.csv').open('ab') as f:f.write(b'changed')
            return original(request,root,**kwargs)
        with patch.object(batch,'acquire',side_effect=acquire),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'tamper-wave')
        cv.assert_not_called();self.assertEqual(result['errorCode'],'accepted_csv_changed');self.assertEqual(self.state['videoGets'],0)
        self.assertEqual(len(self.state['detailIds']),3)

    def test_collect_missing_task_cannot_submit_new_remote_work(self):
        original=batch.acquire
        def acquire(request,root,**kwargs):
            if kwargs.get('_rpa_phase')=='collect':(Path(root)/'acquisition/tasks.json').unlink(missing_ok=True)
            return original(request,root,**kwargs)
        with patch.object(batch,'acquire',side_effect=acquire),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'lost-task')
        cv.assert_not_called();self.assertEqual(result['errorCode'],'rpa_task_missing');self.assertEqual(len(self.state['detailIds']),3)
        self.assertEqual(result['firstBatchCsvGate']['status'],'unconfirmed')

    def test_latched_guard_while_polling_stops_before_csv_and_next_work(self):
        original=batch.StageMonitor.checkpoint;post=core.Gateway.post;seen=[]
        def checkpoint(monitor,stage):
            if stage=='batch_collect_1':seen.append(monitor)
            return original(monitor,stage)
        def response(gateway,path,payload):
            result=post(gateway,path,payload)
            if path.endswith('/tasks/status') and payload['task_group_id'].startswith('detail'):
                seen[0].failure='synthetic_latched_guard'
            return result
        with patch.object(batch.StageMonitor,'checkpoint',new=checkpoint),patch.object(core.Gateway,'post',new=response),patch('cv_probe.probe_cv') as cv:
            result=batch.run_batch(self.request(),self.parent/'latched-wave')
        cv.assert_not_called();self.assertEqual(result['errorCode'],'operation_memory_guard_aborted')
        self.assertEqual(len(self.state['detailIds']),3);self.assertEqual(self.state['videoGets'],0)
        self.assertFalse(list((self.parent/'latched-wave/runs').rglob('detail.csv')))
        self.assertEqual(result['firstBatchCsvGate']['status'],'unconfirmed')
        self.assertEqual(result['rpaWaves'][0]['status'],'unconfirmed')
