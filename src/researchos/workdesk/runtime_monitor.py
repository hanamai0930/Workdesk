"""Read-only runtime summary and explicit, reversible manual control markers."""
import json
import time

from .store import ACTIVE,UNKNOWN,uid
from .execution_constraints import blockers,remote_state


class RuntimeMonitor:
    def __init__(self,service):self.s=service;self.c=service.collaboration
    def handlers(self):return {'monitor.takeover':self.takeover,'monitor.release':self.release}

    def catalog(self,scope='attention'):
        if scope not in {'attention','all'}:raise ValueError('监控筛选无效。')
        rows=[]
        with self.s.store.read() as db:
            manual={r['task_id']:r for r in self.c.all(db,'manual_control')}
            for r in db.execute("SELECT coalesce(json_extract(d.data,'$.title'),t.title) AS title,json_extract(t.spec,'$.provider') AS provider,json_extract(t.spec,'$.runtime_mode') AS runtime_mode,a.* FROM tasks t JOIN attempts a ON a.id=(SELECT id FROM attempts WHERE task_id=t.id ORDER BY started DESC LIMIT 1) LEFT JOIN collaboration d ON d.kind='display' AND d.id='display:task:'||t.id WHERE coalesce(json_extract(d.data,'$.deleted'),0)=0 AND coalesce(json_extract(t.spec,'$.interaction'),'')!='chat' ORDER BY a.started DESC"):
                a=self.s.store.attempt_row(r);task_id=a['task_id'];control=manual.get(task_id)
                task=self.s.store.task(task_id,db);reasons=blockers(self.s,db,task,a)
                items=[]
                for item in db.execute("SELECT w.id,w.title FROM work_items w JOIN work_item_records r ON w.id=r.item_id WHERE r.kind='task' AND r.record_id=?",(task_id,)):
                    hold=self.s.work_runtime.manual(db,item['id'])
                    items.append({**dict(item),'manual':bool(hold and hold['state']=='MANUAL'),
                                  'driver_held':self.s.work_drivers.held(db,item['id'])})
                if scope=='attention' and not reasons and a['state'] not in ACTIVE|UNKNOWN and not (control and control['state']=='MANUAL') and not any(i['manual'] for i in items):continue
                worker=self.s.workers.get(a['id']);result=a.get('result') or {}
                pending=db.execute("SELECT COUNT(*) FROM native_requests WHERE attempt_id=? AND state='PENDING'",(a['id'],)).fetchone()[0]
                pending+=db.execute("SELECT COUNT(*) FROM approvals WHERE attempt_id=? AND state='PENDING'",(a['id'],)).fetchone()[0]
                unit=self.s.work_units.reservation(db,a['id'])
                cooperative=None
                if unit:
                    plan=self.c.get(db,'teamwork_plan',unit['plan_id'])
                    definition=next(u for u in plan['units'] if u['key']==unit['unit_key'])
                    flow=self.s.work_flows.current(db,unit['item_id'])
                    cooperative={'item_id':unit['item_id'],'plan_id':unit['plan_id'],'plan_version':unit['plan_version'],
                                 'unit_key':unit['unit_key'],'unit_title':definition['title'],'run_id':unit['id'],
                                 'driver_generation':unit['generation'],'flow':unit.get('flow'),
                                 'flow_state':flow['state'] if flow and unit.get('flow') and flow['generation']==unit['flow']['generation'] else None,
                                 'member_name':definition['member']['name'], 'connection_fingerprint':unit['connection_fingerprint'],
                                 'exact_identity':self.s.work_units.evidence(db,unit)['exact_identity']}
                rows.append({'task_id':task_id,'title':r['title'],'provider':r['provider'],'runtime_mode':r['runtime_mode'],'state':a['state'],
                             'can_steer':r['runtime_mode']=='official' and task['spec'].get('qwen_runtime')!='daemon',
                             'cooperative':cooperative,'driver_protected':any(self.s.work_drivers.held(db,i) for i in self.s.work_drivers.item_ids(db,task_id)),
                             'attempt_id':a['id'],'owner':a['owner'],'session_id':a['session_id'],'turn_id':a['turn_id'],
                             'started':a['started'],'stop_requested':bool(a['stop_requested']),
                             'local_worker_alive':bool(worker and worker['thread'].is_alive()),'known_tools_exited':self.s.operations.exit_proved(a),
                              'remote_state':remote_state(task,a),'block_reasons':reasons,'pending_approvals':pending,
                             'manual_control':control,'work_items':items,'reconciliation':a.get('reconciliation'),'native_operations':[{k:o.get(k) for k in ('id','attempt_id','operation','state','error','created')} for o in self.s.native_controls.rows(db,task_id)[:3]]})
            api_chats=[];seen=set()
            for turn in sorted(self.c.all(db,'ui_chat_turn'),key=lambda t:t['created'],reverse=True):
                if turn['chat_id'] in seen:continue
                seen.add(turn['chat_id'])
                if scope=='attention' and turn['state'] not in ACTIVE|UNKNOWN:continue
                chat=self.c.get(db,'ui_chat',turn['chat_id']);worker=self.s.ui_features.chats.api.workers.get(turn['id'])
                api_chats.append({'chat_id':chat['id'],'turn_id':turn['id'],'title':chat['title'],'state':turn['state'],'created':turn['created'],
                                  'local_worker_alive':bool(worker and worker['thread'].is_alive()),'stop_requested':turn.get('stop_requested',False),
                                  'error':turn.get('error'),'usage':turn.get('usage'),'model':turn['config']['model'],
                                  'can_stop':chat.get('pending')==turn['id'] and turn['state'] in ACTIVE,'remote_state':'UNKNOWN','agent_dispatch':False})
        return {'runs':rows[:200],'api_chats':api_chats[:200],'limited':len(rows)>200 or len(api_chats)>200,'scope':scope,'checked_at':time.time(),'no_dispatch':True,'health':self.s.health()}

    def takeover(self,db,p):
        task,a=self.s._latest(db,p);self.s.work_records.guard(db,'task',task['id'])
        for item_id in self.s.work_drivers.item_ids(db,task['id']):self.s.work_drivers.guard(db,item_id)
        if not a or a['state'] in ACTIVE|UNKNOWN or not self.s.operations.exit_proved(a):raise ValueError('先停止并核对旧回合，本地退出和已知终态齐备后才能转人工。')
        worker=self.s.workers.get(a['id'])
        if worker and worker['thread'].is_alive():raise ValueError('运行时仍在收尾。')
        if blockers(self.s,db,task,a):raise ValueError('远端或原生控制状态仍未知，先核对回执。')
        if self.s.store.usage(task=task['id'],db=db)['unknown_requests']:raise ValueError('仍有未知请求，先核对。')
        old=db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?",('manual:'+task['id'],)).fetchone()
        if old and json.loads(old[0])['state']=='MANUAL':raise ValueError('当前已经由人工处理。')
        note=p.get('note','')
        if not isinstance(note,str) or len(note)>4000:raise ValueError('人工处理说明最多4000字。')
        row={'id':'manual:'+task['id'],'revision':uid(),'task_id':task['id'],'attempt_id':a['id'],'state':'MANUAL','note':note,'at':time.time(),
             'scope':'WORKDESK_DISPATCH_GUARD; NOT_AN_OS_LOCK','no_auto_resume':True}
        self.c.put(db,'manual_control',row);self.s.store.event(task['id'],a['id'],'manual_control_started',row,db)
        return {'manual_control':row,'no_dispatch':True}

    def release(self,db,p):
        row=self.c.get(db,'manual_control',p.get('id'))
        if row['revision']!=p.get('revision') or row['state']!='MANUAL':raise ValueError('人工处理版本已变化。')
        row.update(state='RELEASED',revision=uid(),released=time.time());self.c.put(db,'manual_control',row)
        self.s.store.event(row['task_id'],None,'manual_control_released',row,db)
        return {'manual_control':row,'no_dispatch':True,'automatically_resumed':False}

    def guard(self,db,p,allow_driver=False):
        if not p.get('task_id'):return
        self.s.work_runtime.guard_task(db,p['task_id'],allow_driver=allow_driver)
        row=db.execute("SELECT data FROM collaboration WHERE kind='manual_control' AND id=?",('manual:'+p['task_id'],)).fetchone()
        if row and json.loads(row[0])['state']=='MANUAL':raise ValueError('该工作正在人工处理，请先结束人工接管；不会自动派发。')
