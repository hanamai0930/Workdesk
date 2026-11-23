"""Explicit model changes at a task boundary; no hidden replay or budget reset."""
import time
import json

from . import workspace
from .store import ACTIVE, UNKNOWN, canonical, digest


def inherit_usage(service,db,source,target,usage):
    debt={k:v for k,v in usage.items() if type(v) in (int,float)}
    db.execute('INSERT INTO usage_offsets VALUES(?,?,?)',('task',target,canonical(debt)))
    service.collaboration.put(db,'ownership',{'id':'owner:'+source['id'],'task_id':source['id'],'state':'RETIRED',
                              'replacement':target,'reason':'EXPLICIT_TASK_REVISION','at':time.time()})
    db.execute("UPDATE tasks SET state='HANDED_OFF',updated=? WHERE id=?",(time.time(),source['id']))
    return debt


def change(service,db,payload):
    if set(payload)-{'task_id','spec_hash','model','effort','tier','continuation'}:raise ValueError('模型切换只接受准确模型、思考、速度与接续方式；账号不随之切换。')
    task=service.store.task(payload.get('task_id'),db);spec=task['spec']
    if payload.get('spec_hash')!=task['spec_hash']:raise ValueError('任务版本已变化。')
    from .workbench import lock_started_model
    lock_started_model(service,db,task)
    service.operations.guard_task(db,task['id'])
    if task['state'] in ACTIVE|UNKNOWN or task['state'] in {'CANCELLED','HANDED_OFF'}:raise ValueError('先停止并核对旧任务；在途或未知时不切换模型。')
    if spec['campaign'] and db.execute('SELECT 1 FROM meta WHERE key=?',('campaign_closed:'+spec['campaign'],)).fetchone():raise ValueError('旧验证包已封闭，不能通过切换模型重开。')
    if db.execute('SELECT 1 FROM chain_members WHERE task_id=?',(task['id'],)).fetchone() or service.operations.group(db,task['id']):raise ValueError('当前入口仅用于独立任务；不隐式改写合作分工。')
    selection={k:payload.get(k) for k in ('model','effort','tier')}
    current=service.profiles.selection_snapshot(spec['provider'],**selection)
    from .attachments import validate as validate_attachments,requested
    validate_attachments([dict(i) for i in spec.get('attachments',[])],spec['provider'],spec.get('execution','scoped'),current,spec['budget'])
    if all(selection[k]==spec[k] for k in selection) and (spec.get('model_selection') or {}).get('catalog_revision')==current['catalog_revision']:raise ValueError('配置和目录版本没有变化。')
    latest=service.operations.quiescent(db,task)
    if any(any(i['task_id']==task['id'] for i in p['items']) for p in service.collaboration.all(db,'plan')):
        raise ValueError('方案中的模型/模式须通过方案修订并重新确认，不隐式改写合作任务。')
    usage=service.store.usage(task=task['id'],db=db);budget=spec['budget']
    if usage['attempts']>=budget['attempts']:raise ValueError('原任务尝试额度已用尽，不通过模型切换清零。')
    folder=service.locations.data/'tasks'/task['id']
    if workspace.current_manifest(folder/'input',spec['materials'])!=spec['materials']:raise ValueError('旧准确材料已变化。')
    source=folder/'attempts'/latest['id']/'files' if latest else folder/'input'
    candidate=(latest['result'] or {}).get('manifest') if latest else spec['materials']
    if not isinstance(candidate,dict) or workspace.current_manifest(source,candidate)!=candidate:raise ValueError('冻结候选无法核对。')
    if latest and workspace.current_manifest(latest['workspace'],set(spec['materials'])|set(spec['writable']))!=candidate:raise ValueError('工作副本在收尾后变化，先修订准确材料。')
    continuation=payload.get('continuation','context')
    if continuation not in {'native','context'}:raise ValueError('请选择原会话下次尝试或准确文件的新上下文。')
    if continuation=='native':
        if spec['provider'] not in {'qwen','codex'} or spec.get('execution')!=spec['provider']+'_native' or not latest or not latest['session_id']:
            raise ValueError('当前路径没有已接通的原生会话，须明确使用新上下文。')
        if (latest['result'] or {}).get('native_scope_check',{}).get('violations'):raise ValueError('旧副本存在范围违规；不原地恢复，请先核对文件范围。')
        reserved=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(latest['id'],)).fetchone()
        binding=json.loads(reserved[0]) if reserved else {}
        if not binding.get('configuration_snapshot') or binding.get('implementation_revision')!=service.implementation or binding.get('credential_selection_revision')!=service.locations.credential_revision:
            raise ValueError('旧会话的配置、实现或账号引用不能准确核对，未交给新配置恢复；可明确使用新上下文。')
        revised={**spec,**selection,'model_selection':current}
        fingerprint=digest(revised)
        db.execute("UPDATE tasks SET spec=?,spec_hash=?,grant=NULL,state='PAUSED',updated=? WHERE id=?",
                   (canonical(revised),fingerprint,time.time(),task['id']))
        detail={'previous_spec_hash':task['spec_hash'],'spec_hash':fingerprint,'previous_specification':spec,
                'selection':selection,'model_selection':current,'session_id':latest['session_id'],'previous_attempt_id':latest['id'],
                'effect':'NATIVE_SAME_SESSION_NEXT_EXPLICIT_ATTEMPT','budget_reset':False,'usage_preserved':usage,
                'new_scope_confirmation_required':True,'no_dispatch':True}
        service.store.event(task['id'],None,'native_model_configuration_revised',detail,db)
        return {'task_id':task['id'],'spec_hash':fingerprint,'state':'PAUSED','continuation':'native',
                'effect':detail['effect'],'no_dispatch':True,'budget_reset':False,'requires_new_grant':True}
    baseline=spec.get('integration_base',spec['materials'])
    values={k:spec[k] for k in ('provider','goal','acceptance','role','writable','checks','host_command_permission','budget','campaign','alternatives')}
    values['attachments']=requested(spec)
    values.update({k:spec[k] for k in ('work_mode','controller_checks') if k in spec})
    member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
    if member:values['chain_id']=member[0]
    row=service._create(db,{**values,**selection,'project_id':task['project_id'],'title':task['title'][:145]+' · 模型切换',
                           'execution':spec.get('execution','scoped'),'files':list(candidate)},snapshot_root=source,integration_base=baseline,
                        selection_from={'task_id':task['id'],'mode':'NEW_NATIVE_SESSION_FROM_CURRENT_FILES','history_restored':False})
    newfolder=service.locations.data/'tasks'/row['task_id']
    workspace.copy_manifest(folder/('integration-base' if 'integration_base' in spec else 'input'),newfolder/'integration-base',baseline)
    debt=inherit_usage(service,db,task,row['task_id'],usage)
    detail={'previous_task_id':task['id'],'new_task_id':row['task_id'],'previous_selection':{k:spec[k] for k in selection},
            'selection':selection,'budget_reset':False,'inherited_usage':debt,'new_scope_confirmation_required':True,
            'continuation':'NEW_NATIVE_SESSION_FROM_CURRENT_FILES','no_dispatch':True}
    service.store.event(task['id'],None,'model_selection_replaced',detail,db)
    service.store.event(row['task_id'],None,'model_selection_revised',detail,db)
    return {**row,**detail}
