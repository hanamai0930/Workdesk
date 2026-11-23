"""Version-bound discussion, serial plans, reviews and guarded project integration.

Model replies are proposals, never control commands. All writes enter through the
Human control API or through a persisted, explicitly enabled plan grant."""
import json
import threading
import time
from . import workspace
from .store import ACTIVE, UNKNOWN, canonical, uid
from .contracts import READ_TOOLS, ROLES, text
from .plans import PlanCommands
from .candidates import CandidateCommands
from .scheduler import CollaborationScheduler


class Collaboration(PlanCommands, CandidateCommands, CollaborationScheduler):
    def __init__(self,service):
        self.s=service;self.store=service.store;self.stop=threading.Event();self.thread=None
        # Restart never silently resumes queue dispatch, even with an old grant.
        with self.store.write() as db:
            for plan in self.all(db,'plan'):
                if plan['queue_state']=='RUNNING':
                    plan.update(queue_state='PAUSED',pause_reason='控制服务重启；先核对，再显式继续队列。')
                    self.put(db,'plan',plan)

    def get(self,db,kind,identity):
        row=db.execute('SELECT data FROM collaboration WHERE kind=? AND id=?',(kind,identity)).fetchone()
        if row is None:raise ValueError(kind+'记录不存在。')
        return json.loads(row[0])

    def all(self,db,kind):
        return [json.loads(r[0]) for r in db.execute('SELECT data FROM collaboration WHERE kind=? ORDER BY updated DESC',(kind,))]

    def put(self,db,kind,value):
        db.execute('INSERT INTO collaboration VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                   (kind,value['id'],canonical(value),time.time()))
        return value

    def member(self,db,task,chain):
        self.get(db,'chain',chain)
        prior=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task,)).fetchone()
        if prior and prior[0]!=chain:raise ValueError('任务不能换问题链以重置预算。')
        if db.execute('SELECT 1 FROM attempts WHERE task_id=?',(task,)).fetchone():raise ValueError('已有尝试不能补挂预算链。')
        self.adopt_lineage(db,task,chain)

    def adopt_lineage(self,db,task,chain):
        """Count each ancestor's raw usage once, plus only the root's old debt.

        Context replacements carry aggregate task offsets. Adding those offsets
        for every draft double-counts ancestors; ignoring them drops pre-chain
        usage. Admit the actual ancestry instead. Membership grants no execution.
        """
        self.get(db,'chain',chain);lineage=[];seen=set();current=task
        while current:
            if current in seen or len(seen)>=100:raise ValueError('累计消耗的任务来源链无法准确核对。')
            seen.add(current);row=self.store.task(current,db);lineage.append(row)
            parent=(row['spec'].get('selection_from') or {}).get('task_id')
            current=parent
        represented={}
        for row in reversed(lineage):
            prior=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(row['id'],)).fetchone()
            if prior:
                if prior[0]!=chain:raise ValueError('来源任务已属于其他预算链，不能转移或清零消耗。')
            usage=self.store.usage(task=row['id'],db=db);debt=usage.get('restoration_budget_debt',{})
            # Older snapshots may retain aggregate debt without every ancestry
            # edge. Keep the residual as debt, never silently discard it.
            residual={k:max(0,v-represented.get(k,0)) for k,v in debt.items() if type(v) in (int,float)}
            if not prior:
                if row['state'] in ACTIVE|UNKNOWN:raise ValueError('来源任务仍在途或未知，先核对再加入预算链。')
                latest=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(row['id'],)).fetchone()
                if latest and not self.s.operations.exit_proved(latest):raise ValueError('来源任务的已知执行尚未确认退出。')
                db.execute('INSERT INTO chain_members VALUES(?,?)',(row['id'],chain))
                if any(residual.values()):
                    old=db.execute("SELECT data FROM usage_offsets WHERE scope='chain' AND id=?",(chain,)).fetchone()
                    offset=json.loads(old[0]) if old else {}
                    for k,v in residual.items():offset[k]=offset.get(k,0)+v
                    db.execute("INSERT INTO usage_offsets VALUES('chain',?,?) ON CONFLICT(scope,id) DO UPDATE SET data=excluded.data",(chain,canonical(offset)))
            for k,v in usage.items():
                if type(v) in (int,float):represented[k]=represented.get(k,0)+v-debt.get(k,0)+residual.get(k,0)
        self.store.event(task,None,'chain_lineage_admitted',{'chain_id':chain,'tasks':[r['id'] for r in lineage],
            'raw_ancestor_usage_counted_once':True,'no_dispatch':True},db)

    def handlers(self):
        return {'chain.create':self.chain_create,'chain.close':self.chain_close,'dialogue.create':self.dialogue_create,
                'dialogue.message':self.dialogue_message,'dialogue.send':self.dialogue_send,
                'dialogue.configure':self.dialogue_configure,'plan.save':self.plan_save,
                'plan.decide':self.plan_decide,'plan.queue':self.plan_queue,
                'plan.order':self.plan_order,'plan.insert':self.plan_insert,
                'decision.record':self.decision_record,
                'candidate.capture':self.capture,'candidate.review':self.request_review,
                'candidate.check':self.request_check,'candidate.judge':self.judge,
                'candidate.integrate':self.integrate,'candidate.accept':self.accept}

    def catalog(self,view=None):
        if view not in (None,'workbench'):raise ValueError('未知合作目录视图。')
        with self.store.read() as db:
            if view=='workbench':
                # Work conversations do not need candidate filesystem checks or
                # queue integration previews merely to render their sidebar.
                organized=self.s.work_records.catalog(db,sidebar=True)
                return {'dialogues':[d for d in self.all(db,'dialogue') if not self.s.work_records.display(db,'dialogue',d['id']).get('deleted')],
                        'display_records':self.all(db,'display'),'work_records':organized['records'],'work_projects':organized['projects'],
                        'sidebar_work_records':organized['sidebar_records'],
                        'sidebar_cooperation_conversations':self.s.cooperation_conversation.sidebar_entries(db),
                        'no_dispatch':True,'view':'workbench'}
            plans=self.all(db,'plan')
            for p in plans:p['items']=self.items_view(db,p)
            return {'dialogues':self.all(db,'dialogue'),'plans':plans,
                    'chains':[{**c,'usage':self.store.usage(chain=c['id'],db=db)} for c in self.all(db,'chain')],
                    'candidates':[self.candidate_view(db,c) for c in self.all(db,'candidate')],
                    'technical_decisions':self.all(db,'decision'),
                    'display_records':self.all(db,'display'),
                    'no_dispatch':True,'review_is_not_human_acceptance':True}

    def chain_create(self,db,p):
        budget=p.get('budget',{});defaults={'attempts':10,'codex_turns':3,'requests':20,'cny':3,'tools':60,'seconds':600}
        bounds={'attempts':(1,40),'codex_turns':(0,12),'requests':(0,60),'cny':(0,1000),'tools':(0,240),'seconds':(20,3600)}
        if not isinstance(budget,dict) or set(budget)-set(defaults):raise ValueError('问题链预算字段不支持。')
        budget={**defaults,**budget}
        for key,value in budget.items():
            if type(value) not in ((int,float) if key=='cny' else (int,)) or not bounds[key][0]<=value<=bounds[key][1]:raise ValueError('问题链预算超出支持范围。')
        value={'id':uid(),'title':text(p.get('title','普通问题链'),160),'budget':budget,'created':time.time(),
               'authority':'Human control command','budget_reset':False,'state':'OPEN'}
        self.put(db,'chain',value);return {'chain':value}

    def chain_close(self,db,p):
        chain=self.get(db,'chain',p.get('chain_id'))
        usage=self.store.usage(chain=chain['id'],db=db)
        rows=db.execute('SELECT a.* FROM attempts a JOIN chain_members m ON m.task_id=a.task_id WHERE m.chain_id=?',(chain['id'],)).fetchall()
        if usage['unknown_requests'] or any(r['state'] in ACTIVE|UNKNOWN or self.s.workers.get(r['id'],{}).get('thread') and self.s.workers[r['id']]['thread'].is_alive() for r in rows):
            raise ValueError('问题链仍在途/未知，先核对，不能封闭。')
        if chain.get('state')!='CLOSED':
            chain.update(state='CLOSED',closed_at=time.time(),closed_usage=usage);self.put(db,'chain',chain)
        for plan in self.all(db,'plan'):
            if plan['chain_id']==chain['id'] and plan['queue_state']=='RUNNING':
                plan.update(queue_state='PAUSED',pause_reason='问题链已封闭');self.put(db,'plan',plan)
        return {'chain':chain,'no_dispatch':True}

    def dialogue_create(self,db,p):
        project=db.execute('SELECT id FROM projects WHERE id=?',(p.get('project_id'),)).fetchone()
        if project:self.s.work_records.guard_project(db,project[0])
        if not project:raise ValueError('项目不存在。')
        chain=p.get('chain_id') or self.chain_create(db,{'title':p.get('title','协作讨论'),'budget':p.get('budget',{})})['chain']['id']
        self.get(db,'chain',chain)
        config=self.config(p.get('config',{}))
        value={'id':uid(),'project_id':project[0],'chain_id':chain,'title':text(p.get('title','主协作讨论'),160),
               'config':config,'files':p.get('files',[]),'attachments':p.get('attachments',[]),'host_command_permission':p.get('host_command_permission') is True,
               'messages':[],'revision':1,'pending':None,'created':time.time()}
        from .attachments import normalize,requested
        value['attachments']=requested({'attachments':normalize(value['attachments'])})
        from .workbench import scene
        if p.get('work_scene') is not None:value['work_scene']=scene(p['work_scene'])
        roles=p.get('work_roles')
        if roles is not None:
            if not isinstance(roles,dict) or set(roles)!={'lead','execute','review'} or any(v not in {'codex','deepseek','qwen'} for v in roles.values()):
                raise ValueError('协作需要统筹、执行、评审三个工作端，可重复选择。')
            if roles['lead']!=config['provider']:raise ValueError('统筹工作端须与讨论配置一致。')
            value['work_roles']=dict(roles)
        # Validate material paths now; exact bytes are captured for each explicit send.
        root=db.execute('SELECT root FROM projects WHERE id=?',(project[0],)).fetchone()[0]
        workspace.select_files(root,value['files'],self.s.locations.data)
        self.put(db,'dialogue',value);return {'dialogue':value,'no_dispatch':True}

    def config(self,p):
        provider=p.get('provider','codex');defaults={'codex':('gpt-6-luna','low','fast'),
                 'deepseek':('deepseek-flash','off','standard'),'qwen':('qwen3.8-flash','off','standard')}
        if provider not in defaults:raise ValueError('未知工作端。')
        model,effort,tier=defaults[provider];value={'provider':provider,'model':p.get('model',model),
                    'effort':p.get('effort',effort),'tier':p.get('tier',tier)}
        self.s.profiles.validate(**value)
        if 'execution' in p:
            execution=p['execution']
            if execution not in {'scoped',provider+'_native'}:raise ValueError('合作执行路径与工作端不符。')
            value['execution']=execution
        return value

    def dialogue_message(self,db,p):
        self.s.work_records.guard(db,'dialogue',p.get('dialogue_id',''))
        d=self.get(db,'dialogue',p.get('dialogue_id'))
        if d['pending']:raise ValueError('主协作端仍在处理；先等待或从任务页停止/核对。')
        d['messages'].append({'id':uid(),'role':'human','content':text(p.get('content')),'at':time.time()})
        d['revision']+=1;self.put(db,'dialogue',d);return {'dialogue':d,'no_dispatch':True}

    def dialogue_configure(self,db,p):
        self.s.work_records.guard(db,'dialogue',p.get('dialogue_id',''))
        d=self.get(db,'dialogue',p.get('dialogue_id'))
        if d['pending']:raise ValueError('先结束/核对当前讨论回合。')
        if 'work_roles' in p:
            roles=p['work_roles']
            if any(m.get('task_id') for m in d['messages']):raise ValueError('工作已经开始，分工已固定。')
            if not isinstance(roles,dict) or set(roles)!={'lead','execute','review'} or any(v not in {'codex','deepseek','qwen'} for v in roles.values()):
                raise ValueError('协作分工需要三个有效工作端。')
            if roles['lead']!=p.get('config',d['config'])['provider']:raise ValueError('统筹工作端须与讨论配置一致。')
            d['work_roles']=dict(roles)
        if d.get('work_scene') and any(m.get('task_id') for m in d['messages']):
            if self.config(p.get('config',d['config']))!=d['config']:
                raise ValueError('本项工作已经开始，统筹配置固定；其他分工请新建工作。')
        d['config']=self.config(p.get('config',d['config']));d['files']=p.get('files',d['files'])
        from .attachments import normalize,requested
        d['attachments']=requested({'attachments':normalize(p.get('attachments',d.get('attachments',[])))})
        d['host_command_permission']=p.get('host_command_permission',d.get('host_command_permission',False)) is True
        root=db.execute('SELECT root FROM projects WHERE id=?',(d['project_id'],)).fetchone()[0]
        workspace.select_files(root,d['files'],self.s.locations.data)
        d['revision']+=1;self.put(db,'dialogue',d);return {'dialogue':d,'old_decisions_stale':True,'no_dispatch':True}

    def dialogue_send(self,db,p):
        self.s.work_records.guard(db,'dialogue',p.get('dialogue_id',''))
        d=self.get(db,'dialogue',p.get('dialogue_id'))
        if d['pending'] or not d['messages'] or d['messages'][-1]['role']!='human':raise ValueError('没有新的Human消息，或旧回合尚未核对。')
        if p.get('revision')!=d['revision']:raise ValueError('讨论版本已变化。')
        history='\n\n'.join(m['role']+':\n'+m['content'] for m in d['messages'])
        row=self.s._create(db,{'project_id':d['project_id'],'title':'讨论 · '+d['title'],**d['config'],
               'goal':'Act as the coordinator. Discuss the Human goal, propose a bounded plan, roles and review requirements. '
                      'Your reply is a proposal and does not authorize execution. No file edits or commands.\nDISCUSSION CONTEXT:\n'+history,
               'acceptance':'Reply to the current discussion; distinguish proposal, technical decision and Human acceptance.',
               'role':'coordinator','work_scene':d.get('work_scene'),'files':d['files'],'attachments':d.get('attachments',[]),'writable':[],'checks':[],
               'host_command_permission':d.get('host_command_permission',False),'chain_id':d['chain_id'],
               'budget':p.get('budget',{'attempts':1,'seconds':120,'tools':8,'requests':4,'cny':8 if d['config'].get('execution','scoped')!='scoped' else .6})})
        self.member(db,row['task_id'],d['chain_id']);self.s._approve(db,row)
        d['pending']=row['task_id'];self.put(db,'dialogue',d)
        self.put(db,'link',{'id':row['task_id'],'kind':'dialogue','dialogue_id':d['id'],'revision':d['revision']})
        from .cooperation import launch
        return {**self.s._start(db,launch(self.store.task(row['task_id'],db)['spec'],row)),'dialogue_id':d['id'],'execution_authority':False}
