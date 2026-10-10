"""One selection, three-task RPA waves and serial media before serial CV. No automatic retries."""
from pathlib import Path
import uuid
from probe_core import (ROOT, ProbeError, Gateway, acquire, external_root, read, write,
                        artifact, safe_file, digest, fingerprint, resource_summary, safe_error)
from runtime_memory import StageMonitor, check_stage, release_completed, release_item


RPA_CONCURRENCY = 3


def acquire_waves(normalized, selected, source, source_ref, queue, queue_sha, result, output, gateway_factory, adapter, monitor):
    """At most three remote tasks; collect a whole wave before serial media work.
    Drain known submissions on ordinary failure, never refill or resubmit.
    """
    def save():write(output/'batch.json',result)
    def fail(entry,exc):
        entry.update(status='failed',acquisitionStatus='failed',errorCode=getattr(exc,'code','acquisition_failed'))
        tasks=read(Path(entry['runDir'])/'acquisition/tasks.json') if (Path(entry['runDir'])/'acquisition/tasks.json').exists() else {}
        task=tasks.get('detail',{})
        entry['rpaStatus']='csv_ready' if entry.get('rpaStatus')=='csv_ready' else 'failed' if task.get('status') in ('completed','partial_success','failed') else 'unconfirmed'
        save()
    result['rpaWaves']=[];result['rpaSubmissionCount']=0
    for offset in range(0,len(selected),RPA_CONCURRENCY):
        cohort=result['entries'][offset:offset+RPA_CONCURRENCY]
        wave={'index':len(result['rpaWaves'])+1,'materialIds':[e['materialId'] for e in cohort],'status':'submitting'}
        result['rpaWaves'].append(wave);errors=[]
        if offset==0:
            result['firstBatchCsvGate']={'status':'pending','materialIds':wave['materialIds'],'outcomes':{},'validCsvCount':0}
        save()
        for entry,(material,params) in zip(cohort,selected[offset:offset+RPA_CONCURRENCY]):
            root=output/'runs'/('item-'+str(entry['index']))
            entry.update(status='running',acquisitionStatus='running',runDir=str(root),rpaWave=wave['index'])
            result['acquisitionPhase']='detail_submit';save()
            monitor.checkpoint('batch_A_'+str(entry['index']))
            seed={'material':material,'params':params,'sourcePath':str(source),
                  'sourceArtifact':{'path':source.name,'sha256':source_ref['sha256'],'sizeBytes':source_ref['sizeBytes']},
                  'binding':{'batchId':result['batchId'],'index':entry['index'],'queueSha256':queue_sha,
                             'requestSha256':queue['requestSha256'],'source':source_ref,'selectedMaterialSha256':fingerprint({'material':material,'params':params})}}
            try:
                submitted=acquire(normalized,root,gateway_factory=gateway_factory,adapter=adapter,selection_seed=seed,_rpa_phase='submit',_operation_check=monitor.check,_defer_report=True)
                entry.update(status='pending',acquisitionStatus='submitted',rpaStatus='submitted',taskId=submitted['taskId'])
                result['rpaSubmissionCount']+=1;save()
            except Exception as exc:
                fail(entry,exc);errors.append((entry['index'],exc));break
        wave['status']='collecting';result['acquisitionPhase']='detail_csv';save()
        # Even if one CSV fails, observe the other already-submitted task IDs.
        for entry in cohort:
            if entry.get('rpaStatus')!='submitted':continue
            monitor.checkpoint('batch_collect_'+str(entry['index']))
            entry['status']='running';save()
            try:
                acquire(normalized,Path(entry['runDir']),resume=True,gateway_factory=gateway_factory,adapter=adapter,_rpa_phase='collect',_operation_check=monitor.check,_defer_report=True)
                entry.update(status='pending',acquisitionStatus='csv_ready',rpaStatus='csv_ready')
            except Exception as exc:
                fail(entry,exc);errors.append((entry['index'],exc))
            save()
        if offset==0:
            gate=result['firstBatchCsvGate'];outcomes={}
            for entry in cohort:
                root=Path(entry['runDir']) if entry.get('runDir') else None
                state=read(root/'status.json') if root and (root/'status.json').exists() else {}
                child_gate=state.get('firstBatchCsvGate',{}).get('status')
                outcomes[entry['materialId']]='valid_csv' if child_gate=='passed' else 'unconfirmed' if entry.get('rpaStatus') in (None,'submitted','unconfirmed') else 'no_valid_csv'
            gate.update(outcomes=outcomes,validCsvCount=sum(v=='valid_csv' for v in outcomes.values()))
            gate['status']='unconfirmed' if 'unconfirmed' in outcomes.values() else 'passed' if gate['validCsvCount'] else 'blocked'
            save()
        # Retain valid earlier A inputs; do not download past the first failed item.
        boundary=min((i for i,_ in errors),default=len(selected)+1)
        wave['status']='media';result['acquisitionPhase']='video';save()
        for entry in cohort:
            if entry['index']>=boundary or entry.get('rpaStatus')!='csv_ready':continue
            monitor.checkpoint('batch_media_'+str(entry['index']))
            entry['status']='running';save()
            try:
                root=Path(entry['runDir'])
                acquire(normalized,root,resume=True,gateway_factory=gateway_factory,adapter=adapter,_rpa_phase='media',_operation_check=monitor.check,_defer_report=True)
                entry.update(status='pending',acquisitionStatus='video_ready')
                result['acquiredCount']+=1;release_completed(root,'batch_acquisition_complete');save()
                monitor.checkpoint('batch_acquisition_complete_'+str(entry['index']))
            except Exception as exc:
                fail(entry,exc);errors.append((entry['index'],exc));break
        wave['status']='failed' if errors else 'completed';save()
        if errors:raise min(errors,key=lambda item:item[0])[1]


def run_batch(request, output, *, gateway_factory=Gateway):
    adapter=__import__('platform_adapter')
    normalized=adapter.validate(request)
    count=adapter.count(normalized)
    output=external_root(output);output.mkdir(parents=True,exist_ok=False)
    selection_root=output/'selection';selection_root.mkdir()
    write(selection_root/'request.json',normalized)
    result={'schemaVersion':1,'packageVersion':read(ROOT/'config/platform.json')['version'],
            'platform':read(ROOT/'config/platform.json')['platform'],'batchId':'batch-'+uuid.uuid4().hex[:12],'runDir':str(output),
            'status':'running','stage':'selection','concurrency':1,'rpaConcurrency':RPA_CONCURRENCY,'mediaConcurrency':1,'cvConcurrency':1,'requestedCount':count,'selectedCount':0,'acquiredCount':0,'completedCount':0,
            'acquisition':'one_selection_rpa_waves_then_serial_B','entries':[]}
    write(output/'batch.json',result)
    gateway=None;monitor=StageMonitor(output,'batch_selection')
    try:
        from cv_probe import probe_cv
        from visual_report import export_batch_html, snapshot_report
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
                          shortageCount=count-len(selected),entries=[{'index':i,'materialId':mid,'status':'pending','acquisitionStatus':'pending','cvStatus':'pending'} for i,mid in enumerate(ids,1)])
            write(output/'batch.json',result)
            release_completed(selection_root,'batch_selection_complete')
            # Close the selection session before any detail work; never reselect per material.
            session=getattr(gateway,'session',None)
            if session is not None and hasattr(session,'close'):session.close()
            gateway=None
            result['stage']='acquisition';write(output/'batch.json',result)
            acquire_waves(normalized,selected,source,source_ref,queue,queue_sha,result,output,gateway_factory,adapter,monitor)
            # B only needs the bound entries; complete material dictionaries stay on disk.
            del selected,queue
            release_item(output,'batch_acquisition_barrier')
            # Phase barrier: no CV is launched until every selected input is ready.
            result['stage']='cv';write(output/'batch.json',result)
            monitor.checkpoint('batch_all_acquisitions_complete')
            for entry in result['entries']:
                root=Path(entry['runDir'])
                entry.update(status='running',cvStatus='running');write(output/'batch.json',result)
                monitor.checkpoint('batch_B_'+str(entry['index']))
                attempt=result['batchId']+'-'+str(entry['index']);entry['attemptId']=attempt
                write(output/'batch.json',result)
                probe_cv(root,attempt_id=attempt,backend='ffmpeg-scene',_defer_report=True)
                receipt=read(root/'cv'/attempt/'receipt.json')
                entry.update(cvStatus=receipt['status'],inputVideoSha256=receipt['inputVideoSha256'],
                             workerExitCode=receipt['workerExitCode'],processCleanup=receipt['processCleanup'])
                if receipt['status']!='succeeded':raise ProbeError(receipt.get('errorCode') or 'batch_cv_failed')
                if receipt['processCleanup']['status']!='completed':raise ProbeError('process_cleanup_unconfirmed')
                entry['status']='succeeded';result['completedCount']+=1
                entry['memoryObservation']=resource_summary(root/'cv'/attempt)
                snapshot=output/'snapshots'/('item-'+str(entry['index']))
                entry.update(snapshot_report(root,snapshot,expected_attempt=attempt))
                write(output/'batch.json',result)
                release_item(root,'batch_item_complete',attempt_id=attempt,report_root=snapshot)
                monitor.checkpoint('batch_item_complete_'+str(entry['index']))
                write(output/'batch.json',result)
            result['status']='succeeded' if result['selectedCount']==count else 'partial'
            write(output/'batch.json',result)
            result['stage']='delivery';write(output/'batch.json',result)
            monitor.checkpoint('batch_html')
            result['report']=export_batch_html(output,output/'index.html')
        monitor.check()
        result['stage']='complete'
    except Exception as exc:
        result.update(status='failed',errorCode=getattr(exc,'code','batch_failed'),message=safe_error(exc))
        guard_stopped=result['errorCode'] in ('operation_memory_guard_aborted','memory_guard_aborted','insufficient_headroom','insufficient_stage_headroom')
        if guard_stopped:
            result.update(guardStopReason=result['errorCode'],guardStopOrigin='phase_admission' if result['errorCode']=='insufficient_stage_headroom' else 'failure_code')
            for entry in result['entries']:
                if entry.get('attemptId') and entry.get('runDir'):
                    guard_path=Path(entry['runDir'])/'cv'/entry['attemptId']/'memory-guard.json'
                    if guard_path.exists():
                        guard=read(guard_path)
                        if guard.get('abort'):result.update(guardStopReason=guard.get('reason') or result['errorCode'],guardStopOrigin='cv_attempt_guard')
        gate=result.get('firstBatchCsvGate',{})
        if gate.get('status')=='pending':gate.update(status='unconfirmed',errorCode=result['errorCode'])
        waves=result.get('rpaWaves',[])
        if waves and waves[-1]['status'] not in ('completed','failed'):waves[-1].update(status='unconfirmed',errorCode=result['errorCode'])
        for entry in result['entries']:
            if entry['status']=='running':
                entry.update(status='failed',errorCode=result['errorCode'])
                if entry['acquisitionStatus']=='running':entry['acquisitionStatus']='failed'
                if entry['cvStatus']=='running':entry['cvStatus']='failed'
        # Preserve completed work; a safe compact report can still describe partial results.
        if monitor.failure is None and not guard_stopped and result['errorCode']!='process_cleanup_unconfirmed':
            try:
                for entry in result['entries']:
                    if entry.get('runDir') and not entry.get('reportSnapshot'):
                        root=Path(entry['runDir']);snapshot=output/'snapshots'/('item-'+str(entry['index']))
                        entry.update(snapshot_report(root,snapshot,entry=entry))
                        release_item(root,'batch_stopped_item',report_root=snapshot)
                write(output/'batch.json',result)
                result['report']=export_batch_html(output,output/'index.html')
            except Exception as report_exc:result['reportErrorCode']=getattr(report_exc,'code','report_failed')
    finally:
        session=getattr(gateway,'session',None)
        if session is not None and hasattr(session,'close'):session.close()
        result['distinctVideoCount']=len({e['inputVideoSha256'] for e in result['entries'] if e.get('inputVideoSha256')})
        result['guardStopReason']=monitor.failure or result.get('guardStopReason')
        if monitor.failure:result['guardStopOrigin']='queue_monitor'
        result['memoryObservation']=resource_summary(output)
        write(output/'status.json',{'status':result['status'],'stage':result['stage'],'errorCode':result.get('errorCode')})
        write(output/'batch.json',result)
    return result
