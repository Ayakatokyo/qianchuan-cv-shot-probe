"""Qianchuan sample selection and explicit CSV source binding."""
from copy import deepcopy
from pathlib import Path
from config_specs import resolve_query_spec, query_spec_to_query_config
from field_registry import load_field_registry
from material_query import execute_material_query
from record_stream import FileRecords
from qianchuan_report_client import fetch_material_report
from rpa_csv_parser import parse_diagnosis_csv
from probe_core import ProbeError, ROOT, resource_id, read, write, artifact

DETAIL_CODE='rpa.conn.juliang.qc.material.analysis.video.live.diagnosis'

def validate(raw):
    if not isinstance(raw,dict) or set(raw)-{'api_shop','rpa_shop','query_spec'}:raise ProbeError('request_invalid')
    api=resource_id(raw.get('api_shop')); rpa=resource_id(raw.get('rpa_shop'))
    spec=deepcopy(raw.get('query_spec'))
    if not isinstance(spec,dict):raise ProbeError('query_spec_required')
    scope=spec.setdefault('scope',{});scope['shopId']=api
    scope.setdefault('advertiserId','1') # Syntax placeholder only; never submitted.
    registry=load_field_registry(ROOT/'config/asset-field-registry-v1.json')
    spec=resolve_query_spec(spec,registry)
    count=spec['collection']['targetTopN']
    if not 1<=count<=10 or spec['collection']['candidateTopN']!=count:raise ProbeError('batch_count_invalid')
    return {'api_shop':api,'rpa_shop':rpa,'query_spec':spec}

def select_many(request,root,gateway):
    detail=gateway.detail(DETAIL_CODE); account=gateway.account(detail,request['rpa_shop'])
    advertiser=str(account.get('account') or '')
    if not advertiser.isascii() or not advertiser.isdecimal():raise ProbeError('advertiser_invalid')
    rows=gateway.post('/adg/v1/agent/api-platform/shops/advertisers',{'authorization':gateway.auth,'platform':'OCEANENGINE'})
    matches=[r for r in rows if str(r.get('shop_id'))==request['api_shop'] and str(r.get('advertiser_id'))==advertiser] if isinstance(rows,list) else []
    if len(matches)!=1:raise ProbeError('api_advertiser_not_authorized')
    spec=deepcopy(request['query_spec']);spec['scope']['advertiserId']=advertiser
    registry=load_field_registry(ROOT/'config/asset-field-registry-v1.json')
    query=query_spec_to_query_config(resolve_query_spec(spec,registry))
    source=root/'acquisition/report-records.json'
    if source.exists():records=FileRecords(source)
    else:
        rank=query['sort']; field=registry.require(rank['field'])['source'][1]
        records=fetch_material_report(request['api_shop'],advertiser,query['period']['startDate'],query['period']['endDate'],output_dir=root/'acquisition/report-source',order_by=[{'field':field,'type':2 if rank['direction']=='desc' else 1}],session=gateway.session,stream=True)
        if isinstance(records,FileRecords):source=records.path
        else:write(source,records) # Small inline gateway payload compatibility.
    material=execute_material_query(records,query,registry,bounded=True)['materials']
    if not material:raise ProbeError('selection_empty')
    write(root/'acquisition/selection-source.json',artifact(source,root))
    return [(m,{'material_id':m['materialId'],'date_type':'CUSTOM','custom_start_date':query['period']['startDate'],'custom_end_date':query['period']['endDate']}) for m in material]

def video_source(csv_path,material,params):
    rows=parse_diagnosis_csv(csv_path);target=str(material['materialId'])
    infos=[(i,r) for i,r in enumerate(rows,2) if r.get('recordType')=='material_info']
    if not infos or any(str(r.get('materialId') or '')!=target for _,r in infos):raise ProbeError('csv_identity_mismatch')
    urls={str(r.get('videoUrl') or '').strip() for _,r in infos if r.get('videoUrl')}
    # Check the actual exported period, not only the period in the request.
    def date(value):return str(value or '').replace('-','')
    for _,r in infos:
        for keys,expected in [(('actualStartDate','customStartDate'),params['custom_start_date']),(('actualEndDate','customEndDate'),params['custom_end_date'])]:
            echoed=[r[k] for k in keys if r.get(k)]
            if not echoed or any(date(v)!=date(expected) for v in echoed):raise ProbeError('csv_period_mismatch')
        if material.get('materialName') and r.get('materialName') and material['materialName']!=r['materialName']:raise ProbeError('csv_name_mismatch')
    if len(urls)!=1:raise ProbeError('video_url_missing_or_conflicting')
    return {'materialId':target,'url':urls.pop(),'csvPath':str(Path(csv_path).name),'recordIndex':infos[0][0]-2,'fieldPath':'videoUrl','identityStatus':'matched','periodStatus':'matched','expectedMedia':{'durationSec':infos[0][1].get('durationSec') or infos[0][1].get('videoDuration')}}

def count(request):return request['query_spec']['collection']['targetTopN']

def select(request,root,gateway):
    if count(request)!=1:raise ProbeError('use_run_batch')
    return select_many(request,root,gateway)[0]
