"""Presentation choices that never dispatch a worker or change an active scope."""
import time
from .contracts import bounded_text

SCENES = {'research', 'documents', 'computer', 'development'}


def scene(value):
    if value is not None and value not in SCENES:
        raise ValueError('请选择科研探索、文档处理、电脑操控或应用开发。')
    return value


def lock_started_model(service, db, task):
    if task['spec'].get('work_mode', 'independent') == 'independent' and db.execute(
            'SELECT 1 FROM attempts WHERE task_id=? LIMIT 1', (task['id'],)).fetchone():
        raise ValueError('独立工作已经开始，不能中途切换工作端、模型或工作方式；请新建工作。')


def configure(service, db, payload):
    allowed={'task_id','spec_hash','work_scene','provider','model','effort','tier'}
    if set(payload)-allowed:raise ValueError('工作配置字段不支持。')
    task=service.store.task(payload.get('task_id'),db)
    if payload.get('spec_hash')!=task['spec_hash']:raise ValueError('任务版本已变化。')
    if db.execute('SELECT 1 FROM attempts WHERE task_id=? LIMIT 1',(task['id'],)).fetchone():
        raise ValueError('工作已经开始，不能更换工作场景或模型；请新建工作。')
    if task['state'] not in {'DRAFT','APPROVED','PAUSED'}:raise ValueError('此记录不能调整工作配置。')
    service.operations.guard_task(db,task['id'])
    spec=task['spec']
    if spec.get('work_mode','independent')!='independent':raise ValueError('合作配置通过协作方案管理。')
    provider=payload.get('provider',spec['provider'])
    defaults=service.profiles.defaults(provider)
    revised={k:payload.get(k,defaults[k] if provider!=spec['provider'] else spec[k]) for k in ('model','effort','tier')}
    changes={'provider':provider,**revised,'title':task['title']}
    if spec.get('execution','scoped')!='scoped':changes['execution']=provider+'_native'
    if 'work_scene' in payload:changes['work_scene']=scene(payload['work_scene'])
    # Reuse full draft validation and native/attachment configuration instead of
    # mutating a frozen specification in place. No attempts exist on this source.
    result=service._revise(db,{'task_id':task['id'],'changes':changes})
    db.execute("UPDATE tasks SET state='HANDED_OFF',grant=NULL,updated=? WHERE id=?",(time.time(),task['id']))
    record(service,db,{'id':task['id'],'kind':'task','archived':True})
    service.store.event(task['id'],None,'work_configuration_changed',
                        {'replacement':result['task_id'],'previous_spec_hash':task['spec_hash'],'no_dispatch':True},db)
    return {'task':service.store.task(result['task_id'],db),'no_dispatch':True,'requires_new_grant':True}


def record(service, db, payload):
    if set(payload) - {'id', 'kind', 'title', 'pinned', 'archived'}:
        raise ValueError('记录显示设置字段不支持。')
    identity = payload.get('id'); kind = payload.get('kind')
    if isinstance(identity,str) and kind in {'task','dialogue'}:service.work_records.guard(db,kind,identity)
    if kind == 'task':
        service.store.task(identity, db)
    elif kind == 'dialogue':
        service.collaboration.get(db, 'dialogue', identity)
    else:
        raise ValueError('记录类型不支持。')
    key = 'display:' + kind + ':' + identity
    old = next((r for r in service.collaboration.all(db, 'display') if r['id'] == key), {})
    value = {**old, 'id': key, 'kind': kind, 'record_id': identity, 'updated': time.time(),'revision':old.get('revision',0)+1}
    if 'title' in payload:
        value['title'] = bounded_text(payload['title'], '记录名称', 160)
    for field in ('pinned', 'archived'):
        if field in payload:
            if type(payload[field]) is not bool: raise ValueError('显示设置须为布尔值。')
            value[field] = payload[field]
    service.collaboration.put(db, 'display', value)
    return {'record': value, 'no_dispatch': True}


def scratch(service, db, payload):
    if payload: raise ValueError('临时工作区不接受外部路径。')
    from .desktop_preferences import scratch_project
    return scratch_project(service, db)
