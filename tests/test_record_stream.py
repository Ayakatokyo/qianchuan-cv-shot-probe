from pathlib import Path
import csv,json,shutil,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import probe_core as core
import platform_adapter as adapter
import qianchuan_report_client as client
from record_stream import FileRecords,json_array_records
from material_query import execute_material_query, MaterialQueryError
from field_registry import load_field_registry
from config_specs import resolve_query_spec,query_spec_to_query_config
from test_acquisition import request

def record(identity,cost='10'):
    return {'dimensions':{'material_id':{'Value':identity},'roi2_material_video_name':{'ValueStr':'fixture'}},'metrics':{key:{'Value':cost} for key in client.DEFAULT_METRICS}}

class RecordStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.registry=load_field_registry(core.ROOT/'config/asset-field-registry-v1.json')
        self.query=query_spec_to_query_config(resolve_query_spec(adapter.validate(request())['query_spec'],self.registry))
    def tearDown(self):self.temp.cleanup()
    def csv(self,records):
        path=self.root/'original.csv'
        with path.open('w',newline='') as handle:
            writer=csv.writer(handle)
            for row in records:writer.writerow([json.dumps(row)])
        return path
    def parse(self,path):
        target=self.root/'source';target.mkdir(exist_ok=True)
        with patch.object(client,'_download_file',side_effect=lambda url,dest,session:shutil.copy2(path,dest)):
            return client._records_from_files({'status':'COMPLETED','files':[{'fileUrl':'https://example.test/report.csv'}]},target,None,stream=True)
    def test_stream_storage_top1_equivalence_and_ties(self):
        rows=[record('101','9'),record('103','10'),record('102','10')]
        stream=self.parse(self.csv(rows))
        self.assertIsInstance(stream,FileRecords);self.assertEqual(len(stream),3)
        self.assertEqual(list(stream),rows)
        self.assertFalse(list((self.root/'source').glob('*.structured.json')))
        bounded=execute_material_query(stream,self.query,self.registry,bounded=True)
        self.assertEqual(bounded,execute_material_query(rows,self.query,self.registry))
        self.assertEqual(bounded['materials'][0]['materialId'],'103')
        for direction in ('asc','desc'):
            query={**self.query,'sort':{**self.query['sort'],'direction':direction}}
            self.assertEqual(execute_material_query(iter(rows),query,self.registry,bounded=True),execute_material_query(rows,query,self.registry))
    def test_late_duplicate_and_missing_metric_are_not_hidden_by_top1(self):
        with self.assertRaises(MaterialQueryError):execute_material_query(iter([record('101','20'),record('102'),record('101')]),self.query,self.registry,bounded=True)
        row=record('102');row['metrics'].pop(client.DEFAULT_METRICS[-1])
        with self.assertRaises(client.ReportClientError):self.parse(self.csv([record('101','20'),row]))
        self.assertEqual(core.read(self.root/'source/result-parse-status.json')['status'],'failed')
    def test_legacy_checkpoint_stream_and_malformed_tail(self):
        rows=[record(str(i)) for i in range(25)];path=self.root/'legacy.json';core.write(path,rows)
        self.assertEqual(list(FileRecords(path)),rows)
        for text in ('[{},]','[{}] trailing','[{','{}','[1]'):
            path.write_text(text)
            with self.assertRaises(ValueError):list(json_array_records(path))
    def test_files_download_failure_has_explicit_status(self):
        target=self.root/'source';target.mkdir()
        with patch.object(client,'_download_file',side_effect=core.ProbeError('download_failed')):
            with self.assertRaises(client.ReportClientError):client._records_from_files({'status':'COMPLETED','files':[{'fileUrl':'https://example.test/report.csv'}]},target,None,stream=True)
        self.assertEqual(core.read(target/'result-download-status.json')['status'],'failed')

    def test_gateway_csv_reader_does_not_depend_on_url_extension(self):
        original=self.csv([record('101')]);target=self.root/'source';target.mkdir()
        with patch.object(client,'_download_file',side_effect=lambda url,dest,session:shutil.copy2(original,dest)):
            stream=client._records_from_files({'status':'COMPLETED','files':[{'fileUrl':'https://example.test/report-download'}]},target,None,stream=True)
        self.assertEqual(list(stream),[record('101')])
