"""Draft, authorize and revise exact task specifications.

Internal command component; receives the same service/collaboration owner.
Public API, persisted records and grants are unchanged.
"""
import os
from pathlib import Path
import time
from . import workspace
from .profiles import DEFAULTS
from .store import ACTIVE, UNKNOWN, canonical, digest, uid
from .contracts import CAMPAIGN, NATIVE_CAMPAIGN, ROLES, bounded_text, integer


class TaskDraftCommands:
    def _project(self,db,payload):
        from .workbench import scene
        if 'scene' in payload:scene(payload['scene'])
        root=Path(bounded_text(payload.get('root'),'项目目录',1024))
        if not root.is_absolute():raise ValueError('项目目录须为绝对路径。')
        root=root.absolute()
        if payload.get('create_directory') is True:
            if root.exists():raise ValueError('目录已存在，请使用“添加已有工作区”。')
            root.mkdir()  # Explicit create only; never create a whole inferred parent tree.
        workspace.plain(root,root)
        if not root.is_dir():raise ValueError('项目目录不存在。')
        prior=db.execute('SELECT id FROM projects WHERE root=?',(str(root),)).fetchone()
        if prior:
            changes={'removed':False}
            if 'scene' in payload:changes['scene']=payload['scene']
            self.work_records.save_project_meta(db,prior['id'],changes)
            return {'project_id':prior['id']}
        identity=uid();name=bounded_text(payload.get('name') or root.name,'项目名',100)
        db.execute('INSERT INTO projects VALUES(?,?,?,?)',(identity,name,str(root),time.time()))
        self.work_records.save_project_meta(db,identity,{'scene':payload.get('scene')})
        return {'project_id':identity}

    def _create(self,db,payload,snapshot_root=None,integration_base=None,selection_from=None):
        project=db.execute('SELECT * FROM projects WHERE id=?',(payload.get('project_id'),)).fetchone()
        if project is None:raise ValueError('请先选择有效项目。')
        self.work_records.guard_project(db,project['id'])
        provider=payload.get('provider','codex');default=DEFAULTS.get(provider)
        if default is None:raise ValueError('未知工作端。')
        selected=self.profiles.defaults(provider)
        model=payload.get('model') or selected['model'];effort=payload.get('effort') or selected['effort'];tier=payload.get('tier') or selected['tier']
        model_selection=self.profiles.selection_snapshot(provider,model,effort,tier)
        budget=payload.get('budget',{})
        budget={'attempts':integer(budget.get('attempts',3),1,10,'尝试次数'),
                'seconds':integer(budget.get('seconds',180),20,600,'单次秒数'),
                'tools':integer(budget.get('tools',20),0,60,'工具次数'),
                'requests':integer(budget.get('requests',8),0,30,'模型请求数'),
                'cny':budget.get('cny',1.5)}
        campaign_default=None if payload.get('execution','scoped')!='scoped' else CAMPAIGN
        cny_ceiling=1000 if payload.get('campaign',campaign_default) is None else 20
        if type(budget['cny']) not in (int,float) or not 0<=budget['cny']<=cny_ceiling:raise ValueError('费用上限不支持；独立任务最多1000元，旧验证范围最多20元，不自动扩额。')
        campaign=payload.get('campaign',campaign_default)
        if campaign not in {CAMPAIGN,NATIVE_CAMPAIGN,None}:raise ValueError('未知实施预算范围。')
        execution=payload.get('execution','scoped')
        if execution not in {'scoped','codex_native','qwen_native','deepseek_native'} or execution!='scoped' and execution!=provider+'_native':raise ValueError('原生执行路径须与所选工作端一致。')
        work_mode=payload.get('work_mode','cooperative' if payload.get('chain_id') else 'independent')
        if work_mode not in {'independent','cooperative'}:raise ValueError('工作方式须为独立或合作。')
        if work_mode=='cooperative' and not payload.get('chain_id'):raise ValueError('合作任务须绑定准确问题链，先保存方案或显式切换工作方式。')
        if campaign==NATIVE_CAMPAIGN and (provider!='codex' or (model,effort,tier)!=('gpt-6-luna','low','fast') or budget['requests'] or budget['cny']):
            raise ValueError('本原生有限包仅Luna/low/Fast订阅，API请求和费用必须为0。')
        if campaign and budget['seconds']>180:raise ValueError('本次实施验证单次最多180秒。')
        checks=payload.get('checks',[])
        if not isinstance(checks,list) or len(checks)>8:raise ValueError('检查命令清单过大。')
        seen=set();normalized=[]
        for check in checks:
            if not isinstance(check,dict):raise ValueError('检查规格须为对象。')
            identity=bounded_text(check.get('id'),'检查ID',80)
            if identity in seen:raise ValueError('检查ID重复。')
            seen.add(identity);argv=check.get('argv')
            if not isinstance(argv,list) or not 1<=len(argv)<=40 or any(not isinstance(a,str) or len(a)>4000 or '\0' in a for a in argv):
                raise ValueError('检查命令须为准确argv列表，不使用shell字符串。')
            approval=check.get('approval','each')
            if approval not in {'each','task'}:raise ValueError('检查审批方式不支持。')
            normalized.append({'id':identity,'argv':argv,'approval':approval,'seconds':integer(check.get('seconds',30),1,120,'检查秒数')})
        host_permission=payload.get('host_command_permission') is True
        if checks and not host_permission:raise ValueError('检查命令需要明确确认本机用户权限；未提供OS/网络隔离。')
        if execution!='scoped' and not host_permission:raise ValueError('原生运行须明确允许本机命令；原生沙箱策略不等于准确材料的OS读取隔离。')
        writable=payload.get('writable',[])
        if not isinstance(writable,list) or len(writable)>200:raise ValueError('可写路径清单不支持。')
        writable=sorted(set(workspace.relative(p) for p in writable))
        role=payload.get('role','executor')
        if role not in ROLES:raise ValueError('未知任务职责。')
        if role in {'coordinator','reviewer','self_check'} and writable:raise ValueError('讨论/审查/自检职责不能获得写入清单。')
        if role in {'coordinator','reviewer'} and checks:raise ValueError('讨论/独立审查不执行本机命令；检查单独归属。')
        alternatives=payload.get('alternatives',[])
        if not isinstance(alternatives,list) or len(alternatives)>3:raise ValueError('最多指定3条替代路线。')
        alternatives=[{**self.collaboration.config(a),'role':a.get('role',role)} for a in alternatives]
        if execution!='scoped':
            for a in alternatives:a['execution']=a.get('execution',a['provider']+'_native')
        if any(a['role']!=role for a in alternatives):raise ValueError('替代路线保持原职责；更换职责须修订范围。')
        from . import attachments
        attachment_items=attachments.normalize(payload.get('attachments',[]))
        attachments.validate(attachment_items,provider,execution,model_selection,budget,self.locations)
        if set(writable)&{i['file'] for i in attachment_items}:raise ValueError('原始附件仅作只读材料，不能同时授权改写。')
        identity=uid();folder=self.locations.data/'tasks'/identity
        text_names=payload.get('files',[])
        if not isinstance(text_names,list):raise ValueError('选定材料须为路径列表。')
        if any(i['route']=='pdf_visual' for i in attachment_items) and any(isinstance(n,str) and n.split('/')[0].casefold()=='pdf-pages' for n in text_names+writable):
            raise ValueError('pdf-pages为本任务PDF派生页只读目录；不能作为原始材料或写入目标。')
        binary_names={i['file'] for i in attachment_items}
        manifest,excluded=workspace.snapshot(snapshot_root or project['root'],text_names+sorted(binary_names),folder/'input',None if snapshot_root else self.locations.data,binary_names=binary_names)
        attachments.freeze(attachment_items,folder/'input',manifest)
        spec={'schema':1,'project_id':project['id'],'goal':bounded_text(payload.get('goal'),'任务目标'),
              'acceptance':bounded_text(payload.get('acceptance') or '按目标完成，报告实际检查结果及未知。','验收条件'),
              'provider':provider,'model':model,'effort':effort,'tier':tier,'role':role,'model_selection':model_selection,
              'materials':manifest,'excluded':excluded,'writable':writable,'checks':normalized,
              'host_command_permission':host_permission,'budget':budget,'campaign':campaign,
              'os_isolation':'NOT_PROVIDED','check_network_isolation':'UNKNOWN','extra_network_tools':False}
        spec['execution']=execution
        if payload.get('interaction')=='chat':
            if execution!='scoped' or manifest or writable or checks or budget['tools'] or host_permission:
                raise ValueError('文本聊天不能包含文件、工具或本机命令权限。')
            spec['interaction']='chat'
        spec['work_mode']=work_mode
        if work_mode=='independent' and execution!='scoped':
            spec['independent_service']=self.independent.describe(provider)
            if provider=='deepseek':spec['independent_service']['native_resume']=False
        from .workbench import scene
        if payload.get('work_scene') is not None:spec['work_scene']=scene(payload['work_scene'])
        spec['controller_checks']=bool(payload.get('controller_checks',execution!='scoped' and work_mode=='cooperative' and normalized))
        if attachment_items:spec['attachments']=attachment_items
        if execution!='scoped':
            spec['native_python']=str(self.locations.python)
            spec['native']={'approval_policy':'on-request','sandbox':'workspace-write' if writable else 'read-only',
                            'network':False,'web':False,'delegation':False,'extensions':'profile_native_no_new_services',
                            'write_boundary':'TASK_POLICY_ONLY; current Windows probe did not enforce workspace writes',
                            'os_enforcement':'NOT_PROVIDED_ON_CURRENT_WINDOWS_RUNTIME',
                            'read_boundary':'native_platform_defaults; not_material_only_os_isolation'}
            if execution=='deepseek_native' and os.name=='nt':
                spec['native'].update(sandbox='danger-full-access',approval_policy='ask',permission_preset='workdesk-host',
                                      reason='Installed Windows SEA ACL wrapper cannot execute; host commands require individual approval; no OS isolation claimed')
        spec['alternatives']=alternatives
        if integration_base is not None:spec['integration_base']=integration_base
        if selection_from is not None:spec['selection_from']=selection_from
        title=bounded_text(payload.get('title') or spec['goal'][:60],'任务标题',160)
        now=time.time();db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',
                                 (identity,project['id'],title,canonical(spec),digest(spec),'DRAFT',None,now,now))
        if payload.get('chain_id'):self.collaboration.member(db,identity,payload['chain_id'])
        self.store.event(identity,None,'task_draft_saved',{'spec_hash':digest(spec),'materials':manifest,'excluded':excluded},db)
        return {'task_id':identity,'state':'DRAFT','spec_hash':digest(spec),'materials':manifest,'excluded':excluded}

    def _approve(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        self.work_records.guard(db,'task',task['id'])
        if task['state'] in {'CANCELLED','HANDED_OFF'}:raise ValueError('任务已取消或转交；不能重新批准旧执行。')
        if task['spec_hash']!=payload.get('spec_hash'):raise ValueError('任务版本已变化，未批准。')
        prior=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        candidate=None
        if prior:
            old=self.store.attempt_row(prior);result=old['result'] or {};recon=old['reconciliation'] or {}
            worker=self.workers.get(old['id'])
            if old['state'] in ACTIVE or worker and worker['thread'].is_alive():raise ValueError('运行中不能续期授权。')
            if not (recon.get('known_local_scopes_exited') or result.get('known_tools_exited') and (result.get('owned_job') or {}).get('active_processes')==0):
                raise ValueError('旧尝试未核对本地退出，不能续期。')
            candidate=workspace.current_manifest(old['workspace'],set(task['spec']['materials'])|set(task['spec']['writable']))
            saved=result.get('manifest')
            if saved is None:saved=recon.get('observed_manifest')
            if candidate!=saved:raise ValueError('副本已改变，须修订材料并重新授权。')
        elif task['state'] not in {'DRAFT','APPROVED','FAILED_NO_SEND','PAUSED'}:raise ValueError('当前任务状态不能批准。')
        spec=task['spec'];source=self.locations.data/'tasks'/task['id']/'input'
        for name,item in spec['materials'].items():
            if workspace.read_blob(source,name)['sha256']!=item['sha256']:raise ValueError('材料快照已变化，未批准。')
        grant={'actor':'Human control command','spec_hash':task['spec_hash'],'approved_at':time.time(),
               'expires_at':time.time()+integer(payload.get('hours',4),1,24,'授权小时数')*3600,
               'recipient':spec['provider'],'budget':spec['budget'],'materials':spec['materials'],
               'credential_ref':'existing_subscription' if spec['provider']=='codex' else 'designated_local_env',
               'credential_selection_revision':self.locations.credential_revision,
               'source':'本地控制接口显式确认；模型工具接口没有此能力'}
        # Renew the same exact derived scope without discarding its immutable
        # predecessor binding. Starting still rechecks the current plan/source.
        for key in ('source_plan','source_plan_version','source_candidate','derivation_paths'):
            if key in (task['grant'] or {}):grant[key]=task['grant'][key]
        grant.update(self.collaboration.dependency_grant_binding(db,task['id']))
        if candidate is not None:grant['current_candidate_manifest']=candidate
        value=task['state'] if prior else 'APPROVED'
        db.execute('UPDATE tasks SET state=?,grant=?,updated=? WHERE id=?',(value,canonical(grant),time.time(),task['id']))
        self.store.event(task['id'],None,'human_scope_approved',grant,db)
        return {'task_id':task['id'],'state':value,'grant':grant,'budget_reset':False}

    def _revise(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        self.operations.guard_task(db,task['id'])
        if task['state'] in ACTIVE|UNKNOWN:raise ValueError('先核对旧任务，不能覆盖在途材料。')
        spec=task['spec'];replacement={key:spec[key] for key in ('goal','acceptance','provider','model','effort','tier','role','writable','checks','host_command_permission','budget','campaign')}
        if spec['campaign'] and db.execute('SELECT 1 FROM meta WHERE key=?',('campaign_closed:'+spec['campaign'],)).fetchone():
            raise ValueError('旧验证包已封闭；不能通过修订恢复执行授权。')
        replacement['alternatives']=spec.get('alternatives',[])
        replacement['execution']=spec.get('execution','scoped')
        replacement['work_mode']=spec.get('work_mode','independent')
        replacement['controller_checks']=spec.get('controller_checks',False)
        replacement['work_scene']=spec.get('work_scene')
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if member:replacement['chain_id']=member[0]
        from .attachments import requested
        replacement['attachments']=requested(spec)
        replacement.update(project_id=task['project_id'],title=task['title']+' · 修订',files=list(spec['materials']))
        changes=payload.get('changes',{})
        if not isinstance(changes,dict) or set(changes)-set(replacement):raise ValueError('修订字段不支持。')
        if changes.get('campaign',spec['campaign'])!=spec['campaign']:raise ValueError('修订不能改变有限批次归属；独立新范围须另建草稿。')
        if changes.get('chain_id',replacement.get('chain_id'))!=replacement.get('chain_id'):raise ValueError('修订不能更换累计预算链。')
        if changes.get('work_mode',replacement['work_mode'])!=replacement['work_mode']:raise ValueError('工作方式须使用显式切换入口。')
        used=self.store.usage(task=task['id'],db=db)
        has_usage=any(used[k]>0 for k in ('attempts','model_requests','tool_calls','reserved_cny'))
        if has_usage and changes.get('work_scene',spec.get('work_scene'))!=spec.get('work_scene'):
            raise ValueError('工作已经开始，工作场景固定；请新建工作。')
        if has_usage and any(changes.get(k,spec[k])!=spec[k] for k in ('provider','model','effort','tier')):
            raise ValueError('已有执行不在本项工作中更换模型或配置；独立工作请新建任务，合作任务通过方案修订管理。')
        replacement.update(changes)
        if has_usage and replacement['budget']!=spec['budget']:raise ValueError('修订不自动扩额或清零累计预算；新增预算需单独新任务范围。')
        value=self._create(db,replacement,selection_from={'task_id':task['id'],'mode':'EXPLICIT_TASK_REVISION','history_restored':False} if has_usage else spec.get('selection_from'))
        inherited={}
        if has_usage:
            from .selection import inherit_usage
            inherited=inherit_usage(self,db,task,value['task_id'],used)
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if member:self.collaboration.member(db,value['task_id'],member[0])
        self.store.event(value['task_id'],None,'draft_revised_from',{'previous_task_id':task['id'],'requires_new_grant':True,
                         'budget_reset':False,'inherited_usage':inherited},db)
        return {**value,'previous_task_id':task['id'],'budget_reset':False}
