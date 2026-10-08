"""Synthetic kernel evidence and local HTTP replay; no platform/OOM acceptance."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import runtime_memory as runtime
import memory_guard as guard
import cv_probe as cv
import batch_acquisition as batch
import test_acquisition as intake
import test_cv
from test_memory_guard import M,snapshot

SOURCE={'path':'/proc/meminfo','fstype':'proc','mountRoot':'/','mountPoint':'/proc'}
def native(dirty=4,writeback=1,after_dirty=None,after_writeback=None):
    before=f'Dirty: {dirty*1024} kB\nWriteback: {writeback*1024} kB\nHugePages_Total: 0\n'
    after=f'Dirty: {(dirty if after_dirty is None else after_dirty)*1024} kB\nWriteback: {(writeback if after_writeback is None else after_writeback)*1024} kB\nHugePages_Total: 0\n'
    a=guard.proc_meminfo_bounds(before);b=guard.proc_meminfo_bounds(after)
    return {'status':'available','source':dict(SOURCE),'before':before,'after':after,'upperBoundsBytes':{key:max(a[key],b[key]) for key in a},'errorCode':None}

def missing_fields(usage=980,inactive=380):
    observed=snapshot(usage=usage,inactive=inactive)
    observed['memory.stat']='\n'.join(line for line in observed['memory.stat'].splitlines() if not line.startswith(('total_shmem ','total_dirty ','total_writeback ')))
    # New fields here are explicit synthetic observations, absent from old logs.
    observed.update({'nativeProcMeminfo':native(),'memory.oom_control':'oom_kill_disable 0\nunder_oom 0'})
    return observed

class AdmissionTests(unittest.TestCase):
    def evaluate(self,s,**kwargs):return guard.evaluate_guard(s,cv.config_value(),baseline=kwargs.pop('baseline',s),tree_rss=kwargs.pop('tree_rss',31*M),**kwargs)
    def test_missing_v1_fields_use_native_global_max_without_inventing_zero(self):
        s=missing_fields();s['nativeProcMeminfo']=native(after_dirty=10,after_writeback=3)
        g=self.evaluate(s,reserve_mib=128)
        self.assertFalse(g['abort']);self.assertTrue(g['cacheBackedAdmission']['used'])
        self.assertEqual(g['inactiveFileCreditBytes'],367*M)
        self.assertEqual(g['reclaimDeductionBytes'],{'total_dirty':10*M,'total_writeback':3*M})
        self.assertEqual(g['missingReclaimDeductionFields'],['total_shmem','total_dirty','total_writeback'])
        self.assertEqual(g['unresolvedReclaimDeductionFields'],[])
        self.assertEqual(g['pressureStatus'],'unknown');self.assertIsNone(g['pressureFullAvg10'])
        self.assertLess(g['headroomToSkillRawCeilingBytes'],128*M);self.assertGreater(g['headroomForStageBytes'],128*M)
        self.assertEqual(g['reclaimDeductionSources']['total_dirty'],'native_proc_global_upper_bound')
    def test_high_dirty_rejected_source_and_inconsistent_upper_bounds_fall_closed(self):
        for kind in ('dirty','source','claimed','unavailable','no_evidence'):
            with self.subTest(kind=kind):
                s=missing_fields()
                if kind=='dirty':s['nativeProcMeminfo']=native(dirty=400)
                elif kind=='source':s['nativeProcMeminfo']['source']['fstype']='fuse.lxcfs'
                elif kind=='claimed':s['nativeProcMeminfo']['upperBoundsBytes']['Dirty']=0
                elif kind=='unavailable':s['nativeProcMeminfo']['status']='unavailable'
                else:s.pop('nativeProcMeminfo')
                g=self.evaluate(s);self.assertTrue(g['abort']);self.assertEqual(g['inactiveFileCreditBytes'],0)
    def test_current_oom_new_fail_and_full_pressure_delta_never_admit_high_raw(self):
        s=missing_fields();before=dict(s)
        for field,value,reason in [('memory.oom_control','under_oom 1','shared_cgroup_current_oom'),('memory.failcnt','3380163','shared_cgroup_limit_event'),('memory.pressure','full avg10=1.00 total=20','raw_emergency_ceiling')]:
            with self.subTest(field=field):self.assertEqual(self.evaluate({**s,field:value},baseline=before)['reason'],reason)
        before={**s,'memory.pressure':'full avg10=0.01 total=10'}
        g=self.evaluate({**s,'memory.pressure':'full avg10=0.01 total=11'},baseline=before)
        self.assertTrue(g['abort']);self.assertEqual(g['pressureFullTotalDelta'],1)
        self.assertIn('shared_cgroup_pressure_since_baseline',g['cacheBackedAdmission']['reasons'])
    def test_actual_limit_working_set_reserve_and_tree_headroom_are_independent(self):
        s=missing_fields()
        stopped=self.evaluate({**s,'memory.usage_in_bytes':str(1024*M)})
        self.assertEqual(stopped['reason'],'sandbox_limit_reached')
        self.assertIn('sandbox_limit_reached',stopped['cacheBackedAdmission']['reasons'])
        self.assertEqual(self.evaluate(missing_fields(usage=900,inactive=10))['reason'],'working_set_ceiling')
        self.assertEqual(self.evaluate(missing_fields(usage=790,inactive=0),reserve_mib=256)['reason'],'insufficient_stage_headroom')
        g=self.evaluate(s,tree_rss=150*M,reserve_mib=128);self.assertTrue(g['abort']);self.assertIn('insufficient_process_tree_headroom',g['cacheBackedAdmission']['reasons'])
        for rss in (None,True,-1,1.2):
            g=self.evaluate(s,tree_rss=rss);self.assertTrue(g['abort']);self.assertIsNone(g['processTreeRssBytes'])
        self.assertEqual(self.evaluate(s,tree_rss=300*M)['reason'],'process_tree_budget')
    def test_stage_reserve_soft80_boundary_and_light_vs_cv_tree_budget(self):
        boundary=int(1024*M*.8)-128*M
        s=missing_fields();s['memory.stat']=f'total_cache {682*M}\ntotal_inactive_file {980*M-boundary+5*M}'
        g=self.evaluate(s,reserve_mib=128);self.assertFalse(g['abort']);self.assertEqual(g['headroomForStageBytes'],128*M)
        above={**s,'memory.usage_in_bytes':str(980*M+1)}
        g=self.evaluate(above,reserve_mib=128);self.assertTrue(g['abort']);self.assertEqual(g['headroomForStageBytes'],128*M-1)
        self.assertFalse(self.evaluate(above,reserve_mib=32)['abort'])
        self.assertFalse(self.evaluate(s,tree_rss=180*M,reserve_mib=32)['abort'])
        self.assertTrue(self.evaluate(s,tree_rss=180*M,reserve_mib=128)['abort'])
    def test_event_baseline_unknown_and_v2_high_raw_keep_raw_guard(self):
        s=missing_fields()
        for baseline in (None,{'memory.failcnt':s['memory.failcnt']},{**s,'memory.failcnt':'invalid'},{**s,'memory.limit_in_bytes':str(512*M)}):
            self.assertTrue(self.evaluate(s,baseline=baseline)['abort'])
        for value in (None,'under_oom -1','under_oom 0\nunder_oom 1'):
            self.assertTrue(self.evaluate({**s,'memory.oom_control':value})['abort'])
        v2={'status':'available','version':2,'memory.current':str(980*M),'memory.max':str(1024*M),'memory.stat':f'file {700*M}\ninactive_file {380*M}\nshmem 0\nfile_dirty 0\nfile_writeback 0','memory.events':'max 0\noom 0\noom_kill 0','memory.pressure':'full avg10=0.00 total=0'}
        self.assertEqual(self.evaluate(v2)['reason'],'raw_emergency_ceiling')
    def test_no_scope_mixing_or_malformed_deduction_defaults(self):
        s=missing_fields();s.pop('nativeProcMeminfo')
        s['memory.stat']+=f'\ndirty 0\nwriteback 0\ninactive_file {400*M}\ncache {700*M}'
        self.assertEqual(self.evaluate(s)['inactiveFileCreditBytes'],0)
        s['nativeProcMeminfo']=native();s['memory.stat']=s['memory.stat'].replace('total_inactive_file '+str(380*M),'total_inactive_file invalid')
        self.assertEqual(self.evaluate(s)['inactiveFileCreditBytes'],0)
    def test_native_proc_parser_strict_units_duplicates_nonnegative(self):
        for raw in ('Dirty: 1 MB\nWriteback: 0 kB','Dirty: -1 kB\nWriteback: 0 kB','Dirty: 1 kB\nDirty: 2 kB\nWriteback: 0 kB','Dirty: 1\nWriteback: 0 kB','Dirty: 1 kB','Dirty: 1 kB\nWriteback: 0 kB\nBad: +1 kB'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):guard.proc_meminfo_bounds(raw)
    def test_proc_source_longest_mount_rejects_overmount_bind_and_symlink(self):
        proc='21 1 0:5 / /proc rw - proc proc rw'
        with patch.object(Path,'is_symlink',return_value=False):
            self.assertEqual(core.native_proc_meminfo_source([proc]),SOURCE)
            for extra in ('22 21 0:6 / /proc/meminfo rw - fuse.lxcfs lxcfs rw','22 21 0:5 /meminfo /proc/meminfo rw - proc proc rw',proc):
                with self.subTest(extra=extra),self.assertRaises(ValueError):core.native_proc_meminfo_source([proc,extra])
        with patch.object(Path,'is_symlink',return_value=True),self.assertRaises(ValueError):core.native_proc_meminfo_source([proc])
    def test_snapshot_preserves_two_raw_samples_and_rejects_source_change_or_permission(self):
        mount='21 1 0:5 / /proc rw - proc proc rw\n22 1 0:7 / /fake-cgroup rw - cgroup cgroup rw,memory'
        before=native()['before'];after=native(after_dirty=20)['after']
        for mode in ('available','changed','permission','invalid'):
            count=0
            def read_text(path,*args,**kwargs):
                nonlocal count
                if str(path)=='/proc/self/cgroup':return '2:memory:/child'
                if str(path)=='/proc/self/mountinfo':
                    count+=1
                    return mount+('\n23 21 0:8 / /proc/meminfo rw - fuse.lxcfs lxcfs rw' if mode=='changed' and count>1 else '')
                if path.name=='memory.stat':return 'total_inactive_file 100000\ntotal_cache 200000'
                return '0'
            side_effect=PermissionError(13,'denied') if mode=='permission' else [before,'Dirty: -1 kB\nWriteback: 0 kB' if mode=='invalid' else after]
            with patch.object(Path,'read_text',read_text),patch.object(Path,'is_symlink',return_value=False),patch.object(core,'native_proc_meminfo_read',side_effect=side_effect):s=core.cgroup_snapshot()
            evidence=s['nativeProcMeminfo']
            self.assertEqual(evidence['status'],'available' if mode=='available' else 'unavailable')
            if mode=='available':
                self.assertEqual(evidence['upperBoundsBytes']['Dirty'],20*M)
                self.assertEqual(evidence['before'],'\n'.join(before.splitlines()[:2]));self.assertEqual(evidence['after'],'\n'.join(after.splitlines()[:2]))
                self.assertLessEqual(evidence['sampleTimes']['before'],evidence['sampleTimes']['after'])
    def test_stage_and_resources_baseline_is_fixed_and_catches_new_event(self):
        s=missing_fields();new={**s,'memory.failcnt':'3380163'}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);core.write(root/'status.json',{'stage':'selection'})
            with patch.object(core,'cgroup_snapshot',return_value=s),patch.object(runtime,'process_tree_rss',return_value=31*M):
                result=runtime.check_stage(root,'selection');self.assertFalse(result['abort']);self.assertEqual(result['baselineFailcnt'],3380162)
            with patch.object(core,'cgroup_snapshot',side_effect=[s,s,new]),patch.object(guard,'process_tree_rss',return_value=31*M):
                resources=core.Resources(root);first=resources.sample();second=resources.sample()
            self.assertEqual(first['memoryEstimate']['reason'],'cache_backed_admitted');self.assertEqual(second['memoryEstimate']['reason'],'shared_cgroup_limit_event')
    def test_monitor_and_resources_share_baseline_and_event_stop_diagnostics(self):
        s=missing_fields();new={**s,'memory.failcnt':'3380163'}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);core.write(root/'status.json',{'stage':'selection'})
            with patch.object(core,'cgroup_snapshot',side_effect=[s,new]) as capture,patch.object(guard,'process_tree_rss',return_value=31*M):
                monitor=runtime.StageMonitor(root,'selection')
                self.assertIs(monitor.sampler.baseline,monitor.baseline);self.assertEqual(capture.call_count,1)
                monitor.observe()
            row=json.loads((root/'resources.ndjson').read_text().splitlines()[-1])
            self.assertEqual(row['memoryEstimate']['reason'],'shared_cgroup_limit_event')
            self.assertEqual(core.read(root/'memory-guard.json')['reason'],monitor.failure)

class AdmissionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):test_cv.CvTests.setUpClass()
    @classmethod
    def tearDownClass(cls):test_cv.CvTests.tearDownClass()
    def setUp(self):
        self.helper=test_cv.CvTests(methodName='runTest');self.helper.setUp();self.parent=self.helper.root.parent
        self.state=self.helper.helper.state;self.state['materialCount']=3;self.state['secondVideo']=self.helper.multi.read_bytes()
    def tearDown(self):self.helper.tearDown()
    def test_two_high_cache_baselines_reach_selection_detail_http_all_A_B_and_one_html(self):
        request=intake.request();request['query_spec']['collection']={'targetTopN':3}
        # First raw reading plus nearby breakdown is a synthetic reference, not
        # a same-instant log replay. Second breakdown below preserves its values.
        cases=[(932937728,791973888,304316416,126312448),(1037201408,895987712,399163392,140689408)]
        for index,(usage,cache,inactive,rss) in enumerate(cases):
            with self.subTest(index=index):
                s=missing_fields();s['memory.usage_in_bytes']=str(usage);s['memory.failcnt']='7369317'
                s['memory.stat']=f'total_cache {cache}\ntotal_rss {rss}\ntotal_inactive_file {inactive}'
                output=self.parent/f'admitted-{index}';selection=self.state['selectionCalls'];details=len(self.state['detailIds']);video_gets=self.state['videoGets']
                with patch.object(core,'cgroup_snapshot',return_value=s),patch.object(guard,'process_tree_rss',return_value=31113216),patch.object(runtime,'process_tree_rss',return_value=31113216),patch.object(cv,'process_tree_rss',return_value=31113216):result=batch.run_batch(request,output)
                self.assertEqual(result['status'],'succeeded',result);self.assertEqual(result['completedCount'],3);self.assertEqual(result['acquiredCount'],3)
                self.assertEqual(self.state['selectionCalls']-selection,1);self.assertEqual(len(self.state['detailIds'])-details,3);self.assertEqual(self.state['videoGets']-video_gets,3)
                self.assertEqual(Path(result['report']['htmlPath']).read_text().count('class="material-panel"'),3)
                self.assertEqual(len(list(output.rglob('*.html'))),1)
                rows=[json.loads(line) for line in (output/'guard-samples.ndjson').read_text().splitlines()]
                self.assertTrue(rows);self.assertFalse(any(row['abort'] for row in rows))
                if index==1:self.assertTrue(any(row['cacheBackedAdmission']['used'] for row in rows))
