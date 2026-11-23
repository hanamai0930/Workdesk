"""Official SDK native execution, with durable interactions and honest boundaries.

The API gateway enforces recipient/model/request budgets. Native filesystem and
shell tools run as the current Windows user; the working copy is not a sandbox.
"""
import json
import os
from pathlib import Path
import threading
import time

from .paths import clean_env, atomic_json
from .store import ACTIVE, canonical, digest, uid


QWEN_DENIED=['web_fetch','web_search','agent','send_message','report_findings',
             'list_agents','enter_worktree','exit_worktree','get_goal','update_goal']


def is_native(task):return task['spec'].get('execution') in {'qwen_native','deepseek_native'}


def qwen_main_result(message, session_id):
    return (message.get('type') == 'result' and message.get('session_id') == session_id
            and message.get('parent_tool_use_id') is None)


def configuration(locations,task,attempt,gate,resume,note):
    spec=task['spec'];provider=spec['provider']
    if spec.get('runtime_mode')=='official':return official_configuration(locations,task,attempt,gate,resume,note)
    home=locations.data/'tasks'/task['id']/'native-home'
    if provider=='deepseek':home=home/attempt['session_id']
    home.mkdir(parents=True,exist_ok=True)
    revision=gate.tools.execution.get('implementation_revision')
    workers=locations.data/'implementations'/revision/'src/researchos/workdesk/workers' if revision else Path(__file__).with_name('workers')
    # Keep the official system prompt and native compression. This append is
    # task policy, not a replacement harness or a common Workdesk tool subset.
    from .native import prompt_for
    prompt=prompt_for(task,note,native_resume=bool(resume)).replace('native Codex tools','native '+provider+' tools')
    from .attachments import prompt_suffix,deepseek_input
    original_prompt=prompt
    prompt+=prompt_suffix(attempt)
    if gate.tools.execution.get('resume_mode')=='context':
        prompt+='\nNEW SESSION: no old native session history restored. Use only the selected files and current results.'
    remaining=max(0,spec['budget']['tools']-gate.store.usage(task=task['id'])['tool_calls'])
    with gate.store.read() as db:
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if member:
            chain=json.loads(db.execute("SELECT data FROM collaboration WHERE kind='chain' AND id=?",(member[0],)).fetchone()[0])
            remaining=min(remaining,max(0,chain['budget']['tools']-gate.store.usage(chain=member[0],db=db)['tool_calls']))
    config={'native':True,'workspace':attempt['workspace'],'home':str(home),'session_id':attempt['session_id'],
            'model':spec['model'],'effort':spec.get('effort','off'),'url':gate.url,'token':gate.token,'resume':bool(resume),'prompt':prompt,
            'max_tools':remaining,'deadline':attempt['deadline'],'excluded_tools':QWEN_DENIED,
            'sdk':str(locations.runtime/'qwen/node_modules/@qwen-code/sdk')}
    if provider=='deepseek':config['input']=deepseek_input(attempt,original_prompt)
    env=clean_env()
    if provider=='qwen':
        settings={'security':{'auth':{'selectedType':'openai'}},
                  'modelProviders':{'openai':[{'id':spec['model'],'name':'Workdesk official API','baseUrl':gate.url+'/v1','envKey':'OPENAI_API_KEY',
                                               'generationConfig':{'enable_thinking':spec.get('effort','off')!='off',
                                                                   'modalities':{'image':'image' in (spec.get('model_selection',{}).get('entry',{}).get('input_modalities') or []),'pdf':False,'audio':False,'video':False}}}]},
                  'model':{'name':spec['model']},'telemetry':{'enabled':False},'context':{'fileName':[]},'mcpServers':{},
                  'agents':{'crossSessionMessaging':False},'general':{'enableAutoUpdate':False},
                  # Official background extraction forks a memory-writing agent
                  # after the main turn. It is outside this task's recipient/tool
                  # authority; keep the main official loop and compression intact.
                  'memory':{'enableManagedAutoMemory':False,'enableManagedAutoDream':False,'enableAutoSkill':False},
                  'tools':{'disabled':QWEN_DENIED}}
        atomic_json(home/'.qwen/settings.json',settings);atomic_json(home/'system-settings.json',{})
        empty=Path(attempt['workspace'])/'.qwen/.env';empty.parent.mkdir(exist_ok=True)
        empty.write_bytes(b'# No provider credentials in working copy.\n')
        (home/'AppData/Local/Microsoft/Windows/PowerShell').mkdir(parents=True,exist_ok=True)
        (home/'AppData/Roaming').mkdir(parents=True,exist_ok=True)
        env.update(USERPROFILE=str(home),HOMEDRIVE=home.drive,HOMEPATH=str(home)[len(home.drive):],
                   LOCALAPPDATA=str(home/'AppData/Local'),APPDATA=str(home/'AppData/Roaming'),
                   PSModuleAnalysisCachePath=str(home/'AppData/Local/Microsoft/Windows/PowerShell/ModuleAnalysisCache'),
                   QWEN_CODE_SYSTEM_SETTINGS_PATH=str(home/'system-settings.json'),
                   QWEN_CODE_SYSTEM_DEFAULTS_PATH=str(home/'system-settings.json'),
                   OPENAI_API_KEY=gate.token,OPENAI_BASE_URL=gate.url+'/v1',OPENAI_MODEL=spec['model'])
        env['PATH']=str(locations.node.parent)+os.pathsep+str(locations.runtime/'poppler/Library/bin')+os.pathsep+env.get('PATH','')
        argv=[str(locations.node),str(workers/'qwen_native.mjs')]
    else:
        # Only task-unauthorized recipients/delegation/log upload/retry are
        # removed. Native files, shell, todos, Skills and compression remain.
        disabled=['session-log-deepseek','plugin-package-inventory-deepseek','session-telemetry-otel','llm-retry',
                  'web','web-search-deepseek','web-fetch-http','tool-web','agent-instructions',
                  'tool-subagent-control','tool-subagent-list-agents','tool-subagent','tool-subagent-fork',
                  'tool-workflow','workflow-worker-thread','subagent-spawn-in-process','subagent-fork-in-process',
                  'tool-goal','tool-ralph','goal-round-driver']
        patch=home/'native.patch.yml'
        text='\n'.join('- id: '+name+'\n  disabled: true' for name in disabled)+'\n'
        text+='- insert:\n    - id: workdesk-native-control\n      name: '+json.dumps((workers/'deepseek_legacy_control.mjs').as_uri())+'\n'
        text+='- insert:\n    - id: tool-ask-user\n      name: "@deepseek-ai/dsh-tool-ask-user"\n'
        if os.name=='nt':
            # The installed SEA runtime cannot launch its ACL runner as Node.
            # Host commands are explicitly granted in this task, never claimed
            # to be OS isolated. Keep per-action approval in the controller.
            text+='- id: sandbox-policy\n  config:\n    mode: danger-full-access\n- id: approval\n  config:\n    policy: ask\n'
            text+='- id: permission\n  config:\n    defaultPreset: workdesk-host\n    presets:\n      workdesk-host:\n        sandbox: danger-full-access\n        approval: ask\n'
        patch.write_text(text,encoding='utf-8')
        config['patch']=str(patch)
        argv=[str(locations.runtime/'deepseek-legacy-venv/Scripts/python.exe'),'-B','-X','utf8',str(workers/'deepseek_native.py')]
    return config,env,argv


def official_configuration(locations,task,attempt,gate,resume,note):
    """Only select the model/credential and bridge native interaction events.

    Do not generate a replacement settings file, disabled-tool list, prompt
    policy, memory/MCP config, or model-request proxy for independent Work.
    """
    from .model_catalog import ENDPOINTS
    from .attachments import prompt_suffix,deepseek_input
    spec=task['spec'];provider=spec['provider']
    revision=gate.tools.execution.get('implementation_revision')
    workers=locations.data/'implementations'/revision/'src/researchos/workdesk/workers' if revision else Path(__file__).with_name('workers')
    prompt=note if resume else spec['goal']
    from .scene_runtime import prompt as scene_prompt
    prompt=scene_prompt(task,prompt,bool(resume))
    config={'native':True,'official':True,'workspace':attempt['workspace'],'session_id':attempt['session_id'],
            'model':spec['model'],'effort':spec['effort'],'url':ENDPOINTS[provider].removesuffix('/chat/completions'),
            'token':gate.secret.reveal_for_provider(),'resume':bool(resume),'prompt':prompt+prompt_suffix(attempt),
            'control_url':gate.url,'control_token':gate.token,
            'sdk':str(locations.runtime/'qwen/node_modules/@qwen-code/sdk')}
    # Inherit vendor user/project configuration; select credentials without
    # copying them into the project or mutating the user's native settings.
    env=clean_env()
    # The controller's Python import policy must not leak into native shell
    # tools: pip may install to the user site while this flag hides that site.
    # Keep the fixed SDK interpreter; let native child Python use its defaults.
    env.pop('PYTHONNOUSERSITE',None)
    env['PATH']=str(locations.node.parent)+os.pathsep+str(locations.runtime/'poppler/Library/bin')+os.pathsep+env.get('PATH','')
    if provider=='qwen':
        from .independent import qwen_runtime
        from .native_permissions import qwen_mode
        config['permission_mode']='plan' if spec.get('plan_only') else qwen_mode(spec.get('access'))
        from .scene_runtime import qwen as scene_qwen
        scene_qwen(locations,task,config,env)
        env.update(OPENAI_API_KEY=config['token'],OPENAI_BASE_URL=config['url'],OPENAI_MODEL=spec['model'])
        if qwen_runtime(spec)=='daemon':
            from . import workspace
            from .execution_constraints import remote_state
            evidence=resume or {}
            fork=spec.get('native_fork')
            settled=bool((fork and evidence==fork) or (evidence.get('id') and (evidence.get('result') or {}).get('known_tools_exited')
                and ((evidence['result'].get('owned_job') or {}).get('active_processes')==0)
                and remote_state(task,evidence) in {'completed','failed','interrupted'}))
            app_root=locations.user or locations.data
            runtime_dir=workspace.plain(app_root/'providers/qwen/daemon-runtime',app_root,missing=True)
            config.update(attempt_id=attempt['id'], origin_id=task['id'], daemon_runtime_dir=str(runtime_dir),
                          source_fork=fork, settled_resume=settled,
                          connection_fingerprint=digest(spec.get('execution_connection')))
            argv=[str(locations.node),str(workers/'qwen_daemon.mjs')]
        else:
            argv=[str(locations.node),str(workers/'qwen_native.mjs')]
    else:
        home=Path(os.environ.get('DSH_HOME') or Path.home()/'.dsh')
        home.mkdir(parents=True,exist_ok=True)
        patch=locations.data/'tasks'/task['id']/'native-control.patch.yml'
        from .native_permissions import deepseek_preset
        preset=deepseek_preset(spec.get('access'))
        patch_text='- insert:\n    - id: workdesk-native-control\n      name: '+json.dumps((workers/'deepseek_native_control.mjs').as_uri())+'\n'
        if spec.get('plan_only'):
            preset='workdesk-plan'
            patch_text+='- id: sandbox-policy\n  config:\n    mode: read-only\n- id: approval\n  config:\n    policy: never\n'
            patch_text+='- id: permission\n  config:\n    defaultPreset: workdesk-plan\n    presets:\n      workdesk-plan:\n        sandbox: read-only\n        approval: never\n'
        elif preset:
            patch_text+='- id: sandbox-policy\n  config:\n    mode: '+preset+'\n- id: approval\n  config:\n    policy: '+('never' if preset=='danger-full-access' else 'ask')+'\n- id: permission\n  config:\n    defaultPreset: '+preset+'\n'
        from .scene_runtime import deepseek_patch
        patch.write_text(patch_text+deepseek_patch(task,workers),encoding='utf-8')
        route = (spec.get('cooperative') or {}).get('runtime_route') or {}
        config.update(home=str(home),patch=str(patch),input=deepseek_input(attempt,prompt),permission_preset=preset,
                      command_catalog=route.get('provider') == 'deepseek',
                      dsh_bin=str(locations.runtime/'deepseek-cli/Workdesk.DeepSeekRuntime.exe'))
        argv=[str(locations.runtime/'deepseek-venv/Scripts/python.exe'),'-B','-X','utf8',str(workers/'deepseek_native.py')]
    return config,env,argv


def response_for(method,params,answer):
    if method=='sdk/tool/requestUserInput':
        questions=params.get('questions',[]);answers=answer.get('answers')
        if any(q.get('isSecret') for q in questions):raise ValueError('密码须使用原生认证入口。')
        if not isinstance(answers,dict) or set(answers)!={q['id'] for q in questions}:raise ValueError('请回答本次准确问题。')
        result={}
        for key,value in answers.items():
            texts=value.get('answers') if isinstance(value,dict) else None
            if not isinstance(texts,list) or not texts or any(not isinstance(s,str) or not s.strip() for s in texts):raise ValueError('回答须为非空文本列表。')
            result[key]={'answers':texts}
        return {'answers':result}
    if method=='sdk/tool/requestApproval':
        decision=answer.get('decision')
        if decision not in {'accept','decline','cancel'}:raise ValueError('仅支持本次批准、拒绝、取消。')
        return {'decision':decision}
    raise ValueError('未接通此SDK交互，未授权操作。')


def validate_paths(task,attempt,params):
    if task['spec'].get('runtime_mode')=='official':return
    name=params.get('tool_name','');args=params.get('input') or {}
    if not isinstance(args,dict):raise ValueError('原生工具参数须为对象。')
    writes=name in {'write','edit','write_file','edit_file','apply_patch','str_replace_editor','replace','notebook_edit'}
    targets=[args[k] for k in ('file_path','filePath','path','absolute_path','target_file','notebook_path') if isinstance(args.get(k),str)]
    if name in {'pwsh','bash','run_shell_command'} and task['spec'].get('role') in {'reviewer','coordinator'}:raise ValueError('当前只读职责未授权本机命令。')
    root=Path(os.path.abspath(attempt['workspace']))
    def local_path(value):
        path=Path(value);path=path if path.is_absolute() else root/path
        # Reject lexical escape / UNC before filesystem resolution or stat;
        # validating an unapproved network path must not contact that host.
        path=Path(os.path.abspath(path))
        try:path.relative_to(root)
        except ValueError:raise ValueError('原生路径越过当前工作副本。') from None
        from .workspace import plain
        plain(path,root,missing=True)
        return path
    for key in ('directory','workdir','cwd'):
        if not isinstance(args.get(key),str):continue
        local_path(args[key])
    if writes and not targets:raise ValueError('未识别此修改工具的准确路径；未批准。')
    allowed=set(task['spec']['writable'] if writes else task['spec']['materials'])
    allowed|=set(task['spec']['writable'])
    from .pdf_visual import manifest as pdf_manifest
    derived=pdf_manifest(attempt)
    if not writes:allowed|=set(derived)
    for value in targets:
        path=local_path(value);relative=path.relative_to(root).as_posix()
        if writes and relative in derived:raise ValueError('PDF派生页面是只读证据，不能授权改写。')
        prefix='' if relative=='.' else relative+'/'
        directory_search=not writes and path.is_dir() and any(n.startswith(prefix) for n in allowed)
        if relative not in allowed and not directory_search:raise ValueError('原生文件操作不在准确文件范围。')
        if relative in derived:
            from .workspace import read_blob
            if {k:read_blob(root,relative)[k] for k in ('sha256','bytes')}!=derived[relative]:raise ValueError('PDF派生页已改变，不交给原生读取工具。')


class Bridge:
    def __init__(self,store,task,attempt,stop,control_lock=None):
        self.store,self.task,self.attempt,self.stop=store,task,attempt,stop
        self.control_lock=control_lock or threading.RLock();self.pending={};self.overrun=False
        self.initialized=False;self.terminal=False;self.last_error=None
        self.official=task['spec'].get('runtime_mode')=='official'
        self.sessions={attempt['session_id']:None}
        self.qwen_tool_uses = set()

    def register_session(self,data):
        child,parent=data.get('session_id'),data.get('parent_session_id')
        with self.control_lock:
            if (not self.official or not isinstance(child, str) or not child or
                    not isinstance(parent, str) or parent not in self.sessions or child == parent):
                raise ValueError('Unbound native descendant')
            if child in self.sessions:
                if self.sessions[child] != parent:
                    raise ValueError('Conflicting native descendant parent')
                return
            self.sessions[child] = parent
            self.store.event(self.task['id'],self.attempt['id'],'native_child_session',data)

    def register(self,data,transport='stdio'):
        method=data.get('method');rpc_id=data.get('id');params=data.get('params')
        if method not in {'sdk/tool/requestApproval','sdk/tool/requestUserInput'} or not isinstance(params,dict) or not isinstance(rpc_id,str):
            raise ValueError('Unsupported SDK request')
        if params.get('session_id') not in self.sessions:raise ValueError('Unbound SDK request session')
        with self.control_lock,self.store.write() as db:
            current=self.store.attempt(self.attempt['id'],db)
            old=db.execute('SELECT id FROM native_requests WHERE attempt_id=? AND rpc_id=?',(self.attempt['id'],canonical(rpc_id))).fetchone()
            if old:raise ValueError('Repeated native interaction identity; not replayed')
            active=not self.terminal and (self.official or self.task['spec']['budget']['tools']>0) and not self.stop.is_set() and not current['stop_requested'] and current['state'] in ACTIVE and (self.official or time.time()<self.attempt['deadline'])
            state='PENDING' if active else 'REJECTED_TERMINAL' if self.terminal else 'REJECTED_BUDGET' if not self.official and not self.task['spec']['budget']['tools'] else 'REJECTED_STOPPED';identity=uid()
            db.execute('INSERT INTO native_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (identity,self.task['id'],self.attempt['id'],canonical(rpc_id),method,canonical(params),state,None,time.time(),None))
            if active:
                self.store.update_attempt(self.attempt['id'],'WAITING_INPUT' if method.endswith('UserInput') else 'WAITING_APPROVAL',db=db)
                if transport=='stdio':self.pending[identity]=rpc_id
            self.store.event(self.task['id'],self.attempt['id'],'native_request_waiting' if active else 'native_request_rejected',
                             {'request_id':identity,'method':method,'params':params,'silence_is_approval':False},db)
            return identity

    def http_interaction(self,data,send):
        identity=self.register(data,'http')
        while not self.stop.wait(.05):
            with self.control_lock:
                with self.store.write() as db:
                    current=self.store.attempt(self.attempt['id'],db)
                    if self.terminal or current['stop_requested'] or current['state'] not in ACTIVE or (not self.official and time.time()>=self.attempt['deadline']):break
                    row=db.execute('SELECT * FROM native_requests WHERE id=?',(identity,)).fetchone()
                    if row['state']!='READY':
                        if row['state']!='PENDING':break
                        continue
                    db.execute("UPDATE native_requests SET state='REPLY_INTENT' WHERE id=?",(identity,))
                if self.stop.is_set() or (not self.official and time.time()>=self.attempt['deadline']):break
                try:send(json.loads(row['response']))
                except Exception:
                    with self.store.write() as db:db.execute("UPDATE native_requests SET state='REPLY_UNKNOWN' WHERE id=?",(identity,))
                    self.stop.set();raise
                with self.store.write() as db:
                    db.execute("UPDATE native_requests SET state='SENT' WHERE id=? AND state='REPLY_INTENT'",(identity,))
                    self.update_wait_state(db)
                return
        with self.store.write() as db:
            db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE id=? AND state IN ('PENDING','READY','REPLY_INTENT')",(time.time(),identity))
        send({'decision':'cancel','cancelled':True})

    def before_tool(self,data):
        if data.get('session_id') not in self.sessions:raise ValueError('Unbound native tool session')
        self.stop_check()
        identity=data.get('id')
        if not isinstance(identity,str) or not identity:raise ValueError('Missing native tool identity')
        call_id=self.attempt['id']+':sdk-native:'+identity
        rejected=None
        with self.control_lock,self.store.write() as db:
            self.stop_check(db)
            if db.execute('SELECT 1 FROM tool_calls WHERE id=?',(call_id,)).fetchone():raise ValueError('Duplicate native tool dispatch refused')
            count=self.store.usage(task=self.task['id'],db=db)['tool_calls']
            if not self.official and count>=self.task['spec']['budget']['tools']:
                self.stop.set();raise ValueError('Native cumulative tool budget exhausted before dispatch')
            try:self.store.chain_guard(self.task['id'],db,'tools',1)
            except ValueError:self.stop.set();raise
            db.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?)',(call_id,self.attempt['id'],digest(data),'INTENT',None))
            self.store.event(self.task['id'],self.attempt['id'],'native_tool_started',{'call_id':call_id,'item':data,'task_tool_count':count+1,'before_dispatch':True},db)
            try:validate_paths(self.task,self.attempt,data)
            except (ValueError,OSError) as exc:
                rejected=exc
                db.execute("UPDATE tool_calls SET state='REJECTED',result=? WHERE id=?",(canonical({'error':str(exc),'not_dispatched':True}),call_id))
                self.store.event(self.task['id'],self.attempt['id'],'native_tool_rejected',{'call_id':call_id,'error':str(exc),'counted_in_budget':True},db)
        if rejected:raise rejected
        return {'allowed':True,'approval_required':not self.official and data.get('tool_name') in {'pwsh','bash'}}

    def stop_check(self,db=None):
        current=self.store.attempt(self.attempt['id'],db)
        if self.terminal or self.stop.is_set() or current['stop_requested'] or current['state'] not in ACTIVE or (not self.official and time.time()>=self.attempt['deadline']):raise ValueError('Stopped, terminal or expired native attempt')

    def flush(self,send):
        # Stop commands and writes share the service lock. Intent is durable
        # before sending; ambiguous writes are never automatically replayed.
        for identity,rpc_id in list(self.pending.items()):
            with self.control_lock:
                with self.store.write() as db:
                    current=self.store.attempt(self.attempt['id'],db)
                    if self.terminal or self.stop.is_set() or current['stop_requested'] or current['state'] not in ACTIVE:return
                    if (not self.official and time.time()>=self.attempt['deadline']):
                        from .native import expire_waits
                        expire_waits(self.store,self.attempt['id'],db,'EXPIRED');self.stop.set();return
                    row=db.execute('SELECT * FROM native_requests WHERE id=?',(identity,)).fetchone()
                    if row['state']!='READY':continue
                    db.execute("UPDATE native_requests SET state='REPLY_INTENT' WHERE id=?",(identity,))
                if (not self.official and time.time()>=self.attempt['deadline']):
                    from .native import expire_waits
                    with self.store.write() as db:expire_waits(self.store,self.attempt['id'],db,'EXPIRED',unsent_intent=identity)
                    self.stop.set();return
                try:send({'action':'native.reply','id':rpc_id,'response':json.loads(row['response'])})
                except Exception:
                    with self.store.write() as db:db.execute("UPDATE native_requests SET state='REPLY_UNKNOWN' WHERE id=?",(identity,))
                    self.stop.set();raise
                with self.store.write() as db:
                    db.execute("UPDATE native_requests SET state='SENT' WHERE id=? AND state='REPLY_INTENT'",(identity,))
                    self.update_wait_state(db)
                self.store.event(self.task['id'],self.attempt['id'],'native_response_sent',{'request_id':identity,'delivery':'WRITE_RETURNED; SDK application separate'})
                self.pending.pop(identity)

    def update_wait_state(self,db):
        attempt=self.store.attempt(self.attempt['id'],db)
        if attempt['stop_requested'] or attempt['state'] not in {'WAITING_APPROVAL','WAITING_INPUT'}:
            return
        waiting=db.execute("""SELECT method FROM native_requests WHERE attempt_id=?
            AND state IN ('PENDING','READY') ORDER BY created DESC,id DESC LIMIT 1""",(attempt['id'],)).fetchone()
        state='RUNNING'
        if waiting:
            state='WAITING_INPUT' if waiting['method'].endswith('UserInput') else 'WAITING_APPROVAL'
        if state!=attempt['state']:
            self.store.update_attempt(attempt['id'],state,db=db)

    def cancelled(self,data):
        rpc_id=data.get('id')
        if not isinstance(rpc_id,str) or not rpc_id:
            return
        with self.control_lock,self.store.write() as db:
            row=db.execute("""SELECT id,state FROM native_requests WHERE attempt_id=? AND rpc_id=?
                AND state IN ('PENDING','READY','SENT')""",(self.attempt['id'],canonical(rpc_id))).fetchone()
            if row is None:
                return
            # The worker only emits this when its exact callback expires or
            # aborts without accepting the reply, even if transport wrote it.
            db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE id=?",(time.time(),row['id']))
            self.pending.pop(row['id'],None)
            self.store.event(self.task['id'],self.attempt['id'],'native_request_cancelled',
                             {'request_id':row['id'],'reason':data.get('reason'),'previous_state':row['state']},db)
            self.update_wait_state(db)

    def applied(self,data):
        with self.store.write() as db:
            db.execute("UPDATE native_requests SET state='RESOLVED',resolved=? WHERE attempt_id=? AND rpc_id=? AND state='SENT'",
                       (time.time(),self.attempt['id'],canonical(data.get('id'))))

    def tool(self,identity,item,result=False):
        if not isinstance(identity,str) or not identity:return
        call_id=self.attempt['id']+':sdk-native:'+identity
        with self.control_lock,self.store.write() as db:
            prior=db.execute('SELECT * FROM tool_calls WHERE id=?',(call_id,)).fetchone()
            if result:
                if prior:
                    from .attachments import event_view
                    db.execute("UPDATE tool_calls SET state='COMPLETED',result=? WHERE id=?",(canonical(event_view(item)),call_id))
                    started=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='native_tool_started' AND json_extract(payload,'$.call_id')=? ORDER BY seq LIMIT 1",(self.attempt['id'],call_id)).fetchone()
                    original=json.loads(started[0]).get('item',{}) if started else {}
                    args=original.get('input') or original.get('arguments') or {}
                    path=next((args[k] for k in ('file_path','filePath','path') if isinstance(args.get(k),str)),None)
                    tool_name=original.get('tool_name',original.get('name'))
                    if path and tool_name in {'read_file','Read','view_image'}:
                        actual=Path(path) if Path(path).is_absolute() else Path(self.attempt['workspace'])/path
                        for a in self.task['spec'].get('attachments',[]):
                            if actual.absolute()!= (Path(self.attempt['workspace'])/a['file']).absolute():continue
                            error=item.get('is_error') is True or item.get('isError') is True
                            self.store.event(self.task['id'],self.attempt['id'],'attachment_native_tool_result',
                                {'file':a['file'],'sha256':a['sha256'],'status':'NATIVE_TOOL_REPORTED_ERROR' if error else 'NATIVE_TOOL_RESULT_RETURNED_NOT_COMPREHENSION',
                                 'tool_name':tool_name,'call_id':call_id,'comprehension':'NOT_ATTESTED'},db)
                        for image in self.attempt.get('_attachment_images',[]):
                            if actual.absolute()!=(Path(self.attempt['workspace'])/image['file']).absolute():continue
                            error=item.get('is_error') is True or item.get('isError') is True
                            self.store.event(self.task['id'],self.attempt['id'],'attachment_native_tool_result',
                                {'file':image['source_file'],'sha256':image['source_sha256'],'page':image['page'],'page_sha256':image['sha256'],
                                 'status':'NATIVE_TOOL_REPORTED_ERROR' if error else 'PDF_PAGE_TOOL_RESULT_RETURNED_NOT_COMPREHENSION',
                                 'read_error':error,'tool_name':tool_name,'call_id':call_id,'comprehension':'NOT_ATTESTED'},db)
                return
            if prior:return
            db.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?)',(call_id,self.attempt['id'],digest(item),'INTENT',None))
            count=self.store.usage(task=self.task['id'],db=db)['tool_calls']
            self.store.event(self.task['id'],self.attempt['id'],'native_tool_started',{'call_id':call_id,'item':item,'task_tool_count':count},db)
            chain_error=None
            try:self.store.chain_guard(self.task['id'],db,'tools')
            except ValueError as exc:chain_error=str(exc)
            try:self.store.chain_guard(self.task['id'],db,'tools',1)
            except ValueError:
                self.stop.set()
                self.store.event(self.task['id'],self.attempt['id'],'native_chain_tool_limit_reached',{'next_tool_not_granted':True,'enforcement':'OBSERVATION_THEN_STOP; pre-observation execution UNKNOWN'},db)
            if not self.official and (count>self.task['spec']['budget']['tools'] or chain_error):
                self.overrun=True;self.stop.set()
                self.store.event(self.task['id'],self.attempt['id'],'native_tool_budget_overrun',{'task_tool_count':count,'chain_error':chain_error,'execution_before_observation':'UNKNOWN'},db)

    def observe(self,kind,data):
        if self.official and kind == 'result' and self.task['spec']['provider'] == 'deepseek':
            if data.get('session_id') != self.attempt['session_id']:
                raise ValueError('Native DeepSeek terminal session mismatch')
        if kind=='scene_capability_receipt':
            from .scene_runtime import receipt
            receipt(self.store,self.task,self.attempt,'FAILED' if data.get('missing') else 'NATIVE_OBSERVED',data)
        if kind in {'result','closed'} or kind=='message' and data.get('type')=='result' and not self.official:
            with self.control_lock,self.store.write() as db:
                self.terminal=True;self.pending.clear()
                db.execute("UPDATE native_requests SET state='CANCELLED_TERMINAL',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY')",(time.time(),self.attempt['id']))
        if kind=='native_request':self.register(data)
        elif kind=='native_reply_applied':self.applied(data)
        elif kind=='native_request_cancelled':self.cancelled(data)
        elif kind=='message':
            parent = data.get('parent_tool_use_id')
            if self.official and parent is not None:
                if not isinstance(parent, str) or parent not in self.qwen_tool_uses:
                    return
                if data.get('type') not in {'assistant', 'user'}:
                    return
            elif (self.official and data.get('type') in {'assistant', 'user'}
                  and data.get('session_id') != self.attempt['session_id']):
                return
            if data.get('type')=='system' and data.get('subtype')=='init':
                if (data.get('session_id')!=self.attempt['session_id'] or data.get('model')!=self.task['spec']['model']
                        or Path(data.get('cwd','')).resolve()!=Path(self.attempt['workspace']).resolve()):raise ValueError('Native Qwen effective session/model/workspace mismatch')
                self.initialized=True
                self.store.event(self.task['id'],self.attempt['id'],'native_sdk_configuration',data)
            if data.get('type')=='result' and data.get('session_id')!=self.attempt['session_id']:raise ValueError('Native Qwen terminal session mismatch')
            for item in (data.get('message') or {}).get('content',[]) if isinstance((data.get('message') or {}).get('content'),list) else []:
                if item.get('type')=='tool_use':
                    identity = item.get('id')
                    if self.official and isinstance(identity, str) and identity:
                        self.qwen_tool_uses.add(identity)
                    self.tool(identity, item)
                elif item.get('type')=='tool_result':self.tool(item.get('tool_use_id'),item,True)
        elif kind=='notification':
            payload=data.get('payload') or {}
            if data.get('method')=='subagent.started':
                child=payload.get('childSessionId');parent=payload.get('parentSessionId')
                if isinstance(child, str) and child and isinstance(parent, str) and parent in self.sessions:
                    if self.official:
                        # The control plugin can register this same relationship first.
                        with self.control_lock:
                            if child != parent and (child not in self.sessions or self.sessions[child] == parent):
                                self.register_session({'session_id': child, 'parent_session_id': parent})
                    else:
                        self.sessions[child] = parent
            session = payload.get('sessionId')
            if session not in self.sessions:return
            event=payload.get('event') or {};body=event.get('data') or {}
            if event.get('type')=='turn/end' and (body.get('reason') or {}).get('kind')=='error':
                if not self.official or session == self.attempt['session_id']:
                    self.last_error={'class':'NativeSdkReportedError','detail':(body.get('reason') or {}).get('error')}
            if event.get('type')=='tool/result':
                message=body.get('message') or {}
                if message.get('toolCallId'):self.tool(message['toolCallId'],body,True)
                for item in message.get('content',[]):
                    if item.get('type')=='tool-result':self.tool(item.get('toolCallId'),body,True)
        elif kind=='initialized':
            if data.get('session_id')!=self.attempt['session_id']:raise ValueError('Native DeepSeek session mismatch')
            self.initialized=True
