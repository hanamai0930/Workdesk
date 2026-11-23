"""Business-task runtime projection and version-bound routes to existing controls."""
import json
import time

from .agent_runtime import evidence as agent_evidence
from .store import ACTIVE, UNKNOWN, TERMINAL, uid
from .contracts import bounded_text
from .execution_constraints import PENDING_OPS,blockers,remote_state,view as constraints_view


class WorkRuntime:
    def __init__(self,service):self.s=service

    def manual(self,db,item_id):
        row=db.execute("SELECT data FROM collaboration WHERE kind='work_item_control' AND id=?",('work-item-control:'+item_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def guard(self,db,item_id,allow_driver=False):
        if not allow_driver:self.s.work_drivers.guard(db,item_id,shared=True)
        row=self.manual(db,item_id)
        if row and row['state']=='MANUAL':raise ValueError('该业务任务正在人工接管；结束接管后再明确派发。')

    def guard_task(self,db,task_id,allow_driver=False):
        for item_id in self.s.work_drivers.item_ids(db,task_id):self.guard(db,item_id,allow_driver=allow_driver)

    def rows(self,db,item):
        dispatches={r['attempt_id']:r for r in self.s.collaboration.all(db,'work_dispatch') if r['item_id']==item['id']}
        rows=[]
        for ref in db.execute("SELECT record_id FROM work_item_records WHERE item_id=? AND kind='task' ORDER BY created DESC",(item['id'],)):
            task=self.s.store.task(ref[0],db)
            raw=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC,id DESC LIMIT 1',(task['id'],)).fetchone()
            if not raw:continue
            a=self.s.store.attempt_row(raw);result=a.get('result') or {};dispatch=dispatches.get(a['id'])
            worker=self.s.workers.get(a['id']);live=bool(worker and worker['thread'].is_alive())
            live=live or any(w['thread'].is_alive() and self.s.store.attempt(identity,db)['task_id']==task['id'] for identity,w in self.s.workers.items())
            native_ops=self.s.native_controls.rows(db,task['id'])
            operations=[{k:o.get(k) for k in ('id','attempt_id','operation','state')} for o in native_ops if o['state'] in PENDING_OPS]
            inventory=next(({k:o.get(k) for k in ('id','state','result','error','ended_at')}
                            for o in native_ops if o['operation']=='plugins.workspace'
                            and o['attempt_id']==a['id'] and o['owner']==a['owner']),None)
            activation=next(({k:o.get(k) for k in ('id','state','result','error','ended_at')}
                        for o in native_ops if (o.get('params') or {}).get('_workspace_selection')
                            and o['attempt_id']==a['id'] and o['owner']==a['owner']),None)
            usage=self.s.store.usage(task=task['id'],db=db)
            reasons=blockers(self.s,db,task,a)
            blocked=a['state'] not in TERMINAL or bool(reasons)
            requests=[{'id':r['id'],'kind':'approval','description':r['description'],'state':r['state']} for r in db.execute("SELECT * FROM approvals WHERE attempt_id=? AND state='PENDING' ORDER BY created",(a['id'],))]
            for r in db.execute("SELECT * FROM native_requests WHERE attempt_id=? AND state IN ('PENDING','READY','REPLY_INTENT') ORDER BY created",(a['id'],)):
                requests.append({'id':r['id'],'kind':'native','method':r['method'],'params':json.loads(r['params']),'state':r['state']})
            blocked=blocked or any(r['kind']=='native' and r['state'] in {'READY','REPLY_INTENT'} for r in requests)
            control=db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?",('manual:'+task['id'],)).fetchone()
            control=json.loads(control[0]) if control else None
            available=True
            try:self.s.work_records.guard(db,'task',task['id'])
            except ValueError:available=False
            agent=agent_evidence(db,task,a)
            rows.append({'task_id':task['id'],'attempt_id':a['id'],'owner':a['owner'],'dispatch_id':dispatch['id'] if dispatch else None,
                'owned':bool(dispatch),'available':available,'provider':task['spec']['provider'],'runtime_mode':task['spec'].get('runtime_mode','finite'),
                'cooperative':task['spec'].get('cooperative'),
                'native_extension_inventory':inventory,
                'native_extension_activation':activation,
                'can_query_extensions':bool(dispatch and available and task['spec']['provider']=='qwen'
                    and task['spec'].get('runtime_mode')=='official' and not blocked and not live and not operations),
                **({'agent_evidence':agent} if agent else {}),
                'spec_hash':task['spec_hash'],'model':task['spec']['model'],'effort':task['spec'].get('effort'),'tier':task['spec'].get('tier'),
                'state':a['state'],'task_state':task['state'],'session_id':a['session_id'],'stop_requested':bool(a['stop_requested']),
                'local_worker_alive':live,'known_tools_exited':self.s.operations.exit_proved(a),'remote_state':remote_state(task,a),
                'constraints':constraints_view(self.s,db,task,a,dispatch),'block_reasons':reasons,
                'recovery':{'can_close_local':bool(dispatch and available and a['state'] in UNKNOWN and not live and self.s.operations.exit_proved(a)),
                    'can_probe_codex':bool(dispatch and available and task['spec']['provider']=='codex' and a['session_id'] and a['turn_id'] and not live),
                    'last_report':a.get('reconciliation'),'no_automatic_resend':True},
                'blocked':bool(blocked),'pending_requests':requests,'native_operations':operations,'manual_control':control,
                'can_resume':bool(dispatch and available and not blocked and item['state']=='OPEN' and not task['spec'].get('cooperative') and not (control and control['state']=='MANUAL') and
                    (task['spec'].get('runtime_mode')!='official' or self.s.independent.describe(task['spec']['provider'])['native_resume']))})
        return rows

    def view(self,db,item):
        rows=self.rows(db,item);manual=self.manual(db,item['id']);held=bool(manual and manual['state']=='MANUAL')
        blocked=any(r['blocked'] for r in rows);driven=self.s.work_drivers.protected(db,item['id'])
        for row in rows:
            row['can_resume']=row['can_resume'] and not held and not blocked and not driven
            row['can_query_extensions']=row['can_query_extensions'] and not held and not blocked
            row['can_change_extensions']=self.extension_boundary(db,item,row) and not held and not blocked
        return {'state':'MANUAL' if held else 'NEEDS_RECONCILIATION' if any(r['state'] in UNKNOWN or r['remote_state']=='UNKNOWN' or any(o['state'] in {'DELIVERY_UNKNOWN','UNKNOWN_AFTER_RESTART'} for o in r['native_operations']) for r in rows)
                else 'WAITING_INPUT' if any(r['pending_requests'] for r in rows) else 'RUNNING' if any(r['state'] in ACTIVE for r in rows) else 'EXIT_UNPROVED' if blocked else 'IDLE',
                'manual_control':manual,'runs':rows,'driver_protected':driven,'can_takeover':not held and not blocked and not driven,'checked_at':time.time(),'no_dispatch':True}

    def extension_boundary(self,db,item,row):
        plan=self.s.teamwork.current(db,item['id'])
        flow=self.s.work_flows.current(db,item['id'])
        inventory=row['native_extension_inventory']
        activation=row['native_extension_activation']
        return bool(row['can_query_extensions'] and plan and plan['mode']=='fixed'
                    and (not flow or flow['state'] in {'PAUSED','RESULTS_AVAILABLE'})
                    and inventory and inventory['state']=='COMPLETED'
                    and (inventory.get('result') or {}).get('source')=='QWEN_OFFICIAL_WORKSPACE'
                    and (inventory['result'].get('projection') or {}).get('trusted')
                    and (not activation or (activation.get('ended_at') or 0)<(inventory.get('ended_at') or 0)))

    def route(self,db,p):
        common={'id','revision','action','dispatch_id','task_id','attempt_id','owner'}
        extra={'stop':set(),'reconcile':set(),'probe':set(),'close-local':set(),'native-extensions':set(),
               'native-extension-activation':{'inventory_id','extension_id','activation'},'resume':{'note','mode','spec_hash'},'approval':{'approval_id','allow'},
               'native-response':{'request_id','response'},'takeover':{'note'},'release':{'control_revision'}}
        action=p.get('action')
        if not isinstance(action,str) or action not in extra or set(p)-common-extra[action]:raise ValueError('任务运行操作字段不支持。')
        item=self.s.work_items.checked(db,p);context={'work_item_id':item['id'],'item_revision':item['revision'],'action':action}
        if action in {'takeover','release'}:
            if set(p)&{'dispatch_id','task_id','attempt_id','owner'}:raise ValueError('人工接管作用于整个业务任务。')
            return 'work-item.control',p,context
        row=next((r for r in self.rows(db,item) if r['dispatch_id']==p.get('dispatch_id') and r['dispatch_id'] and r['owned']),None)
        if not row or any(row[k]!=p.get(k) for k in ('task_id','attempt_id','owner')):raise ValueError('任务派发或运行归属已变化，请刷新后操作。')
        scope={k:row[k] for k in ('task_id','attempt_id','owner')}
        context.update(scope,dispatch_id=row['dispatch_id'])
        if action=='native-extensions':
            if not row['can_query_extensions']:raise ValueError('请在该Qwen执行完成并核对退出后查询扩展。')
            return 'native.control',{**scope,'operation':'plugins.workspace','params':{}},context
        if action=='native-extension-activation':
            if not self.extension_boundary(db,item,row):raise ValueError('请在固定流程暂停或完成后重新查询，再配置工作区扩展。')
            inventory=row['native_extension_inventory']
            if inventory['id']!=p.get('inventory_id') or p.get('activation') not in {'enabled','disabled'}:
                raise ValueError('扩展查询版本或启停目标无效。')
            projection=inventory['result']['projection']
            extension=next((entry for entry in projection['extensions'] if entry['extensionId']==p.get('extension_id')),None)
            if not extension:raise ValueError('所选扩展不在本次官方目录中。')
            selection={'inventory_id':inventory['id'],'extension_id':extension['extensionId'],
                'name':extension['name'],'version':extension['version'],'generation':projection['desiredGeneration'],
                'activation':p['activation']}
            return 'native.control',{**scope,'operation':'plugins.enable' if p['activation']=='enabled' else 'plugins.disable',
                                     'params':{'name':extension['name'],'_workspace_selection':selection}},context
        if action in {'approval','native-response'}:
            identity=p.get('approval_id') if action=='approval' else p.get('request_id')
            kind='approval' if action=='approval' else 'native'
            if not any(r['id']==identity and r['kind']==kind and r['state']=='PENDING' for r in row['pending_requests']):raise ValueError('请求不属于本次任务运行，或已经处理。')
            context['request_id']=identity
            return ('approval.resolve',{'approval_id':identity,'allow':p.get('allow')},context) if action=='approval' else ('native.resolve',{**scope,'request_id':identity,'response':p.get('response'),'source':'HUMAN_WORK_ITEM'},context)
        if action=='stop':return 'task.stop',scope,context
        if action=='reconcile':return 'task.reconcile',scope,context
        if action=='probe':
            if not row['recovery']['can_probe_codex']:raise ValueError('此执行没有可核对的准确Codex回合，或本地仍在运行。')
            return 'task.reconcile',{**scope,'probe_codex':True},context
        if action=='close-local':
            if row['runtime_mode']!='official' or not row['recovery']['can_close_local']:raise ValueError('此回合尚不满足本地关闭条件。')
            return 'independent.recover',scope,context
        if not row['can_resume']:raise ValueError('当前执行尚不可接续；先核对状态、退出和人工接管。')
        self.guard(db,item['id'])
        task=self.s.store.task(row['task_id'],db)
        if p.get('spec_hash')!=task['spec_hash']:raise ValueError('执行配置已变化，请刷新后核对再接续。')
        note=bounded_text(p.get('note'),'接续指令',4000)
        context['spec_hash']=task['spec_hash']
        if task['spec'].get('runtime_mode')=='official':
            if p.get('mode','native')!='native':raise ValueError('官方工作使用原会话接续。')
            effective={**scope,'goal':note};kind='independent.send'
        else:
            effective={**scope,'spec_hash':task['spec_hash'],'mode':p.get('mode','native'),'note':note};kind='task.resume'
        effective['work_item']={'id':item['id'],'revision':item['revision']}
        return kind,effective,context

    def hold(self,db,p):
        item=self.s.work_items.checked(db,p);old=self.manual(db,item['id'])
        if p['action']=='takeover':
            view=self.view(db,item)
            if not view['can_takeover']:raise ValueError('先停止并核对全部关联执行及原生操作，退出证据齐备后才能人工接管。')
            note=p.get('note','')
            if not isinstance(note,str) or len(note)>4000:raise ValueError('人工接管说明最多4000字。')
            row={'id':'work-item-control:'+item['id'],'item_id':item['id'],'item_revision':item['revision'],'revision':uid(),
                 'state':'MANUAL','note':note,'at':time.time(),'scope':'WORKDESK_DISPATCH_GUARD; NOT_AN_OS_LOCK',
                 'attempt_ids':[r['attempt_id'] for r in view['runs']]}
        else:
            if not old or old['state']!='MANUAL' or p.get('control_revision')!=old['revision']:raise ValueError('人工接管版本已变化，请刷新。')
            row={**old,'state':'RELEASED','revision':uid(),'released':time.time()}
        self.s.collaboration.put(db,'work_item_control',row)
        return {'manual_control':row,'no_dispatch':True,'automatically_resumed':False}

    def recorded(self,db,receipt,context):
        if context:
            self.s.store.event(context.get('task_id'),context.get('attempt_id'),'work_item_control_requested',context,db)
            receipt={**receipt,'work_item_control':context}
        return receipt
