"""Explicit ownership transfer and finite two/three independent working copies."""
import json
from pathlib import Path
import time

from . import workspace
from .paths import atomic_json
from .store import ACTIVE,UNKNOWN,canonical,digest,uid


class Operations:
    def __init__(self,service):
        self.s=service;self.store=service.store;self.c=service.collaboration
        with self.store.write() as db:
            for group in self.c.all(db,'parallel'):
                if group['state']=='RUNNING':
                    group.update(state='PAUSED',reason='控制服务重启，先核对原尝试。');self.c.put(db,'parallel',group)

    def handlers(self):return {'task.handoff':self.handoff,'parallel.create':self.parallel_create,
             'parallel.start':self.parallel_start,'parallel.pause':self.parallel_pause}

    def is_current(self,db,task):
        row=db.execute("SELECT data FROM collaboration WHERE kind='ownership' AND id=?",('owner:'+task,)).fetchone()
        return not row or json.loads(row[0])['state']!='RETIRED'

    def guard_task(self,db,task,allow_driver=False):
        self.s.work_records.guard(db,'task',task)
        self.s.monitor.guard(db,{'task_id':task},allow_driver=allow_driver)
        if not self.is_current(db,task):raise ValueError('原执行归属已转交；旧任务仅保留历史，不能恢复写入/整合资格。')

    def group(self,db,task):
        return next((g for g in self.c.all(db,'parallel') if task in g['tasks'] and g['state'] in {'APPROVED','RUNNING','PAUSED'}),None)

    @staticmethod
    def exit_proved(row):
        row=dict(row)
        result=row.get('result') or {};reconciliation=row.get('reconciliation') or {}
        if isinstance(result,str):result=json.loads(result)
        if isinstance(reconciliation,str):reconciliation=json.loads(reconciliation)
        return bool(reconciliation.get('known_local_scopes_exited') or result.get('known_tools_exited') and (result.get('owned_job') or {}).get('active_processes')==0)

    def concurrency_guard(self,db,task,rows):
        current=self.group(db,task['id']);live=[]
        for identity,worker in self.s.workers.items():
            if worker['thread'].is_alive():live.append(self.store.attempt(identity,db))
        blocking=[dict(r) for r in rows if r['state'] in ACTIVE or not self.exit_proved(r)]
        if not current or current['state']!='RUNNING':
            if blocking:raise ValueError('仍有在途或未核对的旧尝试，先停止/核对。')
            if live:raise ValueError('旧执行仍在收尾，稍后重读状态。')
            return
        allowed=set(current['tasks'])
        if any(r['state'] not in ACTIVE and not self.exit_proved(r) or r['state'] in UNKNOWN or r['task_id'] not in allowed or r['task_id']==task['id'] for r in blocking+live):
            raise ValueError('并行组外占用、同任务旧运行或未知归属尚未核对。')
        occupied={r['id'] for r in blocking+live}
        if len(occupied)>=current.get('limit',2):raise ValueError('有限并行已达到本组明确副本名额。')

    def quiescent(self,db,task):
        attempts=[self.store.attempt_row(r) for r in db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started',(task['id'],))]
        if any(w['thread'].is_alive() for identity,w in self.s.workers.items() if any(a['id']==identity for a in attempts)):
            raise ValueError('旧执行线程仍在收尾，不能接管。')
        if any(a['state'] in ACTIVE or a['state'] in UNKNOWN and not (
               (a['reconciliation'] or {}).get('known_local_scopes_exited') and
               (a['reconciliation'] or {}).get('protocol_terminal') in {'completed','failed','interrupted'}) for a in attempts) or self.store.usage(task=task['id'],db=db)['unknown_requests']:
            raise ValueError('旧执行/模型回执未知，先对账，不替代执行。')
        if not attempts:return None
        last=attempts[-1];result=last['result'] or {}
        if not result.get('known_tools_exited') or (result.get('owned_job') or {}).get('active_processes')!=0:
            raise ValueError('已知工具/专属进程退出证据不足，不能接管。')
        if result.get('remote_model_state') not in {'completed','failed','interrupted','NOT_SENT'}:raise ValueError('旧远端仍未知，不能将本地退出当作接管资格。')
        return last

    def handoff_preview_db(self,db,task_id):
        task=self.store.task(task_id,db);self.guard_task(db,task_id)
        if task['state'] in {'CANCELLED','HANDED_OFF'}:raise ValueError('任务已取消或转交。')
        grant=task['grant']
        if not grant or grant['spec_hash']!=task['spec_hash'] or grant['expires_at']<=time.time():raise ValueError('原任务范围授权缺失/过期。')
        alternatives=task['spec'].get('alternatives',[])
        if not alternatives:raise ValueError('原范围没有授权替代工作端；等待Human修订具体接收方。')
        latest=self.quiescent(db,task)
        folder=self.s.locations.data/'tasks'/task_id
        original=workspace.current_manifest(folder/'input',task['spec']['materials'])
        if original!=task['spec']['materials']:raise ValueError('原始准确材料已变化。')
        source=folder/'attempts'/latest['id']/'files' if latest else folder/'input'
        manifest=(latest['result'] or {}).get('manifest') if latest else original
        if not isinstance(manifest,dict) or workspace.current_manifest(source,manifest)!=manifest:raise ValueError('冻结候选不可核对。')
        if latest and workspace.current_manifest(latest['workspace'],set(task['spec']['materials'])|set(task['spec']['writable']))!=manifest:
            raise ValueError('旧工作副本在收尾后发生变化，先明确新版本。')
        usage=self.store.usage(task=task_id,db=db);budget=task['spec']['budget']
        remaining={**budget,'attempts':budget['attempts']-usage['attempts'],'tools':budget['tools']-usage['tool_calls'],
                   'requests':budget['requests']-usage['model_requests'],'cny':round(budget['cny']-usage['reserved_cny'],6)}
        if remaining['attempts']<1 or min(remaining['tools'],remaining['requests'],remaining['cny'])<0:raise ValueError('原任务剩余预算不足接管，不清零重来。')
        decisions=[d for d in self.c.all(db,'decision') if any(p['id']==d['plan_id'] for p in self.c.all(db,'plan') if any(i['task_id']==task_id for i in p['items']))]
        value={'task_id':task_id,'spec_hash':task['spec_hash'],'attempt_id':latest['id'] if latest else None,
               'original_materials':original,'candidate_manifest':manifest,'alternatives':alternatives,
               'usage':usage,'remaining_task_budget':remaining,'decisions':decisions,
               'result':latest['result'] if latest else None,'events':self.store.events(task_id,limit=500,db=db),
               'event_count':db.execute('SELECT COUNT(*) FROM events WHERE task_id=?',(task_id,)).fetchone()[0],
               'transfer_scope':'exact original files + frozen candidate + full task specification/results/events/decisions; summary is navigation only',
               'no_dispatch':True,'local_scopes_exited':True}
        if value['event_count']>500:raise ValueError('任务事件超过当前有界交接包容量，须缩小/扩展明确方案，不截断交接。')
        value['preview_hash']=digest(value);return value

    def preview(self,task):
        with self.store.read() as db:return self.handoff_preview_db(db,task)

    def handoff(self,db,p):
        from .workbench import lock_started_model
        lock_started_model(self.s,db,self.store.task(p.get('task_id'),db))
        preview=self.handoff_preview_db(db,p.get('task_id'))
        if preview['preview_hash']!=p.get('preview_hash'):raise ValueError('接管预览已变化，未转移归属。')
        choice=p.get('alternative')
        if type(choice) is not int or not 0<=choice<len(preview['alternatives']):raise ValueError('请选择原授权中的替代路线。')
        config=preview['alternatives'][choice];self.s.profiles.validate(**{k:config[k] for k in ('provider','model','effort','tier')})
        if config['provider']!='codex' and (preview['remaining_task_budget']['requests']<1 or preview['remaining_task_budget']['cny']<=0):raise ValueError('原剩余API请求/费用预算不足，未转交。')
        task=self.store.task(preview['task_id'],db);spec=task['spec'];folder=self.s.locations.data/'tasks'/task['id']
        note=p.get('unresolved','')
        if not isinstance(note,str) or len(note)>4000:raise ValueError('未决事项说明超过范围。')
        identity=uid();package=self.s.locations.data/'handoffs'/identity/'payload'
        workspace.copy_manifest(folder/'attempts'/preview['attempt_id']/'files' if preview['attempt_id'] else folder/'input',package,preview['candidate_manifest'])
        prefix='handoff-evidence-'+identity[:8];mapping={}
        for name in spec['materials']:
            if name in {a['file'] for a in spec.get('attachments',[])}:
                if spec['materials'][name]!=preview['candidate_manifest'].get(name):raise ValueError('原始只读附件在候选中改变，不能接管。')
                mapping[name]=name;continue
            target=prefix+'/original/'+digest(name)+'.txt';mapping[name]=target
            destination=package/target;destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(workspace.read_blob(folder/'input',name)['raw'])
        context={'source':preview,'specification':spec,'original_path_mapping':mapping,'unresolved':note,
                 'authority':'exact preapproved alternative; same remaining task and chain budget','summary_is_not_material':True}
        atomic_json(package/prefix/'context.json',context)
        names=list(dict.fromkeys([*preview['candidate_manifest'],*mapping.values(),prefix+'/context.json']))
        baseline=spec.get('integration_base',spec['materials'])
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if member:chain_id=member[0]
        else:
            chain=self.c.chain_create(db,{'title':'接管问题链 · '+task['title'], 'budget':{
                  'attempts':spec['budget']['attempts'],'codex_turns':spec['budget']['attempts'],
                  'requests':min(60,spec['budget']['requests']),'cny':spec['budget']['cny'],
                  'tools':spec['budget']['tools'],'seconds':min(3600,spec['budget']['seconds']*spec['budget']['attempts'])}})['chain']
            chain_id=chain['id'];self.c.adopt_lineage(db,task['id'],chain_id)
        from .attachments import requested
        row=self.s._create(db,{'project_id':task['project_id'],'title':task['title']+' · 接管',**config,
                 'goal':spec['goal']+'\nRead '+prefix+'/context.json and original material mapping before continuing. '
                        'Preserve existing candidate changes; report decisions and unresolved issues. Do not replay previous model/tool requests.\nUnresolved: '+note,
                 'acceptance':spec['acceptance'],'files':names,'writable':spec['writable'],'checks':spec['checks'],
                 'host_command_permission':spec['host_command_permission'],'budget':preview['remaining_task_budget'],
                 'campaign':spec['campaign'],'alternatives':spec.get('alternatives',[]),'chain_id':chain_id,
                 'work_mode':'cooperative','controller_checks':spec.get('controller_checks',False),'attachments':requested(spec)},snapshot_root=package,integration_base=baseline)
        newfolder=self.s.locations.data/'tasks'/row['task_id']
        workspace.copy_manifest(folder/('integration-base' if 'integration_base' in spec else 'input'),newfolder/'integration-base',baseline)
        self.c.member(db,row['task_id'],chain_id);self.s._approve(db,row)
        grant=self.store.task(row['task_id'],db)['grant'];grant.update(expires_at=task['grant']['expires_at'],source_task=task['id'],handoff_id=identity)
        db.execute('UPDATE tasks SET grant=? WHERE id=?',(canonical(grant),row['task_id']))
        for plan in self.c.all(db,'plan'):
            item=next((i for i in plan['items'] if i['task_id']==task['id']),None)
            if item:
                item.update(task_id=row['task_id'],spec_hash=row['spec_hash'],handoff_source_task=task['id'],handoff_id=identity)
                plan.update(queue_state='PAUSED',pause_reason='接管范围已按原授权转换；显式继续队列。');self.c.put(db,'plan',plan)
        grant.update(self.c.dependency_grant_binding(db,row['task_id']))
        db.execute('UPDATE tasks SET grant=? WHERE id=?',(canonical(grant),row['task_id']))
        self.c.put(db,'ownership',{'id':'owner:'+task['id'],'task_id':task['id'],'state':'RETIRED','replacement':row['task_id'],'handoff_id':identity,'at':time.time()})
        self.c.put(db,'handoff',{'id':identity,'source_task':task['id'],'target_task':row['task_id'],'preview_hash':preview['preview_hash'],
                     'chain_id':chain_id,'candidate_manifest':preview['candidate_manifest'],'original_mapping':mapping,'at':time.time()})
        db.execute("UPDATE tasks SET state='HANDED_OFF',updated=? WHERE id=?",(time.time(),task['id']))
        self.store.event(task['id'],preview['attempt_id'],'ownership_transferred',{'target_task':row['task_id'],'handoff_id':identity,'budget_reset':False},db)
        return {**row,'handoff_id':identity,'chain_id':chain_id,'no_dispatch':True,'original_retired':True,'budget_reset':False}

    def parallel_create(self,db,p):
        tasks=p.get('tasks')
        if not isinstance(tasks,list) or len(tasks) not in {2,3} or len(set(tasks))!=len(tasks):raise ValueError('有限并行需要两个或三个不同任务。')
        items=[]
        for identity in tasks:
            task=self.store.task(identity,db);self.guard_task(db,identity)
            if task['state']!='APPROVED' or not task['grant'] or task['grant']['expires_at']<=time.time():raise ValueError('并行任务须分别有有效的准确范围授权。')
            if self.group(db,identity):raise ValueError('任务已属于另一并行组。')
            if db.execute('SELECT 1 FROM attempts WHERE task_id=?',(identity,)).fetchone():raise ValueError('首版并行仅接收尚未派发的任务；恢复先串行核对。')
            items.append({'task_id':identity,'spec_hash':task['spec_hash'],'materials':task['spec']['materials'],
                          'recipient':task['spec']['provider'],'budget':task['spec']['budget'],'writable':task['spec']['writable']})
        group={'id':uid(),'tasks':tasks,'scope':items,'state':'APPROVED','limit':len(tasks),'same_target_policy':'independent copies; one hash-guarded integration entry',
               'created':time.time(),'authority':'Human control command','version':digest(items)}
        self.c.put(db,'parallel',group);return {'parallel':group,'no_dispatch':True}

    def parallel_start(self,db,p):
        group=self.c.get(db,'parallel',p.get('group_id'))
        if group['version']!=p.get('version') or group['state'] not in {'APPROVED','PAUSED'}:raise ValueError('并行组版本/状态不允许开始；不重发原尝试。')
        group['state']='RUNNING';self.c.put(db,'parallel',group);launches=[]
        for scope in group['scope']:
            task=self.store.task(scope['task_id'],db)
            if task['spec_hash']!=scope['spec_hash'] or db.execute('SELECT 1 FROM attempts WHERE task_id=?',(task['id'],)).fetchone():raise ValueError('并行组任务已变化/派发，先核对，不重复开始。')
            row=self.s._start(db,scope);launches.append(row['launch_attempt'])
        return {'group_id':group['id'],'launch_attempts':launches,'state':'RUNNING','limit':group.get('limit',2)}

    def parallel_pause(self,db,p):
        group=self.c.get(db,'parallel',p.get('group_id'));group['state']='PAUSED';self.c.put(db,'parallel',group)
        return {'parallel':group,'current_attempts_stopped':False,'note':'仅暂停后续资格；当前回合须分别task.stop。'}

    def finished(self,db,task):
        group=self.group(db,task['id'])
        if not group or group['state']!='RUNNING':return
        rows=[db.execute('SELECT state,result FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(identity,)).fetchone() for identity in group['tasks']]
        if all(r and r['result'] for r in rows):
            group['state']='NEEDS_RECONCILIATION' if any(r['state'] in UNKNOWN or not self.exit_proved(r) for r in rows) else 'FINISHED'
            self.c.put(db,'parallel',group)

    def catalog(self):
        with self.store.read() as db:
            groups=self.c.all(db,'parallel')
            for g in groups:g['attempts']=[dict(r) for r in db.execute('SELECT id,task_id,state,owner FROM attempts WHERE task_id IN ('+','.join('?' for _ in g['tasks'])+')',g['tasks'])]
            return {'parallel_groups':groups,'handoffs':self.c.all(db,'handoff'),'ownership':self.c.all(db,'ownership'),
                    'no_dispatch':True,'parallel_limit':3}
