from pathlib import Path
import json,os,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import runtime_memory as memory
from memory_guard import evaluate_guard
from test_memory_guard import snapshot,M
from test_acquisition import Response

class RuntimeMemoryTests(unittest.TestCase):
    def test_reserve_and_unknown_budget_are_distinct(self):
        cfg=core.read(core.ROOT/'config/cv-low-memory.json')
        guard=evaluate_guard(snapshot(),cfg,reserve_mib=128)
        self.assertFalse(guard['abort']);self.assertEqual(guard['headroomBasis'],'conservative_working_set_estimate')
        self.assertLess(guard['headroomToSkillRawCeilingBytes'],128*M)
        self.assertGreater(guard['headroomForStageBytes'],128*M)
        guard=evaluate_guard(snapshot(usage=790,inactive=0),cfg,reserve_mib=256)
        self.assertTrue(guard['abort']);self.assertEqual(guard['reason'],'insufficient_stage_headroom')
        self.assertFalse(evaluate_guard(snapshot(usage=840),cfg,reserve_mib=128)['abort'])
        g=evaluate_guard({'status':'unavailable'},cfg,reserve_mib=128)
        self.assertFalse(g['headroomKnown']);self.assertNotIn('headroomToSkillRawCeilingBytes',g)
    def test_cache_advice_is_scoped_and_failure_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'media').mkdir();(root/'acquisition').mkdir()
            video=root/'media/source-video.mp4';video.write_bytes(b'video')
            outside=root/'outside.mp4';outside.write_bytes(b'outside')
            (root/'media/link.mp4').symlink_to(outside)
            (root/'media/dependency.so').write_bytes(b'binary')
            with patch.object(os,'posix_fadvise',create=True,side_effect=OSError(22,'unsupported')) as advise,patch.object(os,'POSIX_FADV_DONTNEED',4,create=True):
                result=memory.release_completed(root,'test')
            self.assertEqual(advise.call_count,1);self.assertEqual(result['advisedBytes'],0)
            self.assertEqual(result['errors'][0]['errno'],22);self.assertEqual(video.read_bytes(),b'video')
    def test_download_receipt_hash_matches_stream_and_closes_response(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'media/source-video.mp4';receipt=root/'receipt.json'
            response=Response([b'abc',b'def']);session=type('S',(),{'get':lambda *a,**kw:response})()
            core.download('https://example.test/video',target,max_bytes=20,session=session,receipt_path=receipt,receipt_root=root)
            self.assertEqual(core.read(receipt),core.artifact(target,root));self.assertTrue(response.closed)
    def test_report_uses_observed_peak_and_reports_unknown_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[{'time':i,'attemptId':'a','memoryEstimate':{'rawUsageBytes':v,'workingSetEstimateBytes':v-10,'memoryStat':{'total_cache':v//2,'total_rss':v//3}}} for i,v in enumerate((100,500,200))]
            (root/'resources.ndjson').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            summary=core.resource_summary(root)
            self.assertEqual(summary['peaks']['rawUsageBytes'],500);self.assertEqual(summary['peaks']['cacheBytes'],250)
            self.assertNotIn('processTreeSampledRssBytes',summary['peaks'])

    def test_owned_hash_reads_every_byte_and_advises_aligned_pages(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'source.mp4';page=os.sysconf('SC_PAGE_SIZE');data=b'x'*(1048576+page+19);path.write_bytes(data)
            with patch.object(os,'posix_fadvise',create=True) as advise,patch.object(os,'POSIX_FADV_DONTNEED',4,create=True),patch.object(os,'fsync') as sync:
                sha=memory.digest_owned(path,owner_root=root,log_root=root,stage='test_sha')
            self.assertEqual(sha,hashlib.sha256(data).hexdigest());self.assertEqual(sync.call_count,1)
            calls=[call.args[1:3] for call in advise.call_args_list]
            self.assertEqual(calls,[(0,1048576),(1048576,page),(0,0)])
            event=json.loads((root/'cache-advice.ndjson').read_text().splitlines()[-1])
            self.assertEqual(event['hashedBytes'],len(data));self.assertEqual(event['advisedBytes'],1048576+page)
            self.assertEqual(event['sha256'],sha);self.assertEqual(path.read_bytes(),data)
            with patch.object(os,'posix_fadvise',create=True) as advise:
                self.assertEqual(core.digest(path),sha)
                advise.assert_not_called()
    def test_owned_hash_advice_failure_keeps_complete_sha_and_records_error(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'owned.csv';path.write_bytes(b'csv bytes')
            with patch.object(os,'posix_fadvise',create=True,side_effect=OSError(22,'unsupported')),patch.object(os,'POSIX_FADV_DONTNEED',4,create=True):
                sha=memory.digest_owned(path,owner_root=root,log_root=root,stage='test_sha')
            self.assertEqual(sha,hashlib.sha256(b'csv bytes').hexdigest())
            event=json.loads((root/'cache-advice.ndjson').read_text().splitlines()[-1]);self.assertEqual(event['errors'][0]['errno'],22)
    def test_owned_hash_rejects_symlink_owner_and_changes_during_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);owner=root/'owner';owner.mkdir();path=owner/'video.mp4';path.write_bytes(b'x'*1048576)
            linked=root/'linked';linked.symlink_to(owner,target_is_directory=True)
            with self.assertRaises(core.ProbeError):memory.digest_owned(linked/'video.mp4',owner_root=linked,log_root=root,stage='test_sha')
            changed=False
            def change(*args):
                nonlocal changed
                if not changed:
                    changed=True
                    with path.open('ab') as handle:handle.write(b'tamper')
            with patch.object(os,'posix_fadvise',create=True,side_effect=change),patch.object(os,'POSIX_FADV_DONTNEED',4,create=True):
                with self.assertRaises(core.ProbeError):memory.digest_owned(path,owner_root=owner,log_root=root,stage='test_sha')
    def test_item_release_only_visits_bound_attempt_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);attempt=root/'cv/current';attempt.mkdir(parents=True)
            frame=attempt/'frames/shot-001.jpg';frame.parent.mkdir();frame.write_bytes(b'jpeg')
            core.write(attempt/'receipt.json',{'processCleanup':{'status':'completed'},'artifacts':[core.artifact(frame,attempt)]})
            other=root/'cv/other/frames/shot-001.jpg';other.parent.mkdir(parents=True);other.write_bytes(b'other')
            snapshot=root/'snapshot';core.write(snapshot/'report/report.json',{'status':'succeeded'});core.write(snapshot/'report/receipt.json',{})
            observed=[];original=memory.release_owned
            def record(log,stage,owner,paths):
                observed.extend(paths);return original(log,stage,owner,paths)
            with patch.object(memory,'release_owned',side_effect=record):memory.release_item(root,'test_item',attempt_id='current',report_root=snapshot)
            self.assertIn(frame,observed);self.assertNotIn(other,observed);self.assertTrue(other.exists());self.assertTrue(frame.exists())
