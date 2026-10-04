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
