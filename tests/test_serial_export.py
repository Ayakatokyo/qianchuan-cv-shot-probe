import json
from pathlib import Path
import time
import unittest
import zipfile
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import delivery
import runtime_memory as memory
import serial_probe
import test_cv
import test_acquisition
from test_memory_guard import snapshot


class SerialExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):test_cv.CvTests.setUpClass()
    @classmethod
    def tearDownClass(cls):test_cv.CvTests.tearDownClass()
    def setUp(self):
        self.helper=test_cv.CvTests(methodName='runTest');self.helper.setUp();self.helper.acquire()
        self.root=self.helper.root;self.parent=self.root.parent
    def tearDown(self):self.helper.tearDown()
    def manifest(self,count=2):
        path=self.parent/'manifest.json'
        core.write(path,{'schemaVersion':1,'runs':[{'runDir':str(self.root)} for _ in range(count)]})
        return path
    def test_serial_actual_workers_and_exports_have_complete_independent_evidence(self):
        with patch.object(core.Gateway,'post',side_effect=AssertionError('batch must not acquire')):
            result=serial_probe.probe_batch(self.manifest(),self.parent/'batch',delivery_mode='audit')
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['completedCount'],2)
        self.assertEqual(result['distinctVideoCount'],1)
        self.assertNotEqual(*[e['attemptId'] for e in result['entries']])
        for entry in result['entries']:
            self.assertEqual(entry['processCleanup']['status'],'completed')
            output=entry['export'];self.assertEqual(delivery.verify_export(Path(output['bundleDir']))['status'],'verified')
            evidence=Path(output['memoryEvidenceDir']);receipt=core.read(evidence/'receipt.json')
            self.assertEqual(receipt['status'],'exported');self.assertTrue(receipt['outputPublished'])
            phases={json.loads(line)['stage'] for line in (evidence/'resources.ndjson').read_text().splitlines()}
            self.assertTrue({'export_copy','export_zip','export_verify_zip','export_cache_advice','export_complete'}<=phases)
            for item in receipt['artifacts']:self.assertEqual(core.artifact(evidence/item['path'],evidence),item)
            with zipfile.ZipFile(output['memoryEvidenceZip']) as z:
                self.assertIsNone(z.testzip());self.assertEqual(z.read('receipt.json'),(evidence/'receipt.json').read_bytes())
            with zipfile.ZipFile(output['zipPath']) as z:self.assertIsNone(z.testzip())
        # A competing completed attempt must never be exported under the earlier item's receipt.
        with self.assertRaises(core.ProbeError) as changed:
            delivery.export_report(self.root,self.parent/'stale',expected_attempt=result['entries'][0]['attemptId'])
        self.assertEqual(changed.exception.code,'delivery_cv_attempt_changed')
        self.assertFalse((self.parent/'stale.zip').exists())
    def test_serial_first_failure_keeps_remaining_pending_and_does_not_retry(self):
        def fail(root,attempt_id,**kwargs):
            core.write(root/'cv'/attempt_id/'receipt.json',{'status':'failed','errorCode':'insufficient_headroom','inputVideoSha256':'hash','workerExitCode':None,'processCleanup':{'status':'not_started'}})
        with patch('cv_probe.probe_cv',side_effect=fail) as probe,patch.object(delivery,'export_report') as export:
            result=serial_probe.probe_batch(self.manifest(),self.parent/'failed')
        self.assertEqual(probe.call_count,1);export.assert_not_called()
        self.assertEqual(result['status'],'failed');self.assertEqual(result['completedCount'],0)
        self.assertEqual(result['entries'][1]['status'],'pending')
    def test_two_distinct_verified_A_videos_are_processed_serially(self):
        self.helper.helper.video.write_bytes(self.helper.multi.read_bytes())
        other=self.parent/'second-A'
        core.acquire(test_acquisition.request(),other)
        manifest=self.manifest()
        core.write(manifest,{'schemaVersion':1,'runs':[{'runDir':str(self.root)},{'runDir':str(other)}]})
        with patch.object(core.Gateway,'post',side_effect=AssertionError('existing inputs only')):
            result=serial_probe.probe_batch(manifest,self.parent/'diverse')
        self.assertEqual(result['status'],'succeeded');self.assertEqual(result['distinctVideoCount'],2)
        self.assertEqual(result['completedCount'],2)
    def test_serial_rejects_invalid_queue_before_worker_and_existing_output(self):
        path=self.manifest(11)
        with patch('cv_probe.probe_cv') as probe:
            with self.assertRaises(core.ProbeError):serial_probe.probe_batch(path,self.parent/'invalid')
            probe.assert_not_called()
        output=self.parent/'occupied';output.mkdir()
        with self.assertRaises(FileExistsError):serial_probe.probe_batch(self.manifest(),output)
    def test_export_guard_observes_copy_and_stops_before_publication(self):
        pressured=False;original=delivery.copy_bounded
        def pressure(*args):
            nonlocal pressured
            pressured=True;time.sleep(.3);return original(*args)
        with patch.object(core,'cgroup_snapshot',side_effect=lambda:snapshot(usage=850 if pressured else 600,inactive=0)),patch.object(delivery,'copy_bounded',side_effect=pressure):
            with self.assertRaises(core.ProbeError):delivery.export_report(self.root,self.parent/'stopped')
        self.assertFalse((self.parent/'stopped').exists());self.assertFalse((self.parent/'stopped.zip').exists())
        receipt=core.read(self.parent/'stopped.memory/receipt.json')
        self.assertEqual(receipt['status'],'failed');self.assertEqual(receipt['guardStopReason'],'working_set_ceiling')
        self.assertFalse(list(self.parent.glob('.report-export-*')));self.assertFalse(list(self.parent.glob('.report-bundle-*')))
    def test_explicit_export_advice_preserves_files_and_skips_unlisted_symlinks(self):
        import os
        target=self.parent/'owned.zip';target.write_bytes(b'archive')
        untouched=self.parent/'unlisted.zip';untouched.write_bytes(b'private')
        link=self.parent/'link.zip';link.symlink_to(untouched)
        directory=self.parent/'owned';directory.mkdir();(directory/'frame.jpg').write_bytes(b'jpeg')
        with patch.object(os,'posix_fadvise',create=True) as advise,patch.object(os,'POSIX_FADV_DONTNEED',4,create=True):
            observed=memory.release_owned(self.parent,'export_test',self.parent,[target,link,directory/'frame.jpg'])
        self.assertEqual(advise.call_count,2);self.assertEqual(observed['advisedBytes'],11)
        self.assertEqual(target.read_bytes(),b'archive');self.assertEqual(untouched.read_bytes(),b'private')
