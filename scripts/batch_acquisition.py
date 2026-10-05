"""One selection, bounded distinct-material serial A+B. No automatic retries."""
from pathlib import Path
import uuid
from probe_core import (ROOT, ProbeError, Gateway, acquire, external_root, read, write,
                        artifact, safe_file, digest, fingerprint, resource_summary, safe_error)
from runtime_memory import StageMonitor, check_stage, release_completed


def run_batch(request, output, *, gateway_factory=Gateway):
    adapter=__import__('platform_adapter')
    normalized=adapter.validate(request)
    count=adapter.count(normalized)
    output=external_root(output);output.mkdir(parents=True,exist_ok=False)
    selection_root=output/'selection';selection_root.mkdir()
    write(selection_root/'request.json',normalized)
    result={'schemaVersion':1,'packageVersion':read(ROOT/'config/platform.json')['version'],
            'platform':read(ROOT/'config/platform.json')['platform'],'batchId':'batch-'+uuid.uuid4().hex[:12],
            'status':'running','concurrency':1,'requestedCount':count,'selectedCount':0,'completedCount':0,
            'acquisition':'one_selection_serial_A_B','entries':[]}
    write(output/'batch.json',result)
    gateway=None;monitor=StageMonitor(output,'batch_selection')
    try:
        from cv_probe import probe_cv
        from visual_report import export_batch_html
        with monitor:
            check_stage(selection_root,'selection')
            gateway=gateway_factory(selection_root)
            selected=adapter.select_many(normalized,selection_root,gateway)
            ids=[str(m.get('materialId') or m.get('material_id')) for m,_ in selected]
            if not selected or len(ids)!=len(set(ids)) or len(selected)>count:raise ProbeError('batch_selection_invalid')
            source_ref=read(selection_root/'acquisition/selection-source.json')
            source=safe_file(selection_root,source_ref['path'])
            if artifact(source,selection_root)!=source_ref:raise ProbeError('selection_source_changed')
            queue={'requestSha256':digest(selection_root/'request.json'),'source':source_ref,
                   'selected':[{'material':m,'params':p} for m,p in selected]}
            write(selection_root/'acquisition/queue.json',queue)
            queue_sha=digest(selection_root/'acquisition/queue.json')
            result.update(selectedCount=len(selected),selectionSha256=queue_sha,
                          shortageCount=count-len(selected),entries=[{'index':i,'materialId':mid,'status':'pending'} for i,mid in enumerate(ids,1)])
            write(output/'batch.json',result)
            release_completed(selection_root,'batch_selection_complete')
            # Close the selection session before any detail work; never reselect per material.
            session=getattr(gateway,'session',None)
            if session is not None and hasattr(session,'close'):session.close()
            gateway=None
            for entry,(material,params) in zip(result['entries'],selected):
                monitor.checkpoint('batch_A_'+str(entry['index']))
                root=output/'runs'/('item-'+str(entry['index']))
                entry.update(status='running',runDir=str(root));write(output/'batch.json',result)
                seed={'material':material,'params':params,'sourcePath':str(source),
                      'sourceArtifact':{'path':source.name,'sha256':source_ref['sha256'],'sizeBytes':source_ref['sizeBytes']},
                      'binding':{'batchId':result['batchId'],'index':entry['index'],'queueSha256':queue_sha,
                                 'requestSha256':queue['requestSha256'],'source':source_ref,'selectedMaterialSha256':fingerprint({'material':material,'params':params})}}
                acquire(normalized,root,gateway_factory=gateway_factory,adapter=adapter,selection_seed=seed)
                monitor.checkpoint('batch_B_'+str(entry['index']))
                attempt=result['batchId']+'-'+str(entry['index']);entry['attemptId']=attempt
                probe_cv(root,attempt_id=attempt,backend='ffmpeg-scene')
                receipt=read(root/'cv'/attempt/'receipt.json')
                entry.update(cvStatus=receipt['status'],inputVideoSha256=receipt['inputVideoSha256'],
                             workerExitCode=receipt['workerExitCode'],processCleanup=receipt['processCleanup'])
                if receipt['status']!='succeeded':raise ProbeError(receipt.get('errorCode') or 'batch_cv_failed')
                if receipt['processCleanup']['status']!='completed':raise ProbeError('process_cleanup_unconfirmed')
                entry['status']='succeeded';result['completedCount']+=1
                entry['memoryObservation']=resource_summary(root/'cv'/attempt)
                release_completed(root,'batch_item_complete')
                monitor.checkpoint('batch_item_complete_'+str(entry['index']))
                write(output/'batch.json',result)
            result['status']='succeeded' if len(selected)==count else 'partial'
            write(output/'batch.json',result)
            monitor.checkpoint('batch_html')
            result['report']=export_batch_html(output,output/'index.html')
        monitor.check()
    except Exception as exc:
        result.update(status='failed',errorCode=getattr(exc,'code','batch_failed'),message=safe_error(exc))
        for entry in result['entries']:
            if entry['status']=='running':entry.update(status='failed',errorCode=result['errorCode'])
        # Preserve completed work; a safe compact report can still describe partial results.
        if monitor.failure is None:
            try:
                write(output/'batch.json',result)
                result['report']=export_batch_html(output,output/'index.html')
            except Exception as report_exc:result['reportErrorCode']=getattr(report_exc,'code','report_failed')
    finally:
        session=getattr(gateway,'session',None)
        if session is not None and hasattr(session,'close'):session.close()
        result['distinctVideoCount']=len({e['inputVideoSha256'] for e in result['entries'] if e.get('inputVideoSha256')})
        result['guardStopReason']=monitor.failure;result['memoryObservation']=resource_summary(output)
        write(output/'status.json',{'status':result['status'],'stage':'batch_complete','errorCode':result.get('errorCode')})
        write(output/'batch.json',result)
    return result
