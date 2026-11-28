"""Business ownership at the existing transactional dispatch boundary."""
from pathlib import Path
import time

from .store import ACTIVE, UNKNOWN
from .execution_constraints import blockers,snapshot
from .desktop_preferences import default_work_directory


class Dispatch:
    def __init__(self,service):self.s=service

    def references(self,db,kind,identity):
        return [r[0] for r in db.execute('SELECT item_id FROM work_item_records WHERE kind=? AND record_id=? ORDER BY item_id',(kind,identity))]

    def before(self,db,payload):
        payload=dict(payload);ref=payload.pop('work_item',None)
        if ref is not None:
            if not isinstance(ref,dict) or set(ref)-{'id','revision','handoff_id'}:raise ValueError('任务接续引用无效。')
            item=self.s.work_items.checked(db,ref)
            self.guard(db,item,{payload['task_id']} if payload.get('task_id') else set())
            if not payload.get('task_id') and payload.get('project_id')!=item['project_id']:
                raise ValueError('接续草稿的项目与工作任务不一致，请重新准备。')
            if ref.get('handoff_id'):
                handoff=self.s.collaboration.get(db,'work_handoff',ref['handoff_id'])
                if handoff['item_id']!=item['id'] or handoff['item_revision']!=item['revision']:
                    raise ValueError('接续任务或版本不一致，请重新准备。')
                if handoff.get('dispatch_id'):raise ValueError('接续草稿已派发，请打开原执行记录。')
        return payload,ref

    def guard(self,db,item,excluding,*,unit_run=None):
        if unit_run is None:self.s.work_runtime.guard(db,item['id'])
        else:self.s.work_units.permit(db,item,unit_run)
        if item['state']!='OPEN':raise ValueError('工作任务未处于进行中，请先在任务页调整状态。')
        for r in db.execute("SELECT record_id FROM work_item_records WHERE item_id=? AND kind='task'",(item['id'],)):
            if r[0] in excluding:continue
            task=self.s.store.task(r[0],db)
            last=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(r[0],)).fetchone()
            observed=self.s.store.attempt_row(last) if last else None
            if task['state'] in ACTIVE|UNKNOWN or observed and blockers(self.s,db,task,observed):
                raise ValueError('关联执行仍活动、状态未知、原生控制在途或退出证据不足，请先在运行与控制中核对。')
        # Native controls may outlive a turn; their queued replies also block a handoff.
        for r in db.execute("SELECT record_id FROM work_item_records WHERE item_id=? AND kind='task'",(item['id'],)):
            if any(op['state'] in {'QUEUED','SEND_INTENT','DELIVERY_UNKNOWN','UNKNOWN_AFTER_RESTART'} for op in self.s.native_controls.rows(db,r[0])):
                raise ValueError('关联原生控制操作仍在途，请先核对回执。')

    def resolve(self,db,task,receipt,ref):
        if ref:return self.s.work_items.checked(db,ref)
        ids=self.references(db,'task',task['id'])
        if not ids and receipt.get('dialogue_id'):ids=self.references(db,'dialogue',receipt['dialogue_id'])
        # Replacements and serial collaboration steps retain their business owner.
        parent=(task['spec'].get('selection_from') or {}).get('task_id')
        if not ids and parent:ids=self.references(db,'task',parent)
        chain=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if not ids and chain:
            ids=[r[0] for r in db.execute("SELECT DISTINCT w.item_id FROM work_item_records w JOIN chain_members c ON w.kind='task' AND w.record_id=c.task_id WHERE c.chain_id=?",(chain[0],))]
        if len(ids)>1:raise ValueError('执行关联了多个业务任务，请从任务页准备本次工作，明确推进任务。')
        if ids:return self.s.work_items.item(db,ids[0])
        scope=db.execute("SELECT project_id FROM record_scopes WHERE kind='task' AND record_id=?",(task['id'],)).fetchone()
        root=db.execute('SELECT root FROM projects WHERE id=?',(task['project_id'],)).fetchone()[0]
        unfiled = {(self.s.locations.data/'unfiled-work').resolve(), default_work_directory(self.s.locations)}
        project=scope[0] if scope else None if Path(root).resolve() in unfiled else task['project_id']
        goal=task['spec']['goal']
        if len(goal)>16000:goal=goal[:15900]+'\n完整原始目标见关联执行记录：'+task['id']
        return self.s.work_items.create(db,{'title':task['title'],'goal':goal,
            'acceptance':task['spec'].get('acceptance',''),'project_id':project})['work_item']

    def complete(self,db,kind,payload,receipt,ref):
        from .work_coordinator_handoff_text_rework_contracts import SEND as text_review_send
        launches=receipt.get('launch_attempts',[]) or ([receipt['launch_attempt']] if receipt.get('launch_attempt') else [])
        if ref and not launches:raise ValueError('任务接续引用仅用于实际工作派发。')
        bindings=[];new_tasks={self.s.store.attempt(a,db)['task_id'] for a in launches}
        for identity in launches:
            attempt=self.s.store.attempt(identity,db);task=self.s.store.task(attempt['task_id'],db)
            if task['spec'].get('interaction')=='chat':continue
            unit_run=self.s.work_units.reservation(db,identity) if kind in {'work-item.unit-send',text_review_send} else None
            if kind in {'work-item.unit-send',text_review_send} and not unit_run:raise ValueError('合作单元派发许可缺失。')
            item=self.resolve(db,task,receipt,ref);self.guard(db,item,new_tasks,unit_run=unit_run)
            if any(b['id']==item['id'] for b in bindings):raise ValueError('同一业务任务的并行工作单元尚未登记，未派发。')
            # The source handler validates continuation on the same execution.
            # This check covers other executions already linked to the work item.
            if unit_run is None and not payload.get('task_id') and ref and payload.get('project_id')!=item['project_id']:
                raise ValueError('接续草稿的项目与工作任务不一致，请重新准备。')
            if ref and not payload.get('task_id'):
                db.execute('UPDATE tasks SET title=? WHERE id=?',(item['title'],task['id']))
            for record_kind,record_id in [('task',task['id']),*([('dialogue',receipt['dialogue_id'])] if receipt.get('dialogue_id') else [])]:
                if not self.references(db,record_kind,record_id):
                    db.execute('INSERT OR IGNORE INTO record_scopes VALUES(?,?,?)',(record_kind,record_id,item['project_id']))
                with_record=self.s.work_items.record(db,record_kind,record_id)
                item=self.s.work_items.link(db,{'id':item['id'],'revision':item['revision'],'kind':record_kind,
                    'record_id':record_id,'record_revision':with_record['revision']})['work_item']
            record={'id':'dispatch:'+identity,'item_id':item['id'],'item_revision':item['revision'],
                'task_id':task['id'],'attempt_id':identity,'session_id':attempt['session_id'],
                'command_id':self.s.command_identity,'command_kind':kind,'spec_hash':task['spec_hash'],
                'provider':task['spec']['provider'],
                'credential_revision':self.s.resources.context(task['spec']['execution_connection']).credential_revision if task['spec'].get('execution_connection') else self.s.locations.credential_revision,
                'execution_connection':task['spec'].get('execution_connection'),
                'execution_resource':task['spec'].get('execution_resource'),
                'cooperative':task['spec'].get('cooperative'),
                'constraints':snapshot(task,attempt),
                'responsibility':'OFFICIAL_RUNTIME' if task['spec'].get('runtime_mode')=='official' else 'WORKDESK_EXECUTION',
                'handoff_id':ref.get('handoff_id') if ref else None,'created':time.time()}
            self.s.collaboration.put(db,'work_dispatch',record)
            self.s.store.event(task['id'],identity,'work_dispatch_bound',record,db)
            if record['handoff_id']:
                handoff=self.s.collaboration.get(db,'work_handoff',record['handoff_id'])
                handoff['dispatch_id']=record['id'];self.s.collaboration.put(db,'work_handoff',handoff)
            bindings.append({'id':item['id'],'revision':item['revision'],'dispatch_id':record['id']})
        return {**receipt,**({'work_item':bindings[0],'work_items':bindings} if bindings else {})}
