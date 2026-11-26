"""User-initiated native operations with exact ownership and durable receipts."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import threading
import time

from .store import ACTIVE,UNKNOWN,canonical,uid

CODEX={
 'history':('thread/turns/list',{'limit':30,'sortDirection':'desc','itemsView':'full'}),
 'items':('thread/items/list',{'limit':50,'sortDirection':'desc'}),
 'fork':('thread/fork',{'excludeTurns':True}),
 'revert':('thread/revert',{}),
 'steer':('turn/steer',{}),
 'settings':('thread/settings/update',{}),
 'goal.get':('thread/goal/get',{}),'goal.set':('thread/goal/set',{}),'goal.clear':('thread/goal/clear',{}),
 'skills.list':('skills/list',{}),'skills.configure':('skills/config/write',{}),
 'hooks.list':('hooks/list',{}),
 'plugins.list':('plugin/installed',{}),'plugins.search':('plugin/search',{}),
 'plugins.install':('plugin/install',{}),'plugins.remove':('plugin/uninstall',{}),
 'apps.list':('app/list',{}),'apps.installed':('app/installed',{'forceRefresh':False}),'mcp.list':('mcpServerStatus/list',{'detail':'full'}),
 'mcp.login':('mcpServer/oauth/login',{}),'mcp.reload':('config/mcpServer/reload',{}),
 'config.read':('config/read',{'includeLayers':True}),'config.write':('config/value/write',{}),
 'background.list':('thread/backgroundTerminals/list',{}),'background.stop':('thread/backgroundTerminals/terminate',{}),
 'review':('review/start',{'delivery':'inline'}),
}
QWEN={'commands':('supportedCommands',{}),'mcp.list':('mcpServerStatus',{}),
      'models':('getAvailableModels',{}),'context':('getContextUsage',{}),'usage':('getUsageInfo',{}),
      'settings.model':('setModel',{}),'settings.effort':('setEffortStatus',{}),
      'permissions':('setPermissionMode',{}),'steer':('streamInput',{}),'interrupt':('interrupt',{})}
QWEN.update({'plugins.list':('cli.extensions.list',{}),'plugins.install':('cli.extensions.install',{}),
             'plugins.workspace':('cli.extensions.workspace',{}),
             'plugins.remove':('cli.extensions.uninstall',{}),'plugins.enable':('cli.extensions.enable',{}),
             'plugins.disable':('cli.extensions.disable',{}),'mcp.add':('cli.mcp.add',{}),
             'mcp.remove':('cli.mcp.remove',{}),'mcp.reconnect':('cli.mcp.reconnect',{}),
             'mcp.approve':('cli.mcp.approve',{}),'mcp.reject':('cli.mcp.reject',{})})
QWEN['history.migrate']=('adoptSettledQuery',{})
QWEN_DAEMON={key:value for key,value in QWEN.items() if value[0].startswith('cli.')}
QWEN_DAEMON.update({
    'history':('getSessionTurnIndexPage',{}), 'fork':('branchSession',{}),
    'rewind.snapshots':('getRewindSnapshots',{}), 'revert':('rewindSession',{}),
    'commands':('sessionSupportedCommands',{}), 'models':('workspaceProviders',{}),
    'mcp.list':('workspaceMcp',{}), 'context':('sessionContextUsage',{}), 'usage':('sessionStats',{}),
    'settings.model':('setSessionModel',{}), 'settings.effort':('setSessionConfigOption',{}),
    'permissions':('setSessionApprovalMode',{}), 'interrupt':('interrupt',{}),
    'background.list':('sessionTasks',{}), 'background.stop':('sessionTaskCancel',{}),
    'background.history':('stored.daemon.background',{}),
    'history.verify':('verifyAttempt',{}),
    'agents.list':('sessionAgents',{}),
})
QWEN_DAEMON_LIVE={'commands','context','usage','settings.model','settings.effort','permissions',
                  'interrupt','background.list','background.stop','agents.list'}
DEEPSEEK={'directory':('session/working-directory/get',{}),'directory.set':('session/working-directory/set',{}),
 'permissions.get':('permissions.get',{}),'permissions':('permissions.set',{}),
 'agents.list':('agents.list',{}),'agents.message':('agents.message',{}),
 'interrupt':('agent.interrupt',{}),'commands':('commands.list',{}),
 'command':('command.execute',{}),'steer':('session/prompt',{}),
 'skills.list':('skills.list',{}),'background.list':('jobs.list',{}),
 'workflows.history':('stored.workflow.history',{}),
 'background.output':('jobs.output',{}),'background.stop':('jobs.stop',{}),
 'plugins.list':('plugins.list',{}),'plugins.bundles':('plugins.bundles',{}),
 'plugins.enable':('plugins.enable',{}),'plugins.disable':('plugins.disable',{}),
 'plugins.bundle.enable':('plugins.bundle.enable',{}),'plugins.bundle.disable':('plugins.bundle.disable',{}),
 'plugins.install':('plugins.install',{}),'plugins.remove':('plugins.remove',{})}
CATALOG={'codex':CODEX,'qwen':QWEN,'deepseek':DEEPSEEK}
LIVE_ONLY={'codex':{'steer','review','goal.set','background.list','background.stop'},'qwen':{'steer','interrupt','settings.model','settings.effort','permissions'},
           'deepseek':{'agents.message','interrupt','steer','skills.list','background.list','background.output','background.stop'}}
UNAVAILABLE={'codex':{
    'plugins.search':'当前安装只在实验协议提供插件搜索。请使用工作端的官方插件管理入口。',
    'plugins.install':'官方将插件安装接口标为开发中，暂不向生产客户端开放。请使用工作端的官方插件管理入口。',
    'plugins.remove':'官方将插件卸载接口标为开发中，暂不向生产客户端开放。请使用工作端的官方插件管理入口。',
}}
DEEPSEEK_PROFILE_MUTATIONS={'plugins.enable','plugins.disable','plugins.bundle.enable','plugins.bundle.disable','plugins.install','plugins.remove'}


def operations_for(provider, interface=None):
    if provider=='qwen' and interface=='daemon':return QWEN_DAEMON,QWEN_DAEMON_LIVE
    return CATALOG[provider],LIVE_ONLY[provider]


def catalog(provider, interface=None):
    operations,live_only=operations_for(provider,interface)
    return [{'id':key,'method':method,'defaults':deepcopy(defaults),'requires_live':key in live_only,
             'supported':key not in UNAVAILABLE.get(provider,{}),
             **({'reason':UNAVAILABLE[provider][key]} if key in UNAVAILABLE.get(provider,{}) else {}),
             **({'effect_scope':'NEXT_TURN'} if provider=='codex' and key=='settings' else {})}
            for key,(method,defaults) in operations.items()]


class NativeControls:
    def __init__(self,service):
        self.s=service;self.jobs={};self.transport=None
        with self.s.store.write() as db:
            for op in self.s.collaboration.all(db,'native_control'):
                if op['state'] in {'QUEUED','SEND_INTENT'}:
                    op.update(state='UNKNOWN_AFTER_RESTART',error='服务重启；未自动重发原生操作。')
                    self.s.collaboration.put(db,'native_control',op)

    def rows(self,db,task_id):
        return sorted([op for op in self.s.collaboration.all(db,'native_control') if op['task_id']==task_id],key=lambda op:op['created'],reverse=True)[:60]

    def access(self,db,p):
        from .native_permissions import choice
        access=choice(p.get('access'));task=self.s.store.task(p.get('task_id'),db);provider=task['spec']['provider']
        if provider=='codex':
            a=self.s.store.attempt(p.get('attempt_id'),db)
            from .native_permissions import codex_policy
            params=codex_policy(access)
            operation='settings'
        else:
            from .native_permissions import qwen_mode,deepseek_preset
            params={'mode':qwen_mode(access) if provider=='qwen' else deepseek_preset(access)};operation='permissions'
        receipt=self.request(db,{**p,'operation':operation,'params':params})
        op=self.s.collaboration.get(db,'native_control',receipt['operation_id']);op['access_selection']=access
        op['effect_scope']='NEXT_TURN' if provider=='codex' else 'CURRENT_SESSION'
        self.s.collaboration.put(db,'native_control',op);return receipt

    def request(self,db,p):
        task=self.s.store.task(p.get('task_id'),db)
        if task['spec'].get('execution')=='partner_native':
            raise ValueError('请在当前伙伴运行面板处理审批、补充与停止；此高级原生操作尚未接到该准确连接。')
        if task['spec'].get('runtime_mode')!='official':raise ValueError('此入口只用于官方独立Work。')
        self.s.work_records.guard(db,'task',task['id'])
        workspace_selection=(p.get('params') or {}).get('_workspace_selection') if isinstance(p.get('params'),dict) else None
        self.s.operations.guard_task(db,task['id'],allow_driver=p.get('operation')=='plugins.workspace' or bool(workspace_selection))
        row=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        a=self.s.store.attempt_row(row)
        if not a or a['id']!=p.get('attempt_id') or a['owner']!=p.get('owner'):raise ValueError('运行身份已变化，未操作。')
        provider=task['spec']['provider'];interface=task['spec'].get('qwen_runtime');name=p.get('operation')
        history_method=('stored.workflow.history' if provider=='deepseek' and name=='workflows.history' else
                        'stored.daemon.background' if provider=='qwen' and interface=='daemon' and name=='background.history' else None)
        if history_method:
            if p.get('params'):raise ValueError('工作流历史按本地保存的会话身份查询。')
            identity='native-'+uid(); now=time.time()
            if provider=='deepseek':
                from .deepseek_workflow_history import history
                result=history(self.s.store,db,task,a)
            else:
                from .qwen_daemon_history import recorded_background
                result=recorded_background(db,a)
            op={'id':identity,'task_id':task['id'],'attempt_id':a['id'],'owner':a['owner'],'session_id':a['session_id'],
                'provider':provider,'operation':name,'method':history_method,
                'state':'COMPLETED','live':False,'created':now,'ended_at':now,'result':result}
            self.s.collaboration.put(db,'native_control',op)
            return {'operation_id':identity,'state':'COMPLETED','no_dispatch':True}
        from .execution_constraints import PENDING_OPS,remote_state
        daemon_verify=provider=='qwen' and interface=='daemon' and name=='history.verify'
        daemon_migrate=provider=='qwen' and interface in {None,'query'} and name=='history.migrate'
        if daemon_verify:
            if a['state'] in ACTIVE or not a['turn_id'] or p.get('params'):
                raise ValueError('请在本地回合结束后核对它已保存的原生标识。')
        else:
            if any(o['state'] in PENDING_OPS for o in self.rows(db,task['id'])):raise ValueError('上一项原生操作仍在途或回执未知，请先核对。')
            if remote_state(task,a)=='UNKNOWN':raise ValueError('旧远端仍未知，请先只读核对，不继续原生操作。')
            if a['state'] in UNKNOWN or a['stop_requested'] and a['state'] in ACTIVE:raise ValueError('先核对当前执行状态。')
        operations,live_only=operations_for(provider,interface)
        if name not in operations:raise ValueError('此会话的官方接口未登记该操作。')
        if name in UNAVAILABLE.get(provider,{}):raise ValueError(UNAVAILABLE[provider][name])
        live=a['state'] in ACTIVE and name!='mcp.login'
        if live and provider=='deepseek' and name in DEEPSEEK_PROFILE_MUTATIONS:
            raise ValueError('原生 profile 插件配置在回合结束后修改；实际生效或需要重启以官方回执为准。')
        if live and (name=='revert' or interface=='daemon' and name=='fork'):raise ValueError('变更会话历史前请先结束当前回合。')
        if live and operations[name][0].startswith('cli.'):raise ValueError('原生配置管理在回合结束后执行；更改在下一次工作端启动时加载。')
        if name in live_only and not live:raise ValueError('该官方接口需要正在运行的原生会话。')
        if not live and not daemon_verify and a['state'] not in ACTIVE and not (a['result'] or {}).get('known_tools_exited'):raise ValueError('前一个运行时的退出尚未核对。')
        if daemon_migrate:
            params=p.get('params')
            from .execution_constraints import blockers
            if (live or task['spec'].get('work_mode')=='cooperative' or not a.get('session_id')
                    or not isinstance(params,dict) or set(params)!={'confirm'} or params['confirm'] is not True
                    or a['state'] not in {'COMPLETED','INTERRUPTED','FAILED'}
                    or remote_state(task,a) not in {'completed','interrupted','failed'}
                    or blockers(self.s,db,task,a) or not self.s.independent.qwen_daemon_present()):
                raise ValueError('请先结束 Query 回合并核对退出和终态，再确认切换已保存的独立会话。')
            project=db.execute('SELECT root FROM projects WHERE id=?',(task['project_id'],)).fetchone()
            if not project or Path(project['root']).resolve()!=Path(a['workspace']).resolve():
                raise ValueError('原会话的项目目录已变化，无法切换官方执行接口。')
        params=p.get('params') or {}
        if not isinstance(params,dict):raise ValueError('原生参数须为JSON对象。')
        if provider=='deepseek' and name=='plugins.install':
            from urllib.parse import urlsplit
            source=params.get('source')
            if not isinstance(source,str) or not source.strip() or any(c in source for c in '\0\r\n'):raise ValueError('请提供有效的插件来源。')
            url=urlsplit(source)
            if '://' in source and (url.username or url.password or url.query):raise ValueError('插件来源不能包含登录凭据或查询参数。')
        # A UI operation cannot silently target an unrelated native session.
        for key in ('threadId','sessionId','session_id','expectedTurnId'):
            if key in params:raise ValueError('运行身份由服务绑定，不能手动改写。')
        method,defaults=operations[name];params={**deepcopy(defaults),**params}
        if daemon_verify:params['turnId']=a['turn_id']
        if name=='apps.installed' and provider=='codex':
            if set(params)!={'forceRefresh'} or params['forceRefresh'] is not False:raise ValueError('只读取已安装 Apps 快照。')
            params['threadId']=a['session_id']
        if name=='steer':
            text=params.pop('text',None)
            if not isinstance(text,str) or not text.strip():raise ValueError('补充指令不能为空。')
            if provider=='codex':params.update(input=[{'type':'text','text':text}],expectedTurnId=a['turn_id'])
            elif provider=='deepseek':params['contentBlocks']=[{'type':'text','text':text}]
            else:params['text']=text
        if provider=='codex' and (method.startswith(('thread/','turn/','review/')) or name=='hooks.list'):params['threadId']=a['session_id']
        if provider=='codex' and name in {'skills.list','plugins.list'}:params.setdefault('cwds',[a['workspace']])
        if provider=='deepseek' and method.startswith('session/'):params['sessionId']=a['session_id']
        connection=self.s.resources.task_connection(task,db)
        if not live:self.s.resources.check_connection(connection,db)
        identity='native-'+uid()
        op={'id':identity,'task_id':task['id'],'attempt_id':a['id'],'owner':a['owner'],'session_id':a['session_id'],
            'provider':provider,'operation':name,'method':method,'params':params,'state':'QUEUED','live':live,
            'created':time.time(),'credential_revision':self.s.resources.context(connection).credential_revision,
            'execution_connection':connection}
        if interface=='daemon':op['interface']='Daemon'
        if daemon_migrate:op.update(interface='Daemon',source_spec_hash=task['spec_hash'])
        if interface=='daemon' and name=='fork':op['fork_origin_id']=uid()
        if provider=='codex' and name=='settings':op['effect_scope']='NEXT_TURN'
        self.s.collaboration.put(db,'native_control',op)
        self.s.store.event(task['id'],a['id'],'native_control_requested',{'operation_id':identity,'operation':name,'method':method,'params':params,'source':'HUMAN_UI'},db)
        return {'operation_id':identity,'state':'QUEUED',**({} if live else {'launch_native_control':identity})}

    def claim(self,attempt,plugin=False):
        with self.s.lock,self.s.store.write() as db:
            current=self.s.store.attempt(attempt['id'],db)
            if current['owner']!=attempt['owner'] or current['stop_requested'] or current['state'] not in ACTIVE:return []
            result=[]
            for op in reversed(self.rows(db,attempt['task_id'])):
                if op['attempt_id']!=attempt['id'] or op['state']!='QUEUED':continue
                if op['provider']=='deepseek' and plugin==op['method'].startswith('session/'):continue
                op.update(state='SEND_INTENT',sent_at=time.time());self.s.collaboration.put(db,'native_control',op);result.append(op)
            return result

    def finish(self,identity,result=None,error=None):
        from .attachments import event_view
        with self.s.store.write() as db:
            op=self.s.collaboration.get(db,'native_control',identity)
            if op['state']!='SEND_INTENT':return
            if not error and op['operation']=='history.migrate':
                task=self.s.store.task(op['task_id'],db); data=result or {}
                latest=db.execute('SELECT id,owner,session_id FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
                project=db.execute('SELECT root FROM projects WHERE id=?',(task['project_id'],)).fetchone()
                attempt=self.s.store.attempt(op['attempt_id'],db)
                if (task['spec_hash']!=op['source_spec_hash'] or not latest or latest['id']!=op['attempt_id']
                        or latest['owner']!=op['owner'] or latest['session_id']!=op['session_id']
                        or not project or Path(project['root']).resolve()!=Path(attempt['workspace']).resolve()
                        or data.get('session_id')!=op['session_id'] or data.get('source_id')!=task['id']
                        or data.get('source_type')!='workdesk' or data.get('source_persisted') is not True
                        or data.get('settled') is not True or data.get('verified_after_restart') is not True):
                    error='DELIVERY_UNKNOWN: 原生切换回执与当前会话归属不符。'
                else:
                    from .store import digest
                    spec={**task['spec'],'qwen_runtime':'daemon',
                          'independent_service':self.s.independent.describe('qwen','daemon')}
                    db.execute('UPDATE tasks SET spec=?,spec_hash=?,updated=? WHERE id=?',(canonical(spec),digest(spec),time.time(),task['id']))
                    self.s.store.event(task['id'],op['attempt_id'],'qwen_query_session_adopted',{
                        'session_id':op['session_id'],'operation_id':identity,'source_spec_hash':op['source_spec_hash'],
                        'from':'Query','to':'Daemon','no_dispatch':True},db)
            if not error and op['operation']=='fork' and op.get('interface')=='Daemon':
                data=result or {}
                child=(data.get('thread') or {}).get('id')
                if (not isinstance(child,str) or not child or child==op['session_id'] or
                        data.get('source_session_id')!=op['session_id'] or data.get('source_type')!='workdesk' or
                        data.get('source_id')!=op['fork_origin_id'] or data.get('verified_after_restart') is not True):
                    error='DELIVERY_UNKNOWN: 原生分叉回执与来源或新会话归属不符。'
            op.update(state='DELIVERY_UNKNOWN' if error and (op.get('stop_requested') or str(error).startswith(('TimeoutError','ConnectionError','BrokenPipeError','DELIVERY_UNKNOWN:'))) else 'FAILED' if error else 'FAILED' if isinstance(result,dict) and result.get('application')=='failed' else 'CANCELLED' if isinstance(result,dict) and result.get('application')=='cancelled' else 'COMPLETED',result=event_view(result) if result is not None else op.get('result'),error=str(error) if error else None,ended_at=time.time())
            if not error and op.get('access_selection'):
                from .store import digest
                task=self.s.store.task(op['task_id'],db);spec=task['spec'];spec['access']=op['access_selection']
                db.execute('UPDATE tasks SET spec=?,spec_hash=?,updated=? WHERE id=?',(canonical(spec),digest(spec),time.time(),task['id']))
                if op['access_selection']=='full' and op.get('effect_scope')!='NEXT_TURN':
                    from .native import APPROVALS,resolve_request
                    for row in db.execute("SELECT * FROM native_requests WHERE attempt_id=? AND state='PENDING'",(op['attempt_id'],)).fetchall():
                        method=row['method'];params=json.loads(row['params'])
                        if method in APPROVALS:response={'decision':'acceptForSession'} if not params.get('availableDecisions') or 'acceptForSession' in params['availableDecisions'] else {'decision':'accept'}
                        elif method=='sdk/tool/requestApproval':response={'decision':'accept'}
                        elif method=='item/permissions/requestApproval':response={'allow':True,'scope':'session'}
                        else:continue
                        current=self.s.store.attempt(op['attempt_id'],db)
                        if current['state'] not in ACTIVE or current['stop_requested']:continue
                        resolve_request(self.s,db,{'task_id':op['task_id'],'attempt_id':op['attempt_id'],'owner':op['owner'],'request_id':row['id'],'response':response,'source':'HUMAN_FULL_ACCESS_SELECTION'})
            if not error and op['operation']=='fork':
                child=(result or {}).get('thread',{}).get('id')
                if not isinstance(child,str) or not child or child==op['session_id']:raise ValueError('原生分叉未返回新的会话身份。')
                from .store import digest
                source=self.s.store.task(op['task_id'],db);spec=deepcopy(source['spec'])
                spec['native_fork']={'session_id':child,'source_task':source['id'],'source_session':op['session_id'],'operation_id':identity}
                target=op.get('fork_origin_id') or uid();now=time.time()
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(target,source['project_id'],source['title']+' · 分叉',canonical(spec),digest(spec),'DRAFT',None,now,now))
                bound=db.execute("SELECT payload FROM events WHERE task_id=? AND kind='codex_session_account_bound' AND json_extract(payload,'$.session_id')=? ORDER BY seq LIMIT 1",(source['id'],op['session_id'])).fetchone()
                if bound:self.s.store.event(target,None,'codex_session_account_bound',{'session_id':child,'fingerprint':json.loads(bound[0])['fingerprint'],'actual_thread_response_validated':True,'source_native_fork':identity},db)
                self.s.store.event(target,None,'native_fork_created',spec['native_fork'],db);op['fork_task_id']=target
            if not error and op['operation']=='revert':
                turns=(result or {}).get('removed_turn_ids',[]) if op.get('interface')=='Daemon' else op.get('revert_turn_ids',[])
                affected=[r['id'] for r in db.execute('SELECT id,turn_id,session_id FROM attempts WHERE task_id=?',(op['task_id'],)) if r['session_id']==op['session_id'] and r['turn_id'] in turns]
                self.s.store.event(op['task_id'],op['attempt_id'],'native_history_reverted',{'operation_id':identity,'session_id':op['session_id'],'before_turn_id':op['params']['beforeTurnId'],'turn_ids':turns,'attempt_ids':affected,'files_reverted':False},db)
            self.s.collaboration.put(db,'native_control',op)
            self.s.store.event(op['task_id'],op['attempt_id'],'native_control_result',{'operation_id':identity,'operation':op['operation'],'state':op['state'],'result':op['result'],'error':op['error']},db)

    def ended(self,attempt_id):
        with self.s.store.write() as db:
            for op in self.s.collaboration.all(db,'native_control'):
                if op['attempt_id']==attempt_id and op.get('live') and op['state'] in {'QUEUED','SEND_INTENT'}:
                    op.update(state='NOT_SENT' if op['state']=='QUEUED' else 'DELIVERY_UNKNOWN',error='原生运行时已退出；不自动重发。')
                    self.s.collaboration.put(db,'native_control',op)

    def launch(self,identity):
        stop=threading.Event()
        thread=threading.Thread(target=self._run,args=(identity,stop),daemon=True)
        self.jobs[identity]={'thread':thread,'stop':stop};thread.start()

    def stop(self,db,p):
        op=self.s.collaboration.get(db,'native_control',p.get('operation_id'))
        if op['task_id']!=p.get('task_id') or op['attempt_id']!=p.get('attempt_id') or op['owner']!=p.get('owner'):raise ValueError('原生操作归属不匹配。')
        if op['state'] not in {'QUEUED','SEND_INTENT'}:raise ValueError('该原生操作已经结束。')
        if op.get('live') and op['state']=='SEND_INTENT':raise ValueError('该操作正在当前回合中执行；如需中断运行，请使用停止工作。')
        op['stop_requested']=True
        if op['state']=='QUEUED':op.update(state='NOT_SENT',error='用户在派发前取消。')
        self.s.collaboration.put(db,'native_control',op)
        return {'operation_id':op['id'],'state':op['state'],'stop_native_control':op['id']}

    def signal_stop(self,identity):
        job=self.jobs.get(identity)
        if job:
            job['stop'].set()
            if job.get('cancel'):
                try:job['cancel']()
                except (OSError,ValueError):pass

    def progress(self,identity,result,phase):
        from .attachments import event_view
        with self.s.store.write() as db:
            op=self.s.collaboration.get(db,'native_control',identity)
            if op['state']!='SEND_INTENT':return
            op.update(result=event_view(result),phase=phase);self.s.collaboration.put(db,'native_control',op)
            self.s.store.event(op['task_id'],op['attempt_id'],'native_control_progress',{'operation_id':identity,'phase':phase,'result':op['result']},db)

    def active(self):return sum(job['thread'].is_alive() for job in self.jobs.values())
    def close(self):
        for job in self.jobs.values():job['stop'].set()
        for job in self.jobs.values():
            try:
                if job.get('cancel'):job['cancel']()
            except (OSError,ValueError):pass
        for job in self.jobs.values():job['thread'].join(timeout=5)

    def _run(self,identity,stop):
        with self.s.store.write() as db:
            op=self.s.collaboration.get(db,'native_control',identity)
            if op['state']!='QUEUED':return
            op['state']='SEND_INTENT';self.s.collaboration.put(db,'native_control',op)
        try:
            task=self.s.store.task(op['task_id']);a=self.s.store.attempt(op['attempt_id'])
            with self.s.store.read() as db:
                connection=self.s.resources.task_connection(task,db)
                if op.get('execution_connection')!=connection:raise ValueError('原生控制的任务连接已变化；未操作。')
                locations=self.s.resources.check_connection(connection,db)
            if op['credential_revision']!=locations.credential_revision:raise ValueError('原连接引用已改变；未执行原生操作。')
            if self.transport:result=self.transport(op,task,a,stop)
            elif op['provider']=='codex':result=self.codex(op,task,a,stop)
            elif op['method'].startswith('cli.'):result=self.cli(op,a,stop)
            else:result=self.sdk(op,task,a,stop)
            self.finish(identity,result)
        except Exception as exc:self.finish(identity,error=type(exc).__name__+': '+str(exc))

    def cli(self,op,a,stop):
        from .native_cli import qwen
        task=self.s.store.task(op['task_id'])
        locations=self.s.resources.task_context(task)
        cancel=lambda action:self.jobs[op['id']].update(cancel=action)
        selected=op['params'].get('_workspace_selection')
        if not selected:return qwen(locations,op,a,stop,cancel)
        if op['operation'] not in {'plugins.enable','plugins.disable'}:
            raise ValueError('工作区扩展选择仅用于明确的启用或停用。')
        read={'method':'cli.extensions.workspace','params':{}}
        before=qwen(locations,read,a,stop,cancel)
        projection=before['projection']
        extension=next((entry for entry in projection['extensions'] if entry['extensionId']==selected['extension_id']),None)
        if (not extension or extension['name']!=selected['name'] or extension['version']!=selected['version']
                or projection['desiredGeneration']!=selected['generation'] or not projection['trusted']):
            raise ValueError('扩展身份、版本、工作区信任或配置已变化，请重新查询。')
        if stop.is_set():raise ValueError('扩展配置已在发送前取消。')
        mutation={**op,'params':{'name':extension['name']}}
        result=qwen(locations,mutation,a,stop,cancel)
        progress={'source':'QWEN_WORKSPACE_ACTIVATION','selection':selected,'command_result':result}
        self.progress(op['id'],progress,'CLI_RETURNED')
        try:
            after=qwen(locations,read,a,stop,cancel)
            actual=next((entry for entry in after['projection']['extensions'] if entry['extensionId']==selected['extension_id']),None)
            if (not actual or actual['version']!=selected['version'] or
                    actual['workspaceActivation']!=selected['activation']):
                raise ValueError('原生命令已返回，当前扩展配置与所选目标不一致。')
        except Exception as error:
            raise ValueError('原生命令回执已保存；配置后核对未完成：'+str(error)) from error
        return {**progress,'after':after['projection']}

    def codex(self,op,task,a,stop):
        from .native import open_native
        from .adapters import owned,bind_codex_account
        rpc=None;job=None;oauth=[]
        def receive(message):
            if message.get('method')=='mcpServer/oauthLogin/completed':oauth.append(message.get('params') or {})
            if 'id' in message and message.get('method'):
                rpc.send({'id':message['id'],'error':{'code':-32000,'message':'No active turn for this control operation'}})
        try:
            locations=self.s.resources.task_context(task)
            rpc=open_native(locations,Path(a['workspace']),receive,{'configuration':'VENDOR_DEFAULTS'});job=owned(locations,rpc.process)
            self.jobs[op['id']]['cancel']=rpc.close
            if stop.is_set():raise ValueError('原生操作已停止。')
            account=rpc.request('account/read',{'refreshToken':False},timeout=20)
            bind_codex_account(rpc,self.s.store,task,a,account,a)
            if op['operation']!='mcp.login':rpc.request('thread/resume',{'threadId':a['session_id'],'cwd':a['workspace'],'excludeTurns':True},timeout=20)
            if op['operation']=='revert':
                target=op['params'].get('beforeTurnId');turns=[];cursor=None;seen=set()
                if not isinstance(target,str) or not target.strip():raise ValueError('请从原生历史选择回退位置。')
                while True:
                    page=rpc.request('thread/turns/list',{'threadId':a['session_id'],'sortDirection':'desc','itemsView':'full','limit':100,**({'cursor':cursor} if cursor else {})},timeout=20)
                    for turn in page.get('data',[]):
                        turns.append(turn['id'])
                        if turn['id']==target:break
                    if target in turns:break
                    cursor=page.get('nextCursor')
                    if not cursor or cursor in seen:raise ValueError('原生历史已变化，未回退；请重新载入历史。')
                    seen.add(cursor)
                with self.s.store.write() as db:
                    current=self.s.collaboration.get(db,'native_control',op['id']);current['revert_turn_ids']=turns
                    self.s.collaboration.put(db,'native_control',current)
            if stop.is_set():raise ValueError('原生操作已停止。')
            result=rpc.request(op['method'],op['params'],timeout=30)
            if op['operation']=='mcp.login':
                self.progress(op['id'],result,'WAITING_AUTH')
                while not stop.is_set():
                    matching=next((r for r in oauth if r.get('name')==op['params'].get('name') and (not result.get('loginId') or r.get('loginId')==result['loginId'])),None)
                    if matching:
                        if not matching.get('success'):raise ValueError(matching.get('error') or '官方MCP登录未完成。')
                        return {**result,'completion':matching}
                    rpc.pump(.2)
                raise ValueError('原生授权流程已取消；未自动重试。')
            return result
        finally:
            if rpc:rpc.close()
            if job:job.close()

    def sdk(self,op,task,a,stop):
        from .official_work import RuntimeControl
        from types import SimpleNamespace
        secret=self.s.resources.task_context(task).credentials().get(op['provider'])
        if secret is None:raise ValueError('此工作端的凭据未配置。')
        tools=RuntimeControl(self.s,task,a,stop)
        gate=SimpleNamespace(secret=secret,tools=tools,url='',token='')
        if op['provider']=='deepseek' and not op['method'].startswith('session/'):
            from .gateway import Gateway
            from .sdk_native import Bridge
            tools.check_cancel=lambda:(_ for _ in ()).throw(ValueError('操作已停止')) if stop.is_set() else None
            with Gateway(self.s.store,task,a,tools,stop,secret,{}) as gate:
                gate.native_bridge=Bridge(self.s.store,task,a,stop);gate.control_operation=op
                return self._sdk(op,task,a,stop,gate)
        return self._sdk(op,task,a,stop,gate)

    def _sdk(self,op,task,a,stop,gate):
        from .sdk_native import official_configuration
        from .adapters import owned
        runtime_task=task if op['operation']!='history.migrate' else {**task,'spec':{**task['spec'],'qwen_runtime':'daemon'}}
        config,env,argv=official_configuration(self.s.resources.task_context(task),runtime_task,a,gate,a,'')
        config['operation']=op
        proc=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',cwd=a['workspace'],env=env,creationflags=subprocess.CREATE_NO_WINDOW if __import__('os').name=='nt' else 0)
        job=owned(self.s.locations,proc)
        self.jobs[op['id']]['cancel']=proc.kill
        limit=65 if op.get('interface')=='Daemon' and op['operation'] in {'fork','history.migrate'} else 40
        unbounded=op['provider']=='deepseek' and op['operation'] in DEEPSEEK_PROFILE_MUTATIONS
        try:
            import queue
            frames=queue.Queue();stderr=[]
            def output():
                for line in proc.stdout:
                    try:frames.put(json.loads(line))
                    except ValueError:pass
                frames.put(None)
            def errors():
                for line in proc.stderr:stderr.append(line[-500:]);stderr[:]=stderr[-4:]
            threading.Thread(target=output,daemon=True).start();threading.Thread(target=errors,daemon=True).start()
            proc.stdin.write(json.dumps(config)+'\n');proc.stdin.flush()
            answer=None
            while True:
                if stop.is_set():raise ValueError('原生操作已停止。')
                try:message=frames.get(timeout=.2)
                except queue.Empty:
                    if op['provider']=='deepseek' and not op['method'].startswith('session/'):
                        with self.s.store.read() as db:receipt=self.s.collaboration.get(db,'native_control',op['id'])
                        if receipt['state'] in {'COMPLETED','FAILED','CANCELLED'}:return receipt.get('result')
                    if not unbounded and time.time()-op['created']>limit:raise TimeoutError('Native control timed out; not replayed')
                    continue
                if message is None:break
                if message.get('type')=='native_control_result':answer=message['data'];break
                if message.get('type')=='error':raise RuntimeError(str(message.get('data')))
                if not unbounded and time.time()-op['created']>limit:raise TimeoutError('原生控制回执超时；不自动重发。')
            if answer is None:
                if op.get('interface')=='Daemon':raise ConnectionError('Qwen Daemon 操作回执未返回；未自动重发。')
                raise RuntimeError('原生工作端未返回操作回执：'+''.join(stderr)[-400:])
            if answer.get('error'):
                if op.get('interface')=='Daemon' and str(answer['error']).startswith(('TimeoutError','ConnectionError','DELIVERY_UNKNOWN:')):
                    raise ConnectionError(str(answer['error']))
                raise RuntimeError(answer['error'])
            return answer.get('result')
        finally:
            if job:job.close()
            if proc.poll() is None:proc.kill()
            proc.wait(timeout=5)
