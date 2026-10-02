import csv
from datetime import datetime,timedelta
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import probe_core as core
import platform_adapter as adapter

PLATFORM=core.read(ROOT/'config/platform.json')['platform']
TODAY=datetime.now().date()
START=(TODAY-timedelta(days=10)).isoformat();END=(TODAY-timedelta(days=9)).isoformat()

def request():
    if PLATFORM=='qianchuan':
        return {'api_shop':'api','rpa_shop':'rpa','query_spec':{'schemaVersion':1,'source':'qianchuan_direct_material_report','scope':{'shopId':'api'},'period':{'startDate':START,'endDate':END},'filters':{},'collection':{'targetTopN':1}}}
    return {'rpa_shop':'rpa','query_spec':{'schema_version':1,'source':'juliang_yuntu_industry_content_rankings','industry':'食品饮料','period':{'type':'CUSTOM','start_date':START,'end_date':END},'filters':{},'ranking':{'metric':'EXPOSURE_TOP1000'},'collection':{'target_top_n':1}}}

def csv_bytes(row,delimiter=','):
    handle=StringIO();writer=csv.DictWriter(handle,fieldnames=list(row),delimiter=delimiter);writer.writeheader();writer.writerow(row)
    return handle.getvalue().encode()

class Response:
    headers={}
    def __init__(self,chunks):self.chunks=chunks;self.closed=False
    def raise_for_status(self):pass
    def iter_content(self,chunk_size):yield from self.chunks
    def close(self):self.closed=True

class DownloadTests(unittest.TestCase):
    def test_bounded_chunked_download_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'video';response=Response([b'1234',b'5678'])
            session=type('S',(),{'get':lambda *a,**k:response})()
            with self.assertRaises(core.ProbeError):core.download('https://video.test/file',target,max_bytes=5,session=session)
            self.assertFalse(target.exists());self.assertFalse(list(Path(d).glob('*.part')));self.assertTrue(response.closed)
    def test_html_empty_and_invalid_urls(self):
        with tempfile.TemporaryDirectory() as d:
            for data in ([],[b'<html>login</html>']):
                with self.subTest(data=data):
                    response=Response(data);session=type('S',(),{'get':lambda *a,**k:response})()
                    with self.assertRaises(core.ProbeError):core.download('https://x.test',Path(d)/'v',max_bytes=200,session=session)
            for url in ('file:///etc/passwd','https://user:pass@x.test'):
                with self.assertRaises(core.ProbeError):core.download(url,Path(d)/'v',max_bytes=200)
    def test_nested_files_unique_and_json_only_rejected(self):
        self.assertEqual(core.file_urls({'result_content':{'files':[{'fileUrl':'https://x.test/f'}]}}),'https://x.test/f')
        for data in ({'records':[{'url':'https://x.test'}]},{'files':[{'fileUrl':'a','url':'b'}]}):
            with self.assertRaises(core.ProbeError):core.file_urls(data)
    def test_offline_intake_limits_and_dates(self):
        r=request();query=r['query_spec'];query['collection']={'targetTopN':2} if PLATFORM=='qianchuan' else {'target_top_n':2}
        with self.assertRaises(Exception):adapter.validate(r)
        r=request();period=r['query_spec']['period'];period['endDate' if PLATFORM=='qianchuan' else 'end_date']=TODAY.isoformat()
        with self.assertRaises(Exception):adapter.validate(r)
    def test_dynamic_schema_and_list(self):
        from connector_contract import validate_business_params
        with self.assertRaises(Exception):validate_business_params({'inParamList':[{'param':'material_id','isRequired':True,'dataTypeEnum':'STRING'}]},{'unexpected':1})
        with self.assertRaises(Exception):validate_business_params({'inParamJsonSchemaText':json.dumps({'type':'object','required':['material_id']})},{})
    def test_unknown_submission_is_never_resubmitted(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);gate=core.Gateway(root,environ={'ENV_BACKEND_HOST':'https://x.test','YUCE_AUTHORIZATION':'fake'})
            detail={'platformCode':'p','inParamList':[]};gate.detail=lambda code:detail;gate.account=lambda *_:{}
            calls=[]
            def post(path,payload):calls.append(path);raise core.ProbeError('transport_lost')
            gate.post=post
            for _ in range(2):
                with self.assertRaises(core.ProbeError):gate.csv('detail','connector',{},'rpa')
            self.assertEqual(calls,['/adg/v1/agent/fetch/tasks'])
            self.assertEqual(core.read(root/'acquisition/tasks.json')['detail']['status'],'submission_intent')
    def test_output_and_lock_boundary(self):
        with self.assertRaises(core.ProbeError):core.external_root(ROOT/'runs')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with core.run_lock(root):
                with self.assertRaises(core.ProbeError):
                    with core.run_lock(root):pass

class AcquisitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import imageio_ffmpeg
        cls.temp=tempfile.TemporaryDirectory();cls.video=Path(cls.temp.name)/'tiny.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=red:s=160x90:d=1:r=25','-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(cls.video)],check=True)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.state={'submits':0,'videoGets':0,'wrongId':False,'missingUrl':False,'noCsv':False}
        state=self.state;video=self.video
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def respond(self,content):
                self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
            def do_POST(self):
                raw=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path.endswith('listByConnectorCode'):
                    data=[{'connectorCode':raw['connectorCode'],'platformCode':'p','inParamJsonSchemaText':json.dumps({'type':'object'})}]
                elif self.path.endswith('authorizations/query'):data=[{'shop_id':'rpa','account':'123','platform':'p'}]
                elif self.path.endswith('shops/advertisers'):data=[{'shop_id':'api','advertiser_id':'123'}]
                elif self.path.endswith('/invoke'):
                    from qianchuan_report_client import DEFAULT_METRICS
                    data={'result_type':'payload','result_content':[{'dimensions':{'material_id':{'Value':'101'},'roi2_material_video_name':{'ValueStr':'fixture'}},'metrics':{key:{'Value':'10'} for key in DEFAULT_METRICS}}]}
                elif self.path.endswith('/tasks'):
                    state['submits']+=1;phase='list' if raw['function_code'].endswith('.list') else 'detail';data={'task_group_id':phase}
                elif self.path.endswith('/tasks/status'):
                    data={'status':'completed','result_content':{'records':[{}]} if state['noCsv'] else {'files':[{'fileUrl':f'http://127.0.0.1:{self.server.server_port}/'+raw['task_group_id']+'.csv'}]}}
                else:raise AssertionError(self.path)
                self.respond(json.dumps({'success':True,'data':data}).encode())
            def do_GET(self):
                base=f'http://127.0.0.1:{self.server.server_port}'
                if self.path=='/list.csv':payload=csv_bytes({'material_id':'101','title':'fixture','object_index':json.dumps({'exposure_cnt':10})})
                elif self.path=='/detail.csv':
                    identity='102' if state['wrongId'] else '101';url='' if state['missingUrl'] else base+'/video'
                    if PLATFORM=='qianchuan':payload=csv_bytes({'materialId':identity,'recordType':'material_info','materialName':'fixture','actualStartDate':START,'actualEndDate':END,'videoUrl':url},'\x01')
                    else:payload=csv_bytes({'materialId':identity,'dateType':'CUSTOM','customStartDate':START,'customEndDate':END,'coreData':json.dumps({'objectId':identity,'title':'fixture','videoUrl':url})})
                else:
                    state['videoGets']+=1
                    assert not self.headers.get('Cookie'),'gateway cookie reached CDN'
                    payload=video.read_bytes()
                self.respond(payload)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.env=patch.dict(os.environ,{'ENV_BACKEND_HOST':f'http://127.0.0.1:{self.server.server_port}','YUCE_AUTHORIZATION':'fake-test-secret','YCSESSIONID':'fake-test-cookie','YUCE_SESSION_ID':'fake-test-agent'})
        self.env.start();self.root_temp=tempfile.TemporaryDirectory()
    def tearDown(self):
        self.root_temp.cleanup();self.env.stop();self.server.shutdown();self.server.server_close();self.thread.join()
    def test_full_real_http_acquisition_and_tamper_gate(self):
        root=Path(self.root_temp.name)/'run'
        result=core.acquire(request(),root)
        self.assertEqual(result['status'],'video_ready');self.assertEqual(result['cvStatus'],'not_run')
        receipt=core.verify(root);self.assertGreater(receipt['media']['durationSec'],0)
        paths=[a['path'] for a in receipt['artifacts']];self.assertEqual(len(paths),len(set(paths)))
        self.assertEqual(self.state['submits'],1 if PLATFORM=='qianchuan' else 2)
        self.assertEqual(self.state['videoGets'],1)
        core.acquire(request(),root,resume=True)
        self.assertEqual(self.state['submits'],1 if PLATFORM=='qianchuan' else 2)
        text=(root/'report/index.html').read_text();self.assertNotIn('fake-test-secret',text);self.assertNotIn('/video"',text)
        with (root/'media/source-video.mp4').open('ab') as handle:handle.write(b'tamper')
        with self.assertRaises(core.ProbeError):core.verify(root)
        with self.assertRaises(core.ProbeError):core.report(root)
    def test_wrong_identity_blocks_download_and_resume(self):
        self.state['wrongId']=True;root=Path(self.root_temp.name)/'run'
        with self.assertRaises(core.ProbeError):core.acquire(request(),root)
        state=core.read(root/'status.json');self.assertEqual(state['firstBatchCsvGate']['status'],'blocked')
        self.assertEqual(self.state['videoGets'],0)
        with self.assertRaises(core.ProbeError):core.acquire(request(),root,resume=True)
    def test_url_gap_not_misdiagnosed_as_bad_input_csv(self):
        self.state['missingUrl']=True;root=Path(self.root_temp.name)/'run'
        with self.assertRaises(core.ProbeError):core.acquire(request(),root)
        state=core.read(root/'status.json');self.assertEqual(state['firstBatchCsvGate']['status'],'passed');self.assertEqual(self.state['stage'] if 'stage' in self.state else state['stage'],'video_source')
        self.assertEqual(self.state['videoGets'],0)
    def test_no_csv_stops_external_tasks(self):
        self.state['noCsv']=True;root=Path(self.root_temp.name)/'run'
        with self.assertRaises(core.ProbeError):core.acquire(request(),root)
        self.assertEqual(self.state['videoGets'],0)
        if PLATFORM=='qianchuan':self.assertEqual(core.read(root/'status.json')['firstBatchCsvGate']['status'],'blocked')
    def test_known_task_resume_reuses_csv_and_video(self):
        root=Path(self.root_temp.name)/'run'
        def fail_probe(path):raise core.ProbeError('media_probe_failed')
        with self.assertRaises(core.ProbeError):core.acquire(request(),root,media_probe=fail_probe)
        submitted=self.state['submits']
        self.assertEqual(core.read(root/'status.json')['firstBatchCsvGate']['status'],'passed')
        result=core.acquire(request(),root,resume=True)
        self.assertEqual(result['status'],'video_ready')
        self.assertEqual(self.state['submits'],submitted);self.assertEqual(self.state['videoGets'],1)
        public=core.read(root/'report/report.json')
        self.assertGreater(public['memoryObservation']['sampleCount'],0)
        self.assertEqual(public['videoArtifact']['sha256'],core.digest(root/'media/source-video.mp4'))
        self.assertTrue(public['runtime']['dependencies']['requests'])
    def test_accepted_csv_cannot_be_replaced_during_resume(self):
        root=Path(self.root_temp.name)/'run'
        def fail_probe(path):raise core.ProbeError('media_probe_failed')
        with self.assertRaises(core.ProbeError):core.acquire(request(),root,media_probe=fail_probe)
        with (root/'acquisition/detail.csv').open('ab') as handle:handle.write(b'changed')
        with self.assertRaises(core.ProbeError) as error:core.acquire(request(),root,resume=True)
        self.assertEqual(error.exception.code,'accepted_csv_changed')
        self.assertEqual(self.state['videoGets'],1)
    def test_ffmpeg_header_fallback_on_real_video(self):
        with patch('probe_core.shutil.which',return_value=None):info=core.probe_media(self.video)
        self.assertEqual(info['probeProvider'],'ffmpeg_header')
        self.assertEqual((info['width'],info['height']),(160,90))
    def test_precision_aware_duration_and_real_mismatch(self):
        media={'durationSec':59.3,'width':576,'height':1024,'fps':30}
        matched=core.compare_media({'durationSec':59},media)
        self.assertEqual(matched['status'],'matched');self.assertEqual(matched['comparisons'][0]['tolerance'],1)
        self.assertEqual(core.compare_media({'durationSec':57},media)['status'],'mismatch')
        self.assertEqual(core.compare_media({'durationSec':59.01},media)['status'],'mismatch')
        self.assertEqual(core.compare_media({'width':1080},media)['status'],'mismatch')
        with self.assertRaises(core.ProbeError):core.compare_media({'durationSec':float('nan')},media)
    def test_failure_report_keeps_measured_media_and_selection(self):
        root=Path(self.root_temp.name)/'run';original=adapter.video_source
        def mismatched(*args):
            value=original(*args);value['expectedMedia']={'durationSec':59};return value
        with patch.object(adapter,'video_source',side_effect=mismatched):
            with self.assertRaises(core.ProbeError):core.acquire(request(),root)
        report=core.read(root/'report/report.json')
        self.assertEqual(report['status'],'failed');self.assertEqual(report['materialId'],'101')
        self.assertEqual(report['media']['width'],160);self.assertEqual(report['mediaValidation']['status'],'mismatch')
        self.assertFalse(report['acquisitionVerified']);self.assertTrue(report['videoArtifact'])
        self.assertFalse((root/'acquisition/receipt.json').exists())
    def test_complete_export_and_report_only_bundle_rejected(self):
        from delivery import export_report,verify_export
        import zipfile,shutil
        root=Path(self.root_temp.name)/'run';core.acquire(request(),root)
        exported=Path(self.root_temp.name)/'bundle';result=export_report(root,exported)
        self.assertTrue(result['containsVideo']);self.assertTrue(verify_export(exported)['containsVideo'])
        with zipfile.ZipFile(result['zipPath']) as z:
            self.assertIsNone(z.testzip());self.assertIn('media/source-video.mp4',z.namelist())
            self.assertIn('resources.ndjson',z.namelist());self.assertNotIn('acquisition/source.json',z.namelist())
            clean=Path(self.root_temp.name)/'clean';z.extractall(clean);verify_export(clean)
        (clean/'media/source-video.mp4').unlink()
        with self.assertRaises(core.ProbeError):verify_export(clean)
        report_only=Path(self.root_temp.name)/'report-only';shutil.copytree(exported/'report',report_only/'report')
        with self.assertRaises(core.ProbeError) as error:verify_export(report_only)
        self.assertEqual(error.exception.code,'delivery_manifest_missing')
    def test_preflight_and_help_without_site_dependencies(self):
        p=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/run.py'),'preflight'],capture_output=True,text=True)
        self.assertEqual(p.returncode,2);self.assertEqual(json.loads(p.stdout)['status'],'dependency_missing')
        self.assertNotIn('Traceback',p.stderr)
        p=subprocess.run([sys.executable,'-S',str(ROOT/'scripts/run.py'),'--help'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0)
    def test_resource_resume_gap_is_not_active_time(self):
        root=Path(self.root_temp.name)
        rows=[{'time':1,'attemptId':'a','pid':1},{'time':2,'attemptId':'a','pid':1},{'time':100,'attemptId':'b','pid':2},{'time':101,'attemptId':'b','pid':2}]
        (root/'resources.ndjson').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        summary=core.resource_summary(root)
        self.assertEqual(summary['elapsedObservedSec'],100);self.assertEqual(summary['activeSampledSec'],2)
    def test_media_input_limit(self):
        with patch('probe_core.shutil.which',return_value='/fake/ffprobe'),patch('probe_core.subprocess.run') as run:
            run.return_value=type('R',(),{'returncode':0,'stdout':json.dumps({'streams':[{'codec_type':'video','codec_name':'h264','width':3840,'height':2160,'avg_frame_rate':'25/1'}],'format':{'duration':'1'}})})()
            with self.assertRaises(core.ProbeError):core.probe_media(self.video)
