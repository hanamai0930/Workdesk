"""Versioned plans, dependencies and task materialization.

Internal command component; receives the same service/collaboration owner.
Public API, persisted records and grants are unchanged.
"""
import json
import time
from .store import ACTIVE, UNKNOWN, canonical, digest, uid
from .contracts import ROLES, text


class PlanCommands:
    def plan_save(self,db,p):
        d=self.get(db,'dialogue',p.get('dialogue_id')) if p.get('dialogue_id') else None
        project=d['project_id'] if d else p.get('project_id')
        chain=d['chain_id'] if d else p.get('chain_id')
        self.get(db,'chain',chain)
        prior=self.get(db,'plan',p['previous_plan']) if p.get('previous_plan') else None
        if prior and (prior['chain_id']!=chain or prior['project_id']!=project):raise ValueError('修订不能更换项目或问题链。')
        definitions=p.get('items');keys=[]
        if not isinstance(definitions,list) or not 1<=len(definitions)<=20:raise ValueError('方案需要1–20项任务。')
        for item in definitions:
            key=text(item.get('key'),64)
            if key in keys:raise ValueError('任务键重复。')
            keys.append(key)
        edges={item['key']:item.get('depends',[]) for item in definitions}
        self.validate_graph(edges)
        items=[]
        for item in definitions:
            definition=item.get('task',{});role=definition.get('role','executor')
            if role not in ROLES-{'coordinator','reviewer'}:raise ValueError('方案任务职责不支持。')
            row=self.s._create(db,{**definition,'project_id':project,'role':role,'chain_id':chain,
                                  **({'work_scene':d['work_scene']} if d and d.get('work_scene') else {})})
            self.member(db,row['task_id'],chain)
            review_config=dict(item['reviewer']) if item.get('reviewer') else None
            if review_config is not None and definition.get('execution','scoped')!='scoped':
                review_config.setdefault('execution',review_config.get('provider','codex')+'_native')
            reviewer=self.config(review_config) if review_config else None
            from .cooperation import derived_budget
            if reviewer:
                from .attachments import validate
                saved=self.store.task(row['task_id'],db)['spec']
                validate([dict(a) for a in saved.get('attachments',[])],reviewer['provider'],reviewer.get('execution','scoped'),
                    self.s.profiles.selection_snapshot(**{k:reviewer[k] for k in ('provider','model','effort','tier')}),
                    item.get('review_budget',{'tools':12}))
            gate=item.get('gate','reviewed' if reviewer else 'validated')
            if gate not in {'validated','reviewed','integrated','accepted'}:raise ValueError('依赖成果门槛不支持。')
            if gate=='reviewed' and not reviewer:raise ValueError('缺指定独立审查者。')
            input_from=item.get('input_from')
            if input_from and input_from not in edges[item['key']]:raise ValueError('材料来源必须是本项已声明的直接依赖。')
            repairs=item.get('max_repairs',0)
            if type(repairs) is not int or not 0<=repairs<=2:raise ValueError('有限返工仅支持0–2次，且受同一累计预算约束。')
            items.append({'key':item['key'],'task_id':row['task_id'],'spec_hash':row['spec_hash'],'depends':edges[item['key']],
                          'gate':gate,'reviewer':reviewer,'review_budget':item.get('review_budget',derived_budget(reviewer or {'provider':'codex'})),
                          'max_repairs':repairs,'position':len(items),'temporary':False})
            items[-1].update(input_from=input_from,input_materialized=False)
            decisions=item.get('required_decisions',[])
            if not isinstance(decisions,list) or len(decisions)>10 or any(not isinstance(k,str) or not k.strip() or len(k)>64 for k in decisions):raise ValueError('技术决定依赖须为最多10项非空键。')
            items[-1]['required_decisions']=list(dict.fromkeys(decisions))
        value={'id':uid(),'project_id':project,'chain_id':chain,'title':text(p.get('title','协作方案'),160),
               'dialogue_id':d['id'] if d else None,'dialogue_revision':d['revision'] if d else None,
               'items':items,'state':'PROPOSED','decision':None,'queue_state':'PAUSED','created':time.time(),
               'previous_plan':prior['id'] if prior else None}
        value['version']=digest({k:value[k] for k in ('project_id','chain_id','dialogue_id','dialogue_revision','items','previous_plan')})
        if prior:
            prior.update(state='SUPERSEDED',queue_state='PAUSED');self.put(db,'plan',prior)
        self.put(db,'plan',value);return {'plan':value,'no_dispatch':True,'requires_new_decision':True}

    @staticmethod
    def validate_graph(edges):
        visiting=set();done=set()
        def visit(key):
            if key not in edges:raise ValueError('依赖任务不存在。')
            if key in visiting:raise ValueError('检测到循环依赖，未保存方案。')
            if key in done:return
            visiting.add(key)
            deps=edges[key]
            if not isinstance(deps,list) or any(not isinstance(k,str) for k in deps):raise ValueError('依赖须为任务键列表。')
            for dep in deps:visit(dep)
            visiting.remove(key);done.add(key)
        for key in edges:visit(key)

    def current_plan(self,db,p):
        plan=self.get(db,'plan',p.get('plan_id'))
        if plan['version']!=p.get('version'):raise ValueError('方案版本不一致。')
        if plan['state']=='SUPERSEDED':raise ValueError('方案已有修订，旧版本不能继续。')
        if plan['dialogue_id']:
            d=self.get(db,'dialogue',plan['dialogue_id'])
            if d['revision']!=plan['dialogue_revision']:raise ValueError('讨论目标/上下文已变化，需修订方案与决定。')
        return plan

    def plan_decide(self,db,p):
        plan=self.current_plan(db,p);action=p.get('action')
        if action not in {'approve','return','reject'}:raise ValueError('方案决定不支持。')
        if action=='approve':
            for item in plan['items']:self.s._approve(db,{'task_id':item['task_id'],'spec_hash':item['spec_hash'],'hours':p.get('hours',4)})
        plan.update(state='APPROVED' if action=='approve' else action.upper(),queue_state='PAUSED',
                    decision={'actor':'Human control command','action':action,'version':plan['version'],'note':text(p.get('note','已核对该版本范围。')),'at':time.time()})
        self.put(db,'plan',plan);return {'plan':plan,'no_dispatch':True,'human_stage_acceptance':False}

    def plan_queue(self,db,p):
        plan=self.current_plan(db,p);action=p.get('action')
        if action not in {'enable','pause','tick'}:raise ValueError('队列操作不支持。')
        if action=='enable' and plan['state']!='APPROVED':raise ValueError('方案尚未确认。')
        if action=='enable' and self.get(db,'chain',plan['chain_id']).get('state')=='CLOSED':raise ValueError('问题链已封闭，队列不重新开工。')
        if action!='tick':plan['queue_state']='RUNNING' if action=='enable' else 'PAUSED'
        if action=='enable':plan.pop('pause_reason',None)
        self.put(db,'plan',plan)
        return {'plan_id':plan['id'],'queue_state':plan['queue_state'],'no_dispatch_in_command':True}

    def plan_order(self,db,p):
        plan=self.current_plan(db,p);order=p.get('order')
        if not isinstance(order,list) or len(order)!=len(plan['items']) or set(order)!={i['key'] for i in plan['items']}:raise ValueError('顺序须恰好包含每项任务一次。')
        for item in plan['items']:item['position']=order.index(item['key'])
        self.put(db,'plan',plan);return {'plan':plan,'scope_unchanged':True}

    def decision_record(self,db,p):
        plan=self.current_plan(db,p);key=text(p.get('key'),64);status=p.get('status','PROPOSED')
        if status not in {'PROPOSED','ADOPTED','RETURNED'}:raise ValueError('技术决定状态不支持。')
        source=None
        if p.get('source_task'):
            source=self.store.task(p['source_task'],db)
            member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(source['id'],)).fetchone()
            if not member or member[0]!=plan['chain_id']:raise ValueError('决定来源不属于该问题链。')
        value={'id':uid(),'plan_id':plan['id'],'plan_version':plan['version'],'key':key,'status':status,
               'rationale':text(p.get('rationale')),'source_task':source['id'] if source else None,
               'source_role':source['spec']['role'] if source else 'responsible Human control command',
               'recorded_by':'Human control command','created':time.time(),'execution_grant':False,'human_acceptance':False}
        self.put(db,'decision',value);return {'technical_decision':value,'no_dispatch':True}

    def decision_block(self,db,plan,item):
        decisions=self.all(db,'decision')
        for key in item.get('required_decisions',[]):
            latest=next((d for d in decisions if d['plan_id']==plan['id'] and d['plan_version']==plan['version'] and d['key']==key),None)
            if not latest or latest['status']!='ADOPTED':return '等待本版本技术决定：'+key
        return None

    def plan_insert(self,db,p):
        # A temporary task is a new scope: preserve the old position and require a new decision.
        plan=self.current_plan(db,p);task=self.store.task(p.get('task_id'),db)
        if task['project_id']!=plan['project_id'] or task['state']!='DRAFT':raise ValueError('插入须是同项目未执行的草稿。')
        key=text(p.get('key'),64)
        if any(i['key']==key or i['task_id']==task['id'] for i in plan['items']):raise ValueError('插入身份重复。')
        self.member(db,task['id'],plan['chain_id'])
        item={'key':key,'task_id':task['id'],'spec_hash':task['spec_hash'],'depends':[],
              'gate':'validated','reviewer':None,'max_repairs':0,'position':-1,'temporary':True,
              'review_budget':{},'return_positions':{i['key']:i['position'] for i in plan['items']}}
        plan['items'].append(item);plan.update(state='PROPOSED',queue_state='PAUSED',decision=None)
        plan['version']=digest({'previous':plan['version'],'insert':item})
        self.put(db,'plan',plan);return {'plan':plan,'requires_new_decision':True,'no_dispatch':True}

    def items_view(self,db,plan):
        result=[]
        for item in sorted(plan['items'],key=lambda i:i['position']):
            ok,reason=self.gate(db,item)
            blocked=[]
            for key in item['depends']:
                dependency=next(i for i in plan['items'] if i['key']==key)
                satisfied,why=self.gate(db,dependency)
                if not satisfied:blocked.append(key+'：'+why)
            task=self.store.task(item['task_id'],db)
            decision=self.decision_block(db,plan,item)
            if decision:blocked.append(decision)
            source_reason=self.dependency_material_reason(db,plan,item)
            if source_reason:blocked.append(source_reason)
            if not task['grant'] or task['grant']['expires_at']<=time.time():blocked.append('授权缺失/已过期')
            if task['state'] in ACTIVE|UNKNOWN:blocked.append('在途/未知，先等待或核对')
            if task['state'] in {'PAUSED','CANCELLED'}:blocked.append('任务已暂停/取消；队列不擅自恢复')
            if task['state'] in {'FAILED','FAILED_NO_SEND','INTERRUPTED','STOPPED_RUNTIME'}:blocked.append('失败/中止需要Human核对；不自动重发')
            result.append({**item,'state':task['state'],'complete':ok,'reason':reason,'blocked':blocked})
        return result

    def dependency_material_reason(self,db,plan,item):
        if not item.get('input_from') or not item.get('input_materialized'):return None
        source=next(i for i in plan['items'] if i['key']==item['input_from'])
        candidate=self.latest_candidate(db,source['task_id'])
        if not candidate or candidate['id']!=item.get('source_candidate'):
            return '前置候选已改变，已冻结的依赖输入过时；须修订并确认新材料范围，不自动重派。'
        try:self.candidate_current(db,candidate)
        except ValueError as exc:return '依赖输入来源已失效：'+str(exc)
        return None

    def dependency_grant_binding(self,db,task_id):
        bindings=[]
        for plan in self.all(db,'plan'):
            item=next((i for i in plan['items'] if i['task_id']==task_id),None)
            if not item or not item.get('input_from') or not item.get('input_materialized'):continue
            self.current_plan(db,{'plan_id':plan['id'],'version':plan['version']})
            reason=self.dependency_material_reason(db,plan,item)
            if reason:raise ValueError(reason)
            bindings.append({'source_plan':plan['id'],'source_plan_version':plan['version'],
                'source_candidate':item['source_candidate'],'derivation_paths':list(self.store.task(task_id,db)['spec']['materials'])})
        if len(bindings)>1:raise ValueError('依赖材料属于多个方案，须先明确唯一当前范围。')
        return bindings[0] if bindings else {}

    def guard_start(self,db,task):
        linked=db.execute("SELECT data FROM collaboration WHERE kind='link' AND id=?",(task['id'],)).fetchone()
        if linked:
            link=json.loads(linked[0])
            if link['kind'] in {'check','review'}:
                candidate=self.get(db,'candidate',link['candidate_id']);self.derived_authority(db,candidate)
                self.prior_derived_guard(db,[i for i in candidate.get('checks',[])+candidate['reviews'] if i!=task['id']])
                if task['spec']['materials']!=candidate['manifest'] or task['spec']['writable']:
                    raise ValueError('派生任务与原准确候选范围不符。')
        for plan in self.all(db,'plan'):
            item=next((i for i in plan['items'] if i['task_id']==task['id']),None)
            if not item:continue
            if plan['state']!='APPROVED':raise ValueError('所属方案未确认或已被修订。')
            self.current_plan(db,{'plan_id':plan['id'],'version':plan['version']})
            reason=self.decision_block(db,plan,item)
            if reason:raise ValueError(reason)
            if item.get('input_from') and not item.get('input_materialized'):raise ValueError('须先由队列按已批准材料派生规则冻结前置候选；不能直接派发旧输入。')
            source_reason=self.dependency_material_reason(db,plan,item)
            if source_reason:raise ValueError(source_reason)
            if item.get('input_from') and (not task['grant'] or task['grant'].get('source_candidate')!=item.get('source_candidate')
                    or task['grant'].get('source_plan_version')!=plan['version']):raise ValueError('依赖输入授权与准确来源版本不一致。')
            for key in item['depends']:
                dependency=next(i for i in plan['items'] if i['key']==key)
                ok,reason=self.gate(db,dependency)
                if not ok:raise ValueError('依赖 '+key+' 未满足：'+reason)

    def materialize(self,db,plan,item):
        source_item=next(i for i in plan['items'] if i['key']==item['input_from'])
        c=self.latest_candidate(db,source_item['task_id'])
        _,_,root=self.candidate_current(db,c)
        old=self.store.task(item['task_id'],db);spec=old['spec']
        if db.execute('SELECT 1 FROM attempts WHERE task_id=?',(old['id'],)).fetchone():raise ValueError('派生输入前已存在尝试；须核对，不改写历史。')
        if not old['grant'] or old['grant']['expires_at']<=time.time():raise ValueError('派生输入授权已过期。')
        names=list(spec['materials'])
        if any(name not in c['manifest'] for name in names):raise ValueError('前置候选缺少本项已批准的准确材料路径。')
        payload={k:spec[k] for k in ('goal','acceptance','provider','model','effort','tier','role','writable','checks','host_command_permission','budget','campaign','alternatives')}
        from .cooperation import fields
        row=self.s._create(db,{**payload,**fields(spec),'chain_id':plan['chain_id'],'title':old['title'],'project_id':old['project_id'],'files':names},snapshot_root=root)
        self.member(db,row['task_id'],plan['chain_id']);self.s._approve(db,row)
        # The original plan explicitly approved this finite derivation/recipient rule.
        grant=self.store.task(row['task_id'],db)['grant'];grant.update(expires_at=old['grant']['expires_at'],
              source_plan=plan['id'],source_plan_version=plan['version'],source_candidate=c['id'],derivation_paths=names)
        db.execute('UPDATE tasks SET grant=? WHERE id=?',(canonical(grant),row['task_id']))
        db.execute("UPDATE tasks SET state='CANCELLED',grant=NULL,updated=? WHERE id=?",(time.time(),old['id']))
        item.update(task_id=row['task_id'],spec_hash=row['spec_hash'],input_materialized=True,
                    placeholder_task=old['id'],source_candidate=c['id'])
        self.put(db,'plan',plan)
        self.store.event(row['task_id'],None,'plan_dependency_material_frozen',
                         {'plan_id':plan['id'],'approved_version':plan['version'],'source_candidate':c['id'],
                          'paths':names,'recipient':spec['provider'],'placeholder_task':old['id'],'derived_spec_hash':row['spec_hash']},db)
