"""Serial queue lifecycle; restart never silently resumes dispatch.

Internal command component; receives the same service/collaboration owner.
Public API, persisted records and grants are unchanged.
"""
import json
import threading
import time
import uuid
from . import workspace
from .store import digest, uid
from .contracts import text


class CollaborationScheduler:
    def finished(self,db,task,attempt,result):
        attempt=self.store.attempt(attempt['id'],db)  # Actual native thread id may replace the reserved session UUID.
        row=db.execute("SELECT data FROM collaboration WHERE kind='link' AND id=?",(task['id'],)).fetchone()
        if not row:return
        link=json.loads(row[0]);link.update(finished=True,attempt_id=attempt['id'],state=result['state'],session_id=attempt['session_id'])
        if link['kind']=='dialogue':
            d=self.get(db,'dialogue',link['dialogue_id'])
            if d['pending']==task['id']:
                d['messages'].append({'id':uid(),'role':'coordinator','content':result.get('answer',''),
                    'task_id':task['id'],'attempt_id':attempt['id'],'state':result['state'],'at':time.time(),
                    'proposal_only':True,'context_mode':'explicit transcript in fresh session'})
                d['pending']=None;d['revision']+=1;self.put(db,'dialogue',d)
        else:
            c=self.get(db,'candidate',link['candidate_id'])
            if link['kind']=='check':
                link.update(applicable=False,outcome='NOT_APPLICABLE',observed_validation=(result.get('validation') or {}).get('status','UNKNOWN'))
                try:
                    self.candidate_current(db,c)
                    if result.get('state')!='COMPLETED' or not result.get('known_tools_exited') or (result.get('owned_job') or {}).get('active_processes')!=0:raise ValueError('派生检查没有正常终态和退出证据。')
                    if db.execute("SELECT 1 FROM requests WHERE attempt_id=? AND state='UNKNOWN'",(attempt['id'],)).fetchone():raise ValueError('派生检查仍有未知模型回执。')
                    if task['spec']['materials']!=c['manifest'] or result.get('manifest')!=c['manifest'] or task['spec']['writable']:raise ValueError('派生检查候选材料不符或发生改写。')
                    if (result.get('native_scope_check') or {}).get('violations'):raise ValueError('派生检查存在副本范围违规。')
                    if workspace.current_manifest(attempt['workspace'],c['manifest'])!=c['manifest']:raise ValueError('派生检查副本已变化。')
                    link.update(applicable=True,outcome=link['observed_validation'])
                except (ValueError,OSError,KeyError) as exc:link['inapplicable_reason']=str(exc)
            else:
                link.update(verdict='UNKNOWN',independent=False,findings='未得到适用的结构化审查结论。')
                try:
                    self.candidate_current(db,c)
                    answer=result.get('answer','').strip()
                    if answer.startswith('```') and answer.endswith('```'):answer=answer.split('\n',1)[1].rsplit('```',1)[0].strip()
                    verdict=json.loads(answer)
                    if not isinstance(verdict,dict):raise ValueError('审查回复须为单一JSON对象。')
                    if result['state']!='COMPLETED' or verdict.get('candidate_id')!=c['id'] or verdict.get('verdict') not in {'pass','return'}:raise ValueError('审查结果/候选版本不符。')
                    if attempt['session_id']==c['source_session'] or attempt['id']==c['attempt_id']:raise ValueError('执行会话自检不能替代独立审查。')
                    if task['spec']['materials']!=c['manifest'] or task['spec']['role']!='reviewer':raise ValueError('审查实际材料不符。')
                    from .cooperation import native,native_review_evidence
                    if native(task['spec']):link['material_delivery_evidence']=native_review_evidence(db,task,attempt,link,c)
                    else:
                        receipts=[]
                        for event in self.store.events(task['id'],limit=500,db=db):
                            payload=event['payload']
                            if event['attempt_id']==attempt['id'] and event['kind']=='tool_completed' and payload.get('name')=='read_file':receipts.append(payload.get('result',{}))
                        observed=workspace.confirmed_reads(receipts,c['manifest'])
                        if observed!=c['manifest']:raise ValueError('审查者未实际读取全部准确候选材料，结论不计为适用独立审查。')
                    link.update(verdict=verdict['verdict'],findings=text(verdict.get('findings')),independent=True,
                         reviewer_provider=task['spec']['provider'],reviewer_model=task['spec']['model'],
                         same_model=task['spec']['model']==self.store.task(c['task_id'],db)['spec']['model'],
                         independence='fresh session; same backend/tools; not independent infrastructure or Human acceptance')
                except (ValueError,TypeError,KeyError) as exc:link['findings']=str(exc)
        self.put(db,'link',link)

    def start_scheduler(self):
        if self.thread is not None:return
        self.thread=threading.Thread(target=self._loop,daemon=True,name='workdesk-serial-queue');self.thread.start()

    def _loop(self):
        while not self.stop.wait(.3):
            try:self.tick()
            except Exception as exc:
                # Persist a bounded diagnostic; never swallow a scheduling failure and claim progress.
                try:
                    with self.store.write() as db:self.put(db,'scheduler',{'id':'last-scheduler-error','error':str(exc)[:1200],'at':time.time(),'no_retry_of_unknown':True})
                except Exception:pass

    def tick(self):
        self.s.management.tick()
        self.s.cooperation_conversation.tick()
        self.s.fixed_preparation.tick()
        self.s.leader_preparation.tick()
        self.s.host_preparation.tick()
        if hasattr(self.s,'work_flows'):self.s.work_flows.tick()
        if hasattr(self.s,'planning'):self.s.planning.tick()
        if hasattr(self.s,'ui_features'):self.s.ui_features.tick()
        with self.s.lock:
            if self.s.retiring or (self.s.locations.data/'retired-target.json').exists():return
            if any(w['thread'].is_alive() for w in self.s.workers.values()):return
            with self.store.read() as db:plans=self.all(db,'plan')
            for plan in reversed(plans):
                if plan['queue_state']!='RUNNING':continue
                command=None
                with self.store.write() as db:
                    try:self.current_plan(db,{'plan_id':plan['id'],'version':plan['version']})
                    except ValueError as exc:
                        plan.update(queue_state='PAUSED',pause_reason=str(exc));self.put(db,'plan',plan);continue
                    views=self.items_view(db,plan)
                    for item in views:
                        if item['complete']:continue
                        if item['blocked']:
                            if item['temporary']:break
                            continue
                        task=self.store.task(item['task_id'],db);attempts=self.store.attempts(task['id'])
                        latest=attempts[-1] if attempts else None
                        if not latest:
                            if item.get('input_from') and not item.get('input_materialized'):
                                stored=next(i for i in plan['items'] if i['key']==item['key'])
                                self.materialize(db,plan,stored);break
                            command=('task.start',{'task_id':task['id'],'spec_hash':task['spec_hash']})
                        elif latest['state']=='COMPLETED':
                            c=self.latest_candidate(db,task['id'])
                            if not c or c['attempt_id']!=latest['id']:
                                self.capture(db,{'task_id':task['id'],'attempt_id':latest['id'],'owner':latest['owner']});break
                            if not self.checks_pass(db,c):
                                if (latest['result']['validation']['status']=='CHECKS_FAILED' and len(attempts)<=item['max_repairs']):
                                    command=('task.resume',{'task_id':task['id'],'spec_hash':task['spec_hash'],'mode':'context',
                                               'note':'The actual approved checks failed. Diagnose and repair only within the original scope; rerun the checks. Repair budget is shared.'})
                            elif item['reviewer']:
                                reviews=[self.get(db,'link',i) for i in c['reviews']]
                                if not reviews:command=('candidate.review',{'candidate_id':c['id']})
                                elif reviews[-1].get('verdict')=='return' and len(attempts)<=item['max_repairs']:
                                    command=('task.resume',{'task_id':task['id'],'spec_hash':task['spec_hash'],'mode':'context',
                                               'note':'Independent review returned this exact candidate: '+reviews[-1]['findings']+'. Repair within scope and rerun approved checks.'})
                        # Unknowns, missing checks, budget exhaustion and unclassified failures never auto retry.
                        if command or item['temporary']:break
                    if all(i['complete'] for i in views):plan['queue_state']='COMPLETE';self.put(db,'plan',plan)
                if command:
                    kind,payload=command
                    # Identity includes the current attempt/candidate frontier; repeated ticks cannot add execution.
                    with self.store.read() as db:
                        frontier=[(i['task_id'],db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?',(i['task_id'],)).fetchone()[0]) for i in plan['items']]
                    identity=str(uuid.uuid5(uuid.NAMESPACE_URL,digest({'plan':plan['id'],'version':plan['version'],'kind':kind,'payload':payload,'frontier':frontier})))
                    try:self.s.command(kind,payload,identity)
                    except (ValueError,OSError,LookupError) as exc:
                        with self.store.write() as db:
                            current=self.get(db,'plan',plan['id']);current.update(queue_state='PAUSED',pause_reason=str(exc));self.put(db,'plan',current)
                    return

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=3)
