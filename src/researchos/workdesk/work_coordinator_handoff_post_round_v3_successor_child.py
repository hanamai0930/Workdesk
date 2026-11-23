"""Rebuild the first child facts before granting any parent proof carriers."""
from copy import deepcopy
import json

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_post_round_v3_command_scope import pending, pending_request
from . import work_coordinator_handoff_post_round_v3_successor_contracts as c


def reconstruct(service, db, original, facts, prefix):
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    from .work_coordinator_handoff_post_round_v3_successor_context import values
    from .work_coordinator_handoff_post_round_v3_commands import reconstruct_receipt
    from . import work_coordinator_handoff_post_round_v3_continuation_contracts as cc
    from . import work_coordinator_handoff_post_round_v3_continuation_receipts as receipts
    from . import work_coordinator_handoff_post_round_v3_continuation_proposals as proposals
    from .work_coordinator_handoff_post_round_v3_continuation_context import observation
    from .work_coordinator_handoff_post_round_v3_continuation_replay import history_at,proposal_checks
    from .work_coordinator_handoff_post_round_v3_command_scope import pending_continuation
    owner=WorkCoordinatorHandoffPostRoundV3Runtime(service)
    plan=original['child_plan']; item=plan['item_id']; owner._db=db
    owner._collected=owner._collect(db,plan); owner._root_plan=owner._collected[0]
    owner._auth=original['authorization']; owner._edge_value=original['saved_path']['ancestors'][-1]['edge']
    owner._original=original; owner._prefix=prefix; owner._inherited=[]
    for entry in facts['history']['frontier']:
        run=service.collaboration.get(db,'work_unit_run',entry['run_id'])
        files,current=owner.sources._saved_files(db,run)
        source=owner.sources._historical_evidence(db,run,service.work_artifacts.reference(current))
        frame={'run':run,'task_spec':service.store.task(run['task_id'],db)['spec'],
            'workspace':service.store.attempt(run['attempt_id'],db)['workspace'],
            'evidence':source['raw'],'source_evidence':source['full'],'files':files}
        if owner.old.frontier_entry(frame)!=entry: raise ValueError('V3原前沿不等于真实完整来源。')
        owner._inherited.append(frame)
    rows=owner._related(db,c.KIND,item)
    if len(rows)!=1: raise ValueError('V3须有唯一原首次能力。')
    cap=c.validate_capability(rows[0]); obs=cap['snapshot']['observation']
    opened=owner.checkpoints._event(db,c.OPENED,cap)
    if (obs['plan_snapshot']!=plan or not prefix['boundary_seq']<opened['seq'] or
            not prefix['boundary_time']<=obs['observed_at']<=cap['created'] or prefix['driver']['state']!='RESERVED'):
        raise ValueError('V3首次能力未绑定原已提交责任前缀。')
    basis={**original,'root':facts['history']['root'],'phase':facts['history']['phase'],
        'material_observation':facts['history']['material_observation']}
    expected=values(owner,db,basis,owner._inherited,prefix,obs['selected_unit']['definition'],obs['observed_at'])
    comparable=lambda v:{**v,'usage':{k:x for k,x in v['usage'].items() if k!='elapsed_observed_seconds'}}
    if comparable(expected)!=comparable(obs): raise ValueError('V3首次观察不等于原保存前沿语义重建。')
    owner._successor=cap; owner._opened=opened['seq']
    continuations=owner._related(db,cc.KIND,item)
    owner._same(continuations,[v for _,v in owner._events(db,cc.OPENED,item)],'V3续行开启记录与事件全集不符。')
    if len(continuations)>1: raise ValueError('V3只允许唯一原声明续行能力。')
    continuation=cc.validate_capability(continuations[0]) if continuations else None
    owner._continuation=continuation
    versions=[]
    for row in owner._related(db,'work_driver_revision',item):
        if row['plan_id']!=plan['id']: continue
        driver=owner.old._driver(db,plan,{'id':row['driver_id'],'generation':row['generation'],'revision':row['revision']})
        versions.append((owner._driver_event(db,driver),driver,row))
    versions.sort(key=lambda v:v[0]); base=len(prefix['versions'])
    if [v for _,v,_ in versions[:base]]!=prefix['versions']: raise ValueError('V3原责任前缀变化。')
    runs=[r for r in service.work_units.rows(db,item) if r['plan_id']==plan['id']]
    if not runs and not pending(service,db,cap): raise ValueError('V3首次能力缺少同命令实际预约。')
    if len(runs)>1 and continuation is None: raise ValueError('V3额外运行缺少独立续行能力。')
    child_events=[]; links={}
    for n,(seq,driver,_) in enumerate(versions[base:],base):
        event=db.execute('SELECT * FROM events WHERE seq=?',(seq,)).fetchone()
        run=next((r for r in runs if r['id']==driver.get('last_unit_run')),None)
        if (run is None or event['kind']!='work_driver_execution_linked' or
                event['task_id'] is not None or event['attempt_id'] is not None or run['id'] in links):
            raise ValueError('V3每个责任修订须是某次运行的唯一实际执行绑定。')
        owner._driver_transitions(db,versions[n-1:n+1]); links[run['id']]=driver
        child_events.append({'seq':seq,'kind':event['kind'],'created':event['at'],
            'payload':json.loads(event['payload']),'task_id':None,'attempt_id':None})
    owner._versions=versions; driver=versions[-1][1]
    if runs and (runs[0]['command_id']!=cap['command_id'] or runs[0]['driver_revision']!=obs['driver']['revision'] or
            runs[0]['created']<cap['created'] or runs[0]['input']['unit']!=obs['selected_unit']['definition'] or runs[0].get('proposal') is not None):
        raise ValueError('V3原首次run与原能力/请求不符。')
    root_cap,activated=owner.capability(db,plan); records=owner._records(db,plan)
    frames,revision,_=owner._layer_runs(db,plan,{'id':item},facts['history']['marker']['project_root'],root_cap,
        activated,driver,original['authorization'],[r for _,_,r in versions],runs,records)
    if revision!=driver['item_revision']: raise ValueError('V3全部运行绑定未到达准确业务修订。')
    owner._match_root_records(db,owner._root_plan,frames,records)
    commands=[]; dispatches=[]; task_events={}; first_receipts=[]; later_receipts=[]; carriers=[(c.KIND,cap,opened)]; extra_rows=[]
    child_frames=frames[len(owner._inherited):]
    for index,(run,frame) in enumerate(zip(runs,child_frames)):
        context=run['input'].get('handoff_coordination_context') or run['input']['handoff_unit_context']
        task=service.store.task(run['task_id'],db); attempt=service.store.attempt(run['attempt_id'],db)
        if (db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?',(task['id'],)).fetchone()[0]!=1 or
                task['state']!=attempt['state'] or attempt['deadline']!=0 or attempt['baseline']!={'runtime_mode':'official'} or
                attempt['worker_instance']!=context['driver']['instance'] or not task['created']<=attempt['started']<=run['created']):
            raise ValueError('V3每次原task/attempt/实例/截止不符。')
        raw=db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?',('work_dispatch','dispatch:'+attempt['id'])).fetchone()
        dispatch=json.loads(raw[0]) if raw else None; linked=links.get(run['id'])
        from .work_coordinator_handoff_post_round_v3_continuation_context import request_from_run
        live=pending_request(service,db,run['command_id'],request_from_run(owner,run))
        if dispatch is None:
            if not live or linked is not None or index!=len(runs)-1: raise ValueError('V3缺少准确真实派发绑定。')
        else:
            from .execution_constraints import snapshot
            from .work_coordinator_handoff_post_round_v3_execution_events import dispatch_session
            reserved_session=dispatch_session(service,db,owner.checkpoints,frame,dispatch)
            expected={'id':'dispatch:'+attempt['id'],'item_id':item,'item_revision':run['item_revision']+1,
                'task_id':task['id'],'attempt_id':attempt['id'],'session_id':reserved_session,
                'command_id':run['command_id'],'command_kind':'work-item.unit-send','spec_hash':task['spec_hash'],
                'provider':task['spec']['provider'],'credential_revision':dispatch['credential_revision'],
                'execution_connection':task['spec']['execution_connection'],'execution_resource':task['spec']['execution_resource'],
                'cooperative':run['cooperative'],'constraints':snapshot(task,attempt),'responsibility':'OFFICIAL_RUNTIME',
                'handoff_id':None,'created':dispatch['created']}
            if dispatch!=expected or linked is None or linked['item_revision']!=dispatch['item_revision']:
                raise ValueError('V3每步dispatch/约束/当次责任绑定不符。')
            owner.checkpoints._event(db,'work_dispatch_bound',dispatch,None,task['id'],attempt['id'])
            _business_version(owner,db,run,dispatch,attempt); dispatches.append(dispatch)
        task_events.update(_task_events(owner,db,frame,dispatch))
        re=owner.checkpoints._event(db,'cooperative_unit_reserved',run,None,task['id'],attempt['id'])
        carriers.append(('work_unit_run',run,{**re,'task_id':task['id'],'attempt_id':attempt['id']}))
        if index==0:
            rr,cr,rc=reconstruct_receipt(owner,db,plan,run,linked or driver,dispatch); first_receipts.extend(rr)
        else:
            proof=receipts.reconstruct(owner,db,plan,run,linked or driver,dispatch,continuation)
            rr=proof['rows']; cr=proof['commands']; rc=proof['carriers']; later_receipts.extend(rr)
        commands.extend(cr); carriers.extend(rc)
        for capture in owner.sources._capture_events(db,run,frame['files']):
            event=db.execute('SELECT * FROM events WHERE seq=?',(capture['seq'],)).fetchone()
            task_events[(event['kind'],event['seq'])]=(json.loads(event['payload']),task['id'],attempt['id'])
        for saved in frame['files']['snapshots']:
            artifact=service.collaboration.get(db,'work_unit_artifact',saved['reference']['id'])
            extra_rows.append(('work_unit_artifact',artifact))
        if frame['files']['snapshots']:
            extra_rows.append(('work_unit_artifact_current',{'id':'unit-artifact-current:'+run['id'],
                'artifact_id':frame['files']['snapshots'][-1]['reference']['id']}))
    expected_revisions=[d['item_revision'] for d in links.values()]
    actual_revisions=[v[0] for v in db.execute('SELECT revision FROM work_item_versions WHERE item_id=? AND revision>? ORDER BY revision',
        (item,obs['task']['revision']))]
    if actual_revisions!=expected_revisions: raise ValueError('V3实际业务版本全集脱离逐次执行绑定。')
    if continuation:
        ce=owner.checkpoints._event(db,cc.OPENED,continuation); co=continuation['snapshot']['observation']
        live=pending_continuation(service,db,continuation)
        if not first_receipts or (len(runs)<2 and not live): raise ValueError('V3续行能力缺少完整首次提交或同命令额外run。')
        first_command=db.execute('SELECT * FROM commands WHERE id=?',(cap['command_id'],)).fetchone()
        if first_command is None or not first_command['created']<=co['observed_at']<=continuation['created']:
            raise ValueError('V3续行开启必须晚于原完整首次回执提交。')
        h=history_at(owner,db,original,frames,versions,ce['seq'],co['material_observation'])
        from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
        expected=observation(owner,db,original,h['frames'],prefix,h['driver'],WorkCoordinatorHandoffAdjustmentSources(service)._records_before(db,owner._root_plan,ce['seq']),
            co['selected_unit']['definition'],co['material_observation'],cap,first_receipts[0],co['proposal'],co['request'],co['observed_at'])
        if comparable(expected)!=comparable(co): raise ValueError('V3续行完整开启观察未逐值成立。')
        if len(runs)>1 and (runs[1]['command_id']!=continuation['command_id'] or
                not ce['seq']<owner.sources._reservation_event(db,runs[1]) or continuation['created']>runs[1]['created']):
            raise ValueError('V3续行能力须与首额外预约属于同一准确命令。')
        carriers.append((cc.KIND,continuation,ce))
    checked,pc,pcarriers=proposal_checks(owner,db,original,frames,versions)
    commands.extend(pc); carriers.extend(pcarriers)
    if checked:
        extra_rows.append(('work_proposal_current',{'id':'proposal-current:'+item,'proposal_id':checked[-1]['id']}))
    for kind,event in (('work_coordinator_handoff_delivery','coordinator_handoff_delivered'),
            ('work_coordinator_handoff_completion','coordinator_handoff_completed')):
        for row,seq in records[kind]:
            run=next((r for r in runs if r['id']==row['snapshot']['run_id']),None)
            if run:
                event_row=owner.checkpoints._event(db,event,row,None,run['task_id'],run['attempt_id'])
                carriers.append((kind,row,{**event_row,'task_id':run['task_id'],'attempt_id':run['attempt_id']}))
    return {'capability':cap,'opened':opened,'continuation':continuation,'original':original,'prefix':prefix,
        'versions':versions,'driver':driver,'frames':frames,'events':child_events,'commands':commands,
        'dispatches':dispatches,'task_events':task_events,'receipt_rows':first_receipts,'continuation_receipts':later_receipts,
        'proposal_receipts':[v for k,v,_ in pcarriers if k==proposals.KIND],'proposals':checked,
        'extra_rows':extra_rows,'carriers':carriers,'runs':runs}


def _task_events(owner, db, frame, dispatch):
    """Exact reservation ledger or the already verified complete native exit."""
    service = owner.s; run = frame['run']; task = service.store.task(run['task_id'], db)
    attempt = service.store.attempt(run['attempt_id'], db)
    if attempt['result'] is not None:
        if dispatch is None: raise ValueError('V3已退出运行必须先有真实dispatch。')
        from .work_coordinator_handoff_post_round_v3_execution_events import execution_events
        registry = execution_events(service, db, owner.checkpoints, frame, dispatch)
        # execution_events proves these exact boundaries but returns only its
        # internal runtime ledger. This owner also carries the reconstructed
        # reservation, dispatch and complete result through the parent audit.
        for kind, body in [('cooperative_unit_reserved', run), ('work_dispatch_bound', dispatch),
                ('attempt_result_saved', attempt['result'])]:
            event = owner.checkpoints._event(db, kind, body, None, task['id'], attempt['id'])
            registry[(kind, event['seq'])] = (body, task['id'], attempt['id'])
        return registry
    spec = task['spec']; rows = list(db.execute('SELECT * FROM events WHERE task_id=? OR attempt_id=? ORDER BY seq',
        (task['id'], attempt['id'])))
    reserved = [r for r in rows if r['kind'] == 'execution_reserved']
    if len(reserved) != 1: raise ValueError('V3首预约须有唯一执行保留事实。')
    revision = json.loads(reserved[0]['payload']).get('implementation_revision')
    if not isinstance(revision, str) or not revision: raise ValueError('V3首预约缺少原实现身份。')
    bodies = [('cooperative_unit_created', {'runtime_mode': 'official', 'configuration': 'VENDOR_DEFAULTS', 'extra_task_limits': False}, None),
        ('execution_reserved', {'owner': attempt['owner'], 'runtime_mode': 'official', 'human_note': '',
            'implementation_revision': revision, 'resume_mode': None,
            'configuration_snapshot': {**{k: spec[k] for k in ('provider','model','effort','tier')},
                'access': spec.get('access','inherit'), 'configuration': 'VENDOR_DEFAULTS'},
            'credential_selection_revision': dispatch['credential_revision'] if dispatch else json.loads(reserved[0]['payload'])['credential_selection_revision'],
            'execution_connection': spec['execution_connection'], 'extra_task_limits': False}, attempt['id']),
        ('execution_resource_frozen', spec['execution_resource'], attempt['id']),
        ('cooperative_unit_reserved', run, attempt['id'])]
    if run['input'].get('handoff_delivery_intent'):
        from .work_coordinator_handoff_records import DELIVERY_KIND
        deliveries=[v for v,_ in owner._records(db,owner._root_plan)[DELIVERY_KIND] if v['snapshot']['run_id']==run['id']]
        if len(deliveries)!=1: raise ValueError('V3 B预约缺少已重建的唯一正式交付。')
        bodies.append(('coordinator_handoff_delivered',deliveries[0],attempt['id']))
    if dispatch: bodies.append(('work_dispatch_bound', dispatch, attempt['id']))
    registry = {}; seq = 0
    for kind, body, identity in bodies:
        event = owner.checkpoints._event(db, kind, body, None, task['id'], identity)
        if event['seq'] <= seq: raise ValueError('V3首预约任务事件顺序不符。')
        seq = event['seq']; registry[(kind, seq)] = (body, task['id'], identity)
    if attempt['state'] != 'RESERVED' or any(registry.get((r['kind'], r['seq'])) !=
            (json.loads(r['payload']), r['task_id'], r['attempt_id']) for r in rows):
        raise ValueError('V3首次在途/未知原生观察尚未完整重建；保留原命令，禁止新增或重发。')
    return registry


def _business_version(owner, db, run, dispatch, attempt):
    """Rebuild the sole work-item link and its original frozen record revision."""
    service = owner.s; item = run['item_id']; before_revision = run['item_revision']; after_revision = dispatch['item_revision']
    def version(n):
        row = db.execute('SELECT snapshot FROM work_item_versions WHERE item_id=? AND revision=?', (item,n)).fetchone()
        if row is None: raise ValueError('V3首次绑定缺少真实业务版本。')
        return json.loads(row[0])
    before = version(before_revision); after = version(after_revision)
    event = owner.checkpoints._event(db, 'work_item_linked', {'work_item_id': item, 'revision': after_revision, 'no_dispatch': True})
    cursor=owner.sources._reservation_event(db,run)
    if run['input'].get('handoff_delivery_intent'):
        from .work_coordinator_handoff_records import DELIVERY_KIND
        deliveries=[(v,seq) for v,seq in owner._records(db,owner._root_plan)[DELIVERY_KIND] if v['snapshot']['run_id']==run['id']]
        if len(deliveries)!=1 or not cursor<deliveries[0][1]<event['seq']:
            raise ValueError('V3 B业务记录须冻结本次预约后的准确正式交付游标。')
        cursor=deliveries[0][1]
    source = service.work_records.source(db, 'task', run['task_id'])
    source.update(state='RESERVED', updated=attempt['started'], cursor=cursor)
    display = service.work_records.display(db, 'task', run['task_id'])
    if display: raise ValueError('V3首次执行尚未开放原记录显示修订。')
    record_revision = digest({'kind': 'task', 'source': source, 'display': {}})
    record = {'kind': 'task', 'record_id': run['task_id'], 'source_revision': canonical(record_revision)}
    expected = {**before, 'revision': before_revision + 1, 'updated': after['updated'],
        'record_refs': sorted([*before['record_refs'], record], key=lambda r: (r['kind'],r['record_id']))}
    actual_record = db.execute('SELECT * FROM work_item_records WHERE item_id=? AND kind=? AND record_id=?',
        (item,'task',run['task_id'])).fetchone()
    if (after_revision != before_revision + 1 or after != expected or actual_record is None or
            actual_record['source_revision'] != record['source_revision'] or
            not run['created'] <= actual_record['created'] <= after['updated'] <= dispatch['created']):
        raise ValueError('V3首次业务关联须准确延长原任务版本并保留原记录修订。')
    if not owner.sources._reservation_event(db, run) < event['seq'] < owner.checkpoints._event(
            db, 'work_dispatch_bound', dispatch, None, run['task_id'],run['attempt_id'])['seq']:
        raise ValueError('V3首次业务版本与真实dispatch事件顺序不符。')
