"""Bounded serial acceptance queue over existing A inputs; never acquires data."""
from pathlib import Path
import uuid
from probe_core import ROOT, ProbeError, external_root, read, write, digest, resource_summary, safe_error
from runtime_memory import StageMonitor, release_item


def probe_batch(manifest_file, output, *, delivery_mode='html'):
    if delivery_mode not in ('html','audit'):raise ProbeError('batch_delivery_invalid')
    manifest_file=Path(manifest_file)
    if manifest_file.stat().st_size>65536:raise ProbeError('batch_manifest_limit')
    manifest=read(manifest_file)
    if not isinstance(manifest,dict) or set(manifest)!={'schemaVersion','runs'} or manifest['schemaVersion']!=1:
        raise ProbeError('batch_manifest_invalid')
    runs=manifest['runs']
    if not isinstance(runs,list) or not 1<=len(runs)<=10:raise ProbeError('batch_manifest_limit')
    roots=[]
    for item in runs:
        if not isinstance(item,dict) or set(item)!={'runDir'} or not isinstance(item['runDir'],str) or not Path(item['runDir']).is_absolute():
            raise ProbeError('batch_manifest_invalid')
        root=external_root(item['runDir'])
        if not root.is_dir():raise ProbeError('batch_input_missing')
        # Pure local schema/platform check; no authorization lookup or acquisition.
        __import__('platform_adapter').validate(read(root/'request.json'))
        roots.append(root)
    output=external_root(output)
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots):raise ProbeError('batch_output_overlap')
    output.mkdir(parents=True,exist_ok=False)
    batch_id='serial-'+uuid.uuid4().hex[:12]
    entries=[{'index':i,'runDir':str(root),'attemptId':batch_id+'-'+str(i),'status':'pending'} for i,root in enumerate(roots,1)]
    result={'schemaVersion':1,'packageVersion':read(ROOT/'config/platform.json')['version'],
            'platform':read(ROOT/'config/platform.json')['platform'],'batchId':batch_id,'runDir':str(output),'status':'running',
            'manifestSha256':digest(manifest_file),'concurrency':1,'acquisition':'reused_A_only',
            'entries':entries,'completedCount':0,'requestedCount':len(entries)}
    write(output/'batch.json',result)
    monitor=StageMonitor(output,'batch_precheck')
    try:
        from cv_probe import probe_cv, verify_cv
        from delivery import export_report
        from visual_report import snapshot_report, export_batch_html
        with monitor:
            for entry,root in zip(entries,roots):
                monitor.checkpoint('batch_cv_'+str(entry['index']))
                entry['status']='running';write(output/'batch.json',result)
                probe_cv(root,attempt_id=entry['attemptId'],backend='ffmpeg-scene',_defer_report=True)
                receipt=read(root/'cv'/entry['attemptId']/'receipt.json')
                entry.update(cvStatus=receipt['status'],inputVideoSha256=receipt['inputVideoSha256'],
                             workerExitCode=receipt['workerExitCode'],processCleanup=receipt['processCleanup'])
                if receipt['status']!='succeeded':raise ProbeError(receipt.get('errorCode') or 'batch_cv_failed')
                if receipt['processCleanup']['status']!='completed':raise ProbeError('process_cleanup_unconfirmed')
                verify_cv(root,entry['attemptId'])
                snapshot=output/'snapshots'/('item-'+str(entry['index']))
                entry.update(snapshot_report(root,snapshot,expected_attempt=entry['attemptId']))
                entry['memoryObservation']=resource_summary(root/'cv'/entry['attemptId'])
                entry['status']='succeeded';result['completedCount']+=1;write(output/'batch.json',result)
                release_item(root,'batch_item_complete',attempt_id=entry['attemptId'],report_root=snapshot)
                monitor.checkpoint('batch_item_complete_'+str(entry['index']))
            if delivery_mode=='audit':
                for entry,root in zip(entries,roots):
                    monitor.checkpoint('batch_export_'+str(entry['index']))
                    entry['export']=export_report(root,output/('item-'+str(entry['index'])),expected_attempt=entry['attemptId'],_report_snapshot=entry)
                    write(output/'batch.json',result)
            if delivery_mode=='html':
                from visual_report import export_batch_html
                result['status']='succeeded';write(output/'batch.json',result)
                result['report']=export_batch_html(output,output/'index.html')
        monitor.check()
        result['status']='succeeded'
    except Exception as exc:
        result.update(status='failed',errorCode=getattr(exc,'code','batch_failed'),message=safe_error(exc))
        guard_stopped=result['errorCode'] in ('operation_memory_guard_aborted','memory_guard_aborted','insufficient_headroom','insufficient_stage_headroom')
        if guard_stopped:
            result.update(guardStopReason=result['errorCode'],guardStopOrigin='phase_admission' if result['errorCode']=='insufficient_stage_headroom' else 'failure_code')
            for entry in entries:
                if entry.get('attemptId') and entry.get('runDir'):
                    guard_path=Path(entry['runDir'])/'cv'/entry['attemptId']/'memory-guard.json'
                    if guard_path.exists():
                        guard=read(guard_path)
                        if guard.get('abort'):result.update(guardStopReason=guard.get('reason') or result['errorCode'],guardStopOrigin='cv_attempt_guard')
        for entry in entries:
            if entry['status']=='running':entry.update(status='failed',errorCode=result['errorCode'])
        if delivery_mode=='html' and monitor.failure is None and not guard_stopped and result['errorCode']!='process_cleanup_unconfirmed' and not (output/'index.html').exists():
            try:
                for entry,root in zip(entries,roots):
                    if not entry.get('reportSnapshot'):
                        snapshot=output/'snapshots'/('item-'+str(entry['index']))
                        entry.update(snapshot_report(root,snapshot,entry=entry))
                        release_item(root,'batch_stopped_item',report_root=snapshot)
                write(output/'batch.json',result)
                result['report']=export_batch_html(output,output/'index.html')
            except Exception as report_exc:result['reportErrorCode']=getattr(report_exc,'code','report_failed')
    finally:
        result['distinctVideoCount']=len({e['inputVideoSha256'] for e in entries if e.get('inputVideoSha256')})
        result['guardStopReason']=monitor.failure or result.get('guardStopReason')
        if monitor.failure:result['guardStopOrigin']='queue_monitor'
        result['memoryObservation']=resource_summary(output)
        result['acceptanceNote']='Repeated identical video hashes prove repetition only; diverse-video batch and manual quality require separate evidence.'
        write(output/'status.json',{'status':result['status'],'stage':'batch_complete'})
        write(output/'batch.json',result)
    return result
