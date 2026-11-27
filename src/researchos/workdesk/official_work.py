"""Direct independent conversations; vendor runtimes own policy and execution.

Task/attempt rows are persistence identities, not an extra project contract.
deadline=0 explicitly means no Workdesk deadline in this path.
"""
from pathlib import Path
import time

from . import workspace
from .contracts import bounded_text
from .paths import atomic_json, implementation_matches
from .store import ACTIVE, UNKNOWN, canonical, digest, uid
from . import partner_task


def is_official(task):
    return task['spec'].get('runtime_mode') == 'official'


def send(service, db, payload, *, cooperative=None):
    if not implementation_matches(service.locations, service.implementation):
        raise ValueError('后端程序已更新，请先重载空闲控制服务。')
    # Scheduled/goal operations call this boundary inside their own command.
    # Public callers have already passed resources.before; internal callers
    # must freeze exactly the same connection before reserving an attempt.
    if '_connection_binding' not in payload:payload,_=service.resources.before(db,'independent.send',payload)
    binding=payload['_connection_binding'];partner=partner_task.is_binding(binding)
    allowed={'task_id','attempt_id','owner','project_id','provider','model','effort','tier','goal','work_scene','files','attachments','access','_connection_binding','work_directory_confirmation','plan_only'}
    if set(payload)-allowed:raise ValueError('独立Work直接发送不接收任务范围、预算或工具限制。')
    if type(payload.get('plan_only',False)) is not bool:raise ValueError('计划模式设置无效。')
    note=bounded_text(payload.get('goal'),'消息')
    if cooperative is not None and payload.get('task_id'):
        raise ValueError('合作单元使用独立的准确执行记录。')
    prior=None;partner_resume={}
    if payload.get('task_id'):
        task=service.store.task(payload['task_id'],db)
        if task['spec'].get('work_mode')=='cooperative':raise ValueError('合作单元须通过合作派发明确安排；不能从独立发送接续绕过累计授权。')
        if not is_official(task):raise ValueError('历史任务仍使用原来的执行流程；请新建独立Work。')
        if partner_task.selected(task)!=partner:
            raise ValueError('任务与原伙伴连接不一致，未续跑。')
        service.work_records.guard(db,'task',task['id'])
        attempts=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        prior=service.store.attempt_row(attempts) if attempts else None
        if prior and (prior['id']!=payload.get('attempt_id') or prior['owner']!=payload.get('owner')):
            raise ValueError('运行身份已变化，请刷新后发送。')
        if task['state'] in ACTIVE|UNKNOWN:raise ValueError('当前回合仍在运行或状态待核对。')
        from .execution_constraints import blockers
        if prior and blockers(service,db,task,prior):raise ValueError('旧执行、远端或原生控制回执未知，或退出证据不足；先核对，不重发。')
        if partner and prior:partner_resume=partner_task.resume_reference(task,prior)
        if prior and not partner and not service.independent.describe(task['spec']['provider'])['native_resume']:
            raise ValueError('此工作端的原生会话恢复尚未接通，请新建独立Work；不会悄悄丢弃历史。')
        spec=task['spec'];before=digest(spec)
        if 'plan_only' in payload:
            if payload['plan_only']:spec['plan_only']=True
            else:spec.pop('plan_only',None)
        spec['execution_connection']=payload['_connection_binding']
        for key in ('model','effort','tier'):
            if key in payload:spec[key]=payload[key]
        from .native_permissions import choice,deepseek_preset
        spec['access']=choice(payload.get('access',spec.get('access')))
        if spec['provider']=='deepseek':deepseek_preset(spec['access'])
        spec['model_selection']=partner_task.model_selection(binding) if partner else service.profiles.selection_snapshot(spec['provider'],spec['model'],spec['effort'],spec['tier'])
        if digest(spec)!=before:
            db.execute('UPDATE tasks SET spec=?,spec_hash=?,updated=? WHERE id=?',(canonical(spec),digest(spec),time.time(),task['id']))
            service.store.event(task['id'],None,'native_configuration_selected',{'model':spec['model'],'effort':spec['effort'],'tier':spec['tier'],'access':spec.get('access','inherit'),'effect':'NEXT_NATIVE_TURN','source':'HUMAN_UI'},db)
            task=service.store.task(task['id'],db)
    else:
        project_id=payload.get('project_id')
        if not project_id:
            from .desktop_preferences import scratch_project
            project_id=scratch_project(service,db,payload.get('work_directory_confirmation'))['project_id']
        project=db.execute('SELECT * FROM projects WHERE id=?',(project_id,)).fetchone()
        if not project:raise ValueError('请选择有效工作区。')
        service.work_records.guard_project(db,project_id)
        provider=payload.get('provider','codex')
        if partner and (binding.get('partner_id',binding.get('provider'))!=provider or binding.get('provider')!=provider):
            raise ValueError('伙伴与所选准确连接不一致，未启动。')
        interface=None
        if provider=='qwen' and not partner:
            frozen_route=(cooperative or {}).get('runtime_route') or {}
            interface=({'Query':'query','Daemon':'daemon'}[frozen_route['interface']] if frozen_route
                       else 'query' if cooperative is not None else service.independent.qwen_selection(db)['interface'])
            if interface=='daemon' and not service.independent.qwen_daemon_present():
                raise ValueError('Qwen Daemon SDK 不可用，请检查本机安装。')
        if partner:
            runtime=partner_task.descriptor(binding)
            model=binding.get('model','')
            if payload.get('model') and payload['model']!=model:
                raise ValueError('模型与已冻结伙伴连接不一致，请重新选择连接。')
            effort,tier=(payload.get(k) or 'default' for k in ('effort','tier'))
            selection=partner_task.model_selection(binding)
        else:
            runtime=service.independent.describe(provider,interface)
            defaults=service.profiles.defaults(provider)
            model,effort,tier=(payload.get(k) or defaults[k] for k in ('model','effort','tier'))
            selection=service.profiles.selection_snapshot(provider,model,effort,tier)
        from .workbench import scene
        from .native_permissions import choice
        if provider=='deepseek' and not partner:
            from .native_permissions import deepseek_preset
            deepseek_preset(payload.get('access'))
        spec={'schema':1,'runtime_mode':'official','project_id':project_id,'goal':note,'access':choice(payload.get('access')),
              'provider':provider,'model':model,'effort':effort,'tier':tier,'model_selection':selection,
              'execution':runtime['execution'],'work_mode':'cooperative' if cooperative is not None else 'independent','work_scene':scene(payload.get('work_scene')),
              'independent_service':{**runtime,'configuration':'FROZEN_PARTNER_CONNECTION' if partner else 'VENDOR_DEFAULTS'},
              'role':'executor','acceptance':'','materials':{},'writable':[],'checks':[],'alternatives':[],
              'budget':{},'campaign':None,'native':{'configuration':'FROZEN_PARTNER_CONNECTION' if partner else 'VENDOR_DEFAULTS'},
              'execution_connection':payload['_connection_binding']}
        if partner:spec['partner_connection']=dict(binding)
        if interface:spec['qwen_runtime']=interface
        if payload.get('plan_only'):spec['plan_only']=True
        if cooperative is not None:
            spec['cooperative']=cooperative
            spec['independent_service']['scope']='COOPERATIVE_UNIT'
        identity=uid()
        # Keep selected originals as delivery evidence. The runtime works in
        # project.root, and this manifest is never an access whitelist.
        from .attachments import normalize, freeze
        items=normalize(payload.get('attachments',[]))
        if any(i['route']!='native' for i in items):raise ValueError('独立Work文件由官方工作端处理。')
        names=payload.get('files',[])
        if not isinstance(names,list):raise ValueError('文件列表格式无效。')
        manifest,_=workspace.snapshot(project['root'],names+[i['file'] for i in items],service.locations.data/'tasks'/identity/'input',service.locations.data,binary_names={i['file'] for i in items})
        freeze(items,project['root'],manifest)
        for item in items:
            item['delivery']='NATIVE_IMAGE_INPUT' if item['kind']=='image' and provider!='qwen' and not partner else 'NATIVE_FILE_TOOLS'
        spec.update(materials=manifest,attachments=items)
        now=time.time()
        db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(identity,project_id,note[:100],canonical(spec),digest(spec),'DRAFT',None,now,now))
        task=service.store.task(identity,db)
        service.store.event(identity,None,'cooperative_unit_created' if cooperative is not None else 'independent_conversation_created',{'runtime_mode':'official','configuration':'FROZEN_PARTNER_CONNECTION' if partner else 'VENDOR_DEFAULTS','extra_task_limits':False},db)
    project=db.execute('SELECT * FROM projects WHERE id=?',(task['project_id'],)).fetchone()
    root=Path(project['root'])
    if not root.is_dir():raise ValueError('工作区目录不存在。')
    if prior and (partner or task['spec'].get('qwen_runtime')=='daemon') and root.resolve()!=Path(prior['workspace']).resolve():
        raise ValueError('原会话的项目目录已变化；请在原目录继续或新建工作。')
    identity,owner=uid(),uid();now=time.time()
    fork=task['spec'].get('native_fork') if not prior else None
    session=prior['session_id'] if prior else fork['session_id'] if fork else uid()
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (identity,task['id'],uid(),owner,'RESERVED',now,0,str(root),session,None,0,None,None,canonical({'runtime_mode':'official',**({'partner_resume':partner_resume} if partner_resume else {})}),None,service.instance))
    db.execute("UPDATE tasks SET state='RESERVED',updated=? WHERE id=?",(now,task['id']))
    service.store.event(task['id'],identity,'execution_reserved',{'owner':owner,'runtime_mode':'official','human_note':note if prior or fork else '',**({'partner_resume':partner_resume} if partner_resume else {}),
        'implementation_revision':service.implementation,'resume_mode':'native' if prior else 'native_fork' if fork else None,
        'configuration_snapshot':{**{k:task['spec'][k] for k in ('provider','model','effort','tier')},'access':task['spec'].get('access','inherit'),'configuration':'FROZEN_PARTNER_CONNECTION' if partner else 'VENDOR_DEFAULTS',
                                  **({'qwen_runtime':task['spec'].get('qwen_runtime','query')} if task['spec']['provider']=='qwen' else {})},
        'credential_selection_revision':(binding.get('account_revision') if partner else service.resources.context(task['spec']['execution_connection']).credential_revision),
        'execution_connection':task['spec']['execution_connection'],'extra_task_limits':False},db)
    return {'task_id':task['id'],'spec_hash':task['spec_hash'],'launch_attempt':identity,'resume_mode':'native' if prior else 'native_fork' if fork else None,'note':note if prior or fork else '','state':'RESERVED'}


def recover(service,db,payload):
    """Close an ambiguous local turn explicitly, preserving delivery uncertainty."""
    task,attempt=service._latest(db,payload)
    if not is_official(task) or not attempt or attempt['state'] not in UNKNOWN:raise ValueError('只有官方独立Work的待核对回合可使用此操作。')
    service.work_records.guard(db,'task',task['id'])
    worker=service.workers.get(attempt['id'])
    active=bool(worker and worker['thread'].is_alive())
    from .reconcile import inspect
    report=inspect(service,task,attempt,db,active)
    if not report['known_local_scopes_exited']:raise ValueError('尚未确认本地运行时退出；请先停止或核对。')
    original=attempt['result'] or {}
    result={**original,'state':'STOPPED_RUNTIME','known_tools_exited':True,'owned_job':{'active_processes':0,'source':'READ_ONLY_RECONCILIATION','previous_observation':original.get('owned_job')},
            'remote_model_state':original.get('remote_model_state','UNKNOWN'),
            'manual_close':{'original_state':attempt['state'],'at':time.time(),'remote_delivery_not_reclassified':True,'no_resend':True}}
    db.execute('UPDATE attempts SET result=?,state=?,reconciliation=? WHERE id=?',(canonical(result),'STOPPED_RUNTIME',canonical(report),attempt['id']))
    db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',('STOPPED_RUNTIME',time.time(),task['id']))
    service.store.event(task['id'],attempt['id'],'independent_unknown_turn_closed',{'original_state':attempt['state'],'frozen_result_preserved':True,'no_resend':True,'known_local_scopes_exited':True},db)
    return {'task_id':task['id'],'state':'STOPPED_RUNTIME','no_resend':True,'remote_model_state':result['remote_model_state']}


class RuntimeControl:
    """Lifecycle only; no Workdesk filesystem/command tool implementation."""
    def __init__(self,service,task,attempt,stop):
        import threading
        self.service,self.task,self.attempt,self.stop=service,task,attempt,stop
        self.lock=threading.RLock()
        self.execution={'implementation_revision':service.implementation}
    def check_cancel(self):
        if self.stop.is_set() or self.service.store.attempt(self.attempt['id'])['stop_requested']:raise ValueError('运行已停止。')
    def execute(self,*args,**kwargs):raise ValueError('独立Work只使用官方原生工具。')


def run(service, identity, stop, resume_mode, note):
    attempt=service.store.attempt(identity);task=service.store.task(attempt['task_id'])
    tools=RuntimeControl(service,task,attempt,stop)
    prior=[a for a in service.store.attempts(task['id']) if a['id']!=identity]
    resume=prior[-1] if prior and resume_mode=='native' else task['spec'].get('native_fork') if resume_mode=='native_fork' else None
    entered=False
    try:
        if partner_task.selected(task):
            # The Go owner verifies the frozen connection and exact parent
            # attempt after the reserving transaction has committed.
            result=partner_task.run(service,task,attempt,stop,resume,note)
        else:
            result=_legacy_run(service,task,attempt,tools,stop,resume,note)
    except Exception as exc:
        result={'state':'SEND_UNKNOWN' if entered else 'FAILED_NO_SEND','answer':'','errors':[{'class':type(exc).__name__,'message':str(exc)[:1200]}],
                'known_tools_exited':not entered,'owned_job':{'active_processes':0} if not entered else None}
    finally:
        stop.set();service.native_controls.ended(identity)
    result.update(attempt_id=identity,task_id=task['id'],owner=attempt['owner'],ended_at=time.time(),runtime_mode='official',
                  configuration='FROZEN_PARTNER_CONNECTION' if partner_task.selected(task) else 'VENDOR_DEFAULTS',extra_task_limits=False,implementation_revision=service.implementation)
    from .attachments import summary
    result['attachments']=summary(service.store,task,attempt) if not resume else []
    if result['state']=='COMPLETED' and (not result.get('known_tools_exited') or (result.get('owned_job') or {}).get('active_processes')!=0):result['state']='SEND_UNKNOWN'
    try:atomic_json(service.locations.data/'tasks'/task['id']/'attempts'/identity/'result.json',result)
    except OSError as exc:
        result['state']='SEND_UNKNOWN';result.setdefault('errors',[]).append({'artifact_save_error':str(exc)})
    with service.store.write() as db:
        service.store.update_attempt(identity,result['state'],db=db,result=result)
        db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY','REPLY_INTENT')",(time.time(),identity))
        service.store.event(task['id'],identity,'attempt_result_saved',result,db)


def _legacy_run(service,task,attempt,tools,stop,resume,note):
    entered=False
    try:
        locations=service.resources.verify_run(task)
        from .attachments import prepare
        if not resume:
            prepare(service.locations,service.store,task,attempt,stop,tools.execution)
            attached={i['file'] for i in task['spec'].get('attachments',[])}
            attempt['_selected_files']=[str(Path(attempt['workspace'])/name) for name in task['spec']['materials'] if name not in attached]
        if service.engine:
            entered=True;result=service.engine(service,task,attempt,tools,stop,resume,note)
        elif task['spec']['provider']=='codex':
            from .native import run as native_run
            entered=True
            result=native_run(locations,service.store,task,attempt,stop,resume,note,control_lock=service.lock)
        else:
            from .adapters import sdk_run
            secret=locations.credentials().get(task['spec']['provider'])
            if secret is None:raise ValueError('所选工作端的API凭据未配置。')
            entered=True
            result=sdk_run(locations,service.store,task,attempt,tools,stop,secret,{},resume,note,control_lock=service.lock)
    except Exception as exc:
        result={'state':'SEND_UNKNOWN' if entered else 'FAILED_NO_SEND','answer':'','errors':[{'class':type(exc).__name__,'message':str(exc)[:1200]}],
                'known_tools_exited':not entered,'owned_job':{'active_processes':0} if not entered else None}
    finally:
        stop.set()
    return result
