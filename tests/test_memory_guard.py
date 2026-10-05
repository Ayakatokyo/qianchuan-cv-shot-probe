from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cv_probe as cv
from memory_guard import evaluate_guard
M=1048576

def snapshot(usage=850,inactive=336,**extra):
    return {'status':'available','version':1,'memory.usage_in_bytes':str(usage*M),'memory.limit_in_bytes':str(1024*M),'memory.failcnt':'3380162','memory.stat':f'total_cache {682*M}\ntotal_rss {135*M}\ntotal_inactive_file {inactive*M}\ntotal_active_file {346*M}\ntotal_shmem 0\ntotal_dirty 0\ntotal_writeback 0',**extra}

class MemoryGuardTests(unittest.TestCase):
    def test_cache_heavy_logged_case_can_run_instead_of_raw_80_abort(self):
        s=snapshot();g=evaluate_guard(s,cv.config_value(),baseline=s,tree_rss=70*M)
        self.assertFalse(g['abort']);self.assertEqual(g['workingSetEstimateBytes'],514*M);self.assertEqual(g['inactiveFileCreditBytes'],336*M)
    def test_real_working_set_and_emergency_total_still_abort(self):
        g=evaluate_guard(snapshot(inactive=0),cv.config_value());self.assertTrue(g['abort']);self.assertEqual(g['reason'],'working_set_ceiling')
        g=evaluate_guard(snapshot(usage=980),cv.config_value());self.assertTrue(g['abort']);self.assertEqual(g['reason'],'raw_emergency_ceiling')
    def test_dirty_shared_memory_and_missing_stats_are_not_all_free(self):
        s=snapshot();s['memory.stat']+=f'\ntotal_shmem {100*M}\ntotal_dirty {300*M}'
        self.assertTrue(evaluate_guard(s,cv.config_value())['abort'])
        s=snapshot();s.pop('memory.stat');g=evaluate_guard(s,cv.config_value());self.assertTrue(g['abort']);self.assertEqual(g['guardBasis'],'raw_usage_fallback')
    def test_v2_pressure_events_and_independent_process_budget(self):
        s={'status':'available','version':2,'memory.current':str(900*M),'memory.max':str(1024*M),'memory.stat':f'file {682*M}\ninactive_file {336*M}\nshmem 0\nfile_dirty 0\nfile_writeback 0','memory.events':'max 7\noom 0\noom_kill 0','memory.pressure':'full avg10=2.00 avg60=1.00 avg300=0.00 total=1'}
        g=evaluate_guard(s,cv.config_value(),baseline=s);self.assertTrue(g['abort']);self.assertEqual(g['reason'],'shared_cgroup_pressure')
        s.pop('memory.pressure');before={**s,'memory.events':'max 6\noom 0\noom_kill 0'}
        self.assertEqual(evaluate_guard(s,cv.config_value(),baseline=before)['reason'],'shared_cgroup_limit_event')
        self.assertEqual(evaluate_guard(s,cv.config_value(),baseline=s,tree_rss=300*M)['reason'],'process_tree_budget')
    def test_historical_failure_counter_is_not_current_pressure(self):
        s=snapshot();self.assertFalse(evaluate_guard(s,cv.config_value(),baseline=s)['abort'])
        newer={**s,'memory.failcnt':'3380163'};self.assertEqual(evaluate_guard(newer,cv.config_value(),baseline=s)['reason'],'shared_cgroup_limit_event')
    def test_unknown_limit_retains_process_budget_and_no_fake_headroom(self):
        g=evaluate_guard({'status':'unavailable'},cv.config_value());self.assertFalse(g['abort']);self.assertNotIn('workingSetEstimateBytes',g)
        self.assertTrue(evaluate_guard({'status':'unavailable'},cv.config_value(),tree_rss=300*M)['abort'])

    def test_missing_any_deduction_field_forces_raw_usage_v1_v2(self):
        cases=[snapshot(),{'status':'available','version':2,'memory.current':str(850*M),'memory.max':str(1024*M),'memory.stat':f'file {682*M}\ninactive_file {336*M}\nshmem 0\nfile_dirty 0\nfile_writeback 0'}]
        for observed in cases:
            keys=('total_shmem','total_dirty','total_writeback') if observed['version']==1 else ('shmem','file_dirty','file_writeback')
            for key in keys:
                with self.subTest(version=observed['version'],key=key):
                    s={**observed,'memory.stat':'\n'.join(line for line in observed['memory.stat'].splitlines() if not line.startswith(key+' '))}
                    g=evaluate_guard(s,cv.config_value())
                    self.assertEqual(g['inactiveFileCreditBytes'],0)
                    self.assertEqual(g['workingSetEstimateBytes'],850*M)
                    self.assertTrue(g['abort']);self.assertEqual(g['missingReclaimDeductionFields'],[key])
    def test_hierarchical_stats_do_not_mix_local_deductions(self):
        s=snapshot();s['memory.stat']='\n'.join(line for line in s['memory.stat'].splitlines() if not line.startswith('total_writeback '))+'\nwriteback 0'
        self.assertEqual(evaluate_guard(s,cv.config_value())['inactiveFileCreditBytes'],0)
