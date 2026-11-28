"""Start, stop, reconcile and collect explicitly authorized task attempts.

Internal command component; receives the same service/collaboration owner.
Public API, persisted records and grants are unchanged.
"""
import json
from pathlib import Path
import time
from . import workspace
from .adapters import codex_run, sdk_run
from .paths import atomic_json, implementation_matches
from .store import ACTIVE, TERMINAL, UNKNOWN, canonical, uid
from .tools import ToolRunner
from .reconcile import inspect as inspect_attempt
from .contracts import CAMPAIGN_LIMITS, NATIVE_CAMPAIGN, NATIVE_LIMITS, READ_TOOLS


class TaskExecutionCommands:
    def _start(self,db,payload,resume_mode=None):
        if self.store.task(payload.get('task_id'),db)['spec'].get('runtime_mode')=='official':
            raise ValueError('独立Work直接发送消息，请使用independent.send。')
        if not implementation_matches(self.locations,self.implementation):raise ValueError('后端程序已修改；先重新加载空闲控制服务再派发，原任务不会重发。')
        task=self.store.task(payload.get('task_id'),db);spec=task['spec'];grant=task['grant']
        if task['state'] in {'CANCELLED','HANDED_OFF'}:raise ValueError('任务已取消或转交；不能继续旧执行。')
        from .attachments import validate as validate_attachments
        validate_attachments([dict(i) for i in spec.get('attachments',[])],spec['provider'],spec.get('execution','scoped'),spec.get('model_selection',{}),spec['budget'])
        if spec['provider']!='codex':
            from .model_catalog import EDITION
            if (spec.get('model_selection') or {}).get('entry',{}).get('edition')!=EDITION:
                raise ValueError('API任务缺少当前价格参考版快照；保留旧任务，使用模型切换入口重新冻结目录并确认范围，不自动改价或派发。')
        if spec['campaign'] and db.execute('SELECT 1 FROM meta WHERE key=?',('campaign_closed:'+spec['campaign'],)).fetchone():raise ValueError('本有限实施验证已封闭；普通新任务须另行明确独立范围，不重开旧包。')
        self.operations.guard_task(db,task['id']);self.persistence.guard_resume(db,task,resume_mode)
        self.collaboration.guard_start(db,task)
        if payload.get('spec_hash')!=task['spec_hash']:raise ValueError('规格指纹不一致，未派发。')
        if not grant or grant['spec_hash']!=task['spec_hash'] or grant['expires_at']<=time.time():raise ValueError('授权缺失、版本不符或已过期。')
        if grant.get('credential_selection_revision')!=self.locations.credential_revision:
            raise ValueError('账号引用已改变；原任务须重新确认范围，不自动派发。')
        source=self.locations.data/'tasks'/task['id']/'input'
        if workspace.current_manifest(source,spec['materials'])!=spec['materials']:raise ValueError('原始材料快照已变化，未派发。')
        # First batch intentionally serial. Ownership is not an OS sandbox.
        active=db.execute('SELECT * FROM attempts a WHERE started=(SELECT MAX(started) FROM attempts WHERE task_id=a.task_id)').fetchall()
        self.operations.concurrency_guard(db,task,active)
        old=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        prior=self.store.attempt_row(old) if old else None
        if resume_mode and prior is None:raise ValueError('没有可接续的原尝试。')
        if resume_mode=='native' and prior:
            saved=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(prior['id'],)).fetchone()
            old_selection=json.loads(saved[0]).get('credential_selection_revision') if saved else None
            if old_selection!=self.locations.credential_revision:
                raise ValueError('账号引用已改变；旧账号原生会话不得交给新选择恢复。可核对后明确使用准确材料的新上下文。')
        if spec.get('execution','scoped')!='scoped' and resume_mode=='native' and prior:
            reserved=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(prior['id'],)).fetchone()
            if reserved and json.loads(reserved[0]).get('implementation_revision')!=self.implementation:
                raise ValueError('原生会话绑定的实现已变化；核对后须准确材料的新会话，不复用旧审查上下文。')
        if resume_mode=='native' and spec['provider']=='codex' and prior:
            reservation=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(prior['id'],)).fetchone()
            original=json.loads(reservation[0]).get('codex_profile_reference') if reservation else None
            if not isinstance(original,str) or not original:
                raise ValueError('原生 Codex 会话缺少已登记的账号目录；不猜测或恢复旧配置。')
            if Path(original).resolve()!=self.locations.codex_home.resolve():
                raise ValueError('Codex账号配置已切换；旧账号原生会话不交给新账号恢复。核对后可明确选择准确材料的新上下文尝试。')
        if not prior and task['state']!='APPROVED':raise ValueError('任务尚未批准。')
        if prior and not resume_mode:raise ValueError('已有尝试，请显式选择恢复或接续，不重发原命令。')
        local_proof=bool(prior and ((prior['reconciliation'] or {}).get('known_local_scopes_exited') or
                         prior['result'] and prior['result'].get('known_tools_exited') and
                         (prior['result'].get('owned_job') or {}).get('active_processes')==0))
        if prior and not local_proof:
            raise ValueError('旧运行/已知工具退出证据不足，先核对，不替换执行。')
        if resume_mode=='native' and (spec['provider']=='deepseek' or not prior['session_id']):
            raise ValueError('本工作端不支持原会话恢复；只能在原执行确认停止后显式选择新会话接续。')
        usage=self.store.usage(task=task['id'],db=db)
        unknown_prior=bool(prior and (prior['state'] in UNKNOWN or usage['unknown_requests'] or
                          prior['state']=='STOPPED_RUNTIME' and (prior['result'] or {}).get('remote_model_state')=='UNKNOWN'))
        acknowledged=bool(unknown_prior and resume_mode=='context' and payload.get('ack_unknown_remote') is True
                          and payload.get('note') and (prior['reconciliation'] or {}).get('known_local_scopes_exited'))
        native_reconciled=bool(unknown_prior and resume_mode=='native' and spec['provider']=='codex'
                               and (prior['reconciliation'] or {}).get('protocol_terminal') in {'completed','failed','interrupted'}
                               and (prior['reconciliation'] or {}).get('known_local_scopes_exited'))
        if unknown_prior and not (acknowledged or native_reconciled):
            raise ValueError('旧远端/发送状态仍未知；须先核对本地退出，再显式确认新会话接续；不自动重发。')
        if usage['attempts']>=spec['budget']['attempts']:
            raise ValueError('尝试预算已到或有模型请求未知，未启动。')
        self.store.chain_guard(task['id'],db,'attempts',1)
        self.store.chain_guard(task['id'],db,'seconds')
        if spec['provider']=='codex':self.store.chain_guard(task['id'],db,'codex',1)
        if spec['campaign']:
            total=self.store.usage(campaign=spec['campaign'],db=db)
            limits=NATIVE_LIMITS if spec['campaign']==NATIVE_CAMPAIGN else CAMPAIGN_LIMITS
            if spec['provider']=='codex' and total['codex_turns']>=limits['codex_turns']:
                raise ValueError('本轮Codex验证回合上限已到。')
            if spec['campaign']==NATIVE_CAMPAIGN:
                from .native import guard_campaign
                guard_campaign(self,db,task)
        from .project_usage import check_native_budget
        check_native_budget(db, task)
        folder=self.locations.data/'tasks'/task['id'];working=folder/'worktree'
        if prior:
            baseline=workspace.current_manifest(working,set(spec['materials'])|set(spec['writable']))
            saved=(prior['result'] or {}).get('manifest')
            if saved is None:saved=(prior['reconciliation'] or {}).get('observed_manifest')
            if baseline!=saved:raise ValueError('原副本在上次收尾后变化，须新建材料版本与授权。')
        else:
            if working.exists():
                if workspace.current_manifest(working,spec['materials'])!=spec['materials'] or any(p.is_file() and p.relative_to(working).as_posix() not in spec['materials'] for p in working.rglob('*')):
                    raise ValueError('上次未提交的工作副本不匹配原材料，须核对。')
            else:workspace.copy_manifest(folder/'input',working,spec['materials'])
            baseline=spec['materials']
        identity=uid();owner=uid();session=prior['session_id'] if prior and resume_mode=='native' else uid()
        now=time.time();note=payload.get('note','');seconds=spec['budget']['seconds']
        member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(task['id'],)).fetchone()
        if member:
            chain=self.collaboration.get(db,'chain',member[0]);used=self.store.usage(chain=member[0],db=db)
            reserved=db.execute('SELECT a.deadline FROM attempts a JOIN chain_members m ON a.task_id=m.task_id WHERE m.chain_id=? AND a.state IN ('+','.join('?' for _ in ACTIVE)+')',(member[0],*ACTIVE)).fetchall()
            future=sum(max(0,r[0]-now) for r in reserved)
            seconds=min(seconds,chain['budget']['seconds']-used['elapsed_observed_seconds']-future)
            if seconds<1:raise ValueError('问题链累计时间预算不足。')
        if not isinstance(note,str) or len(note)>4000:raise ValueError('接续说明过长。')
        allowed_tools=payload.get('allowed_tools',['list_files','read_file','search_files','write_file','run_check'])
        if spec.get('execution','scoped')!='scoped' and 'allowed_tools' in payload:
            raise ValueError('原生路径不能使用Workdesk五工具选择器收窄；请修订任务权限形成新版本。')
        if not isinstance(allowed_tools,list) or any(t not in {'list_files','read_file','search_files','write_file','run_check'} for t in allowed_tools):
            raise ValueError('本次工具范围不支持。')
        role_scope=READ_TOOLS if spec['role'] in {'coordinator','reviewer'} else READ_TOOLS+['run_check'] if spec['role']=='self_check' else None
        if role_scope is not None:allowed_tools=[t for t in allowed_tools if t in role_scope]
        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (identity,task['id'],uid(),owner,'RESERVED',now,now+seconds,str(working),
                    session,None,0,None,None,canonical(baseline),None,self.instance))
        self.collaboration.started(db,task['id'],identity)
        # Preserve frozen evidence before a reused working copy can change again.
        self.store.event(task['id'],identity,'execution_reserved',{'owner':owner,'spec_hash':task['spec_hash'],
            'configuration_snapshot':{'status':'FROZEN','spec_hash':task['spec_hash'],**{k:spec[k] for k in ('provider','model','effort','tier')},
                                      'model_selection':spec.get('model_selection')},
            'implementation_revision':self.implementation,'baseline':baseline,'resume_mode':resume_mode,'allowed_tools':allowed_tools,'previous_attempt':prior['id'] if prior else None,'human_note':note,
            'codex_profile_reference':str(self.locations.codex_home) if spec['provider']=='codex' else None,
            'credential_selection_revision':self.locations.credential_revision},db)
        db.execute("UPDATE tasks SET state='RESERVED',updated=? WHERE id=?",(now,task['id']))
        return {'task_id':task['id'],'launch_attempt':identity,'state':'RESERVED','resume_mode':resume_mode,'note':note}

    def _resume(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        if task['state']=='PAUSED' and not db.execute('SELECT 1 FROM attempts WHERE task_id=?',(task['id'],)).fetchone():
            value='APPROVED' if task['grant'] and task['grant']['expires_at']>time.time() else 'DRAFT'
            db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(value,time.time(),task['id']))
            self.store.event(task['id'],None,'pending_task_unpaused',{'state':value,'no_dispatch':True},db)
            return {'task_id':task['id'],'state':value,'no_dispatch':True}
        mode=payload.get('mode','native')
        if mode not in {'native','context'}:raise ValueError('恢复方式不支持。')
        return self._start(db,payload,mode)

    def _latest(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        row=db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started DESC LIMIT 1',(task['id'],)).fetchone()
        if row is None:return task,None
        attempt=self.store.attempt_row(row)
        if payload.get('attempt_id')!=attempt['id'] or payload.get('owner')!=attempt['owner']:
            raise ValueError('尝试或执行归属不一致，未操作。')
        return task,attempt

    def _stop(self,db,payload):
        task,attempt=self._latest(db,payload)
        if not attempt:return {'task_id':task['id'],'registered':False,'reason':'没有运行尝试。'}
        if attempt['state'] in TERMINAL:return {'task_id':task['id'],'registered':False,'reason':'原尝试已有保存终态。'}
        worker=self.workers.get(attempt['id']);owned=bool(worker and worker['thread'].is_alive())
        value='STOP_REQUESTED' if owned else 'STOP_UNKNOWN'
        db.execute('UPDATE attempts SET stop_requested=1,state=? WHERE id=?',(value,attempt['id']))
        db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY')",(time.time(),attempt['id']))
        db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(value,time.time(),task['id']))
        self.store.event(task['id'],attempt['id'],'stop_requested',{'stop_confirmed':False,'source':'Human explicit command'},db)
        receipt={'task_id':task['id'],'registered':True,'signal_stop':attempt['id'] if owned else None,'state':value,'stop_confirmed':False}
        if task['spec'].get('execution')=='partner_native' and not owned:
            receipt['stop_partner_task']={'task_id':task['id'],'attempt_id':attempt['id'],'spec_hash':task['spec_hash'],
                                          'run_id':(attempt.get('result') or {}).get('partner_run_id','')}
        return receipt

    def _pause(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        if task['state'] in ACTIVE|UNKNOWN:
            receipt=self._stop(db,payload);receipt['pause_requested']=True
            self.store.event(task['id'],receipt.get('signal_stop'),'pause_requested',{'scope':'当前串行任务；不自动续跑'},db)
            return receipt
        db.execute("UPDATE tasks SET state='PAUSED',updated=? WHERE id=?",(time.time(),task['id']))
        self.store.event(task['id'],None,'pause_requested',{'scope':'未派发任务'},db)
        return {'task_id':task['id'],'state':'PAUSED'}

    def _cancel(self,db,payload):
        task=self.store.task(payload.get('task_id'),db)
        if task['state'] in ACTIVE|UNKNOWN:
            value=self._stop(db,payload)
            self.store.event(task['id'],payload.get('attempt_id'),'cancel_requested',{'stop_confirmed':False},db)
            return {**value,'cancel_requested':True}
        db.execute("UPDATE tasks SET state='CANCELLED',grant=NULL,updated=? WHERE id=?",(time.time(),task['id']))
        self.store.event(task['id'],None,'task_cancelled',{'in_flight_cancel_confirmed':False},db)
        return {'task_id':task['id'],'state':'CANCELLED'}

    def _approval(self,db,payload):
        row=db.execute('SELECT * FROM approvals WHERE id=?',(payload.get('approval_id'),)).fetchone()
        if row is None or row['state']!='PENDING':raise ValueError('审批已处理或不存在。')
        attempt=self.store.attempt(row['attempt_id'],db)
        if attempt['state'] not in ACTIVE or attempt['stop_requested'] or attempt['deadline'] and time.time()>=attempt['deadline']:
            raise ValueError('任务已停止或期限已到，未批准。')
        if type(payload.get('allow')) is not bool:raise ValueError('必须明确批准或拒绝。')
        value='APPROVED' if payload['allow'] else 'DENIED'
        db.execute('UPDATE approvals SET state=?,resolved=? WHERE id=?',(value,time.time(),row['id']))
        self.store.event(row['task_id'],row['attempt_id'],'human_tool_decision',{'approval_id':row['id'],'state':value},db)
        return {'approval_id':row['id'],'state':value}

    def _reconcile(self,db,payload):
        task,attempt=self._latest(db,payload)
        if not attempt:return {'task_id':task['id'],'state':task['state'],'no_resend':True}
        worker=self.workers.get(attempt['id']);active=bool(worker and worker['thread'].is_alive())
        report=inspect_attempt(self,task,attempt,db,active,payload.get('probe_codex') is True)
        report['model_request_usage']=self.store.usage(task=task['id'],db=db)
        recovered=report.pop('recovered_frozen_result',None)
        rejected=report.pop('recovered_rejected_result',None)
        if rejected:recovered=rejected
        if not self.operations.is_current(db,task['id']):
            report['historical_only']=True
            db.execute('UPDATE attempts SET reconciliation=? WHERE id=?',(canonical(report),attempt['id']))
            self.store.event(task['id'],attempt['id'],'retired_owner_reconciliation',report,db)
            return report
        if recovered and recovered.get('state') in TERMINAL and not active and report['known_local_scopes_exited']:
            if task['spec'].get('execution')=='partner_native' and recovered.get('session_id'):
                db.execute('UPDATE attempts SET session_id=? WHERE id=?',(recovered['session_id'],attempt['id']))
            db.execute('UPDATE attempts SET result=?,state=? WHERE id=?',(canonical(recovered),recovered['state'],attempt['id']))
            db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(recovered['state'],time.time(),task['id']))
            report['recovered_state']=recovered['state']
        if (task['spec'].get('runtime_mode')=='official' and not active and report['known_local_scopes_exited']
                and (report.get('exact_turn') or {}).get('account_verified') is True
                and report.get('protocol_terminal') in {'completed','failed','interrupted'}
                and (attempt['state'] in UNKNOWN or (attempt['result'] or {}).get('manual_close'))):
            # A separate disposition follows exact read-only evidence. Keep the
            # frozen result and original unknown delivery observation intact.
            state={'completed':'COMPLETED','failed':'FAILED','interrupted':'INTERRUPTED'}[report['protocol_terminal']]
            db.execute('UPDATE attempts SET state=? WHERE id=?',(state,attempt['id']))
            db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(state,time.time(),task['id']))
            report['recovered_state']=state;report['frozen_result_preserved']=True
        db.execute('UPDATE attempts SET reconciliation=? WHERE id=?',(canonical(report),attempt['id']))
        self.store.event(task['id'],attempt['id'],'read_only_reconciliation',report,db)
        return report

    def _run(self,identity,stop,resume_mode,note):
        attempt=self.store.attempt(identity);task=self.store.task(attempt['task_id'])
        if task['spec'].get('runtime_mode')=='official':
            from .official_work import run
            try:return run(self,identity,stop,resume_mode,note)
            finally:
                from .scene_runtime import finish
                finish(self.store,task,attempt)
        tools=ToolRunner(self.store,self.locations,task,attempt,stop);folder=self.locations.data/'tasks'/task['id']
        prior=[a for a in self.store.attempts(task['id']) if a['id']!=identity]
        resume=prior[-1] if prior and resume_mode=='native' else None
        result=None;adapter_entered=False
        try:
            from .attachments import prepare as prepare_attachments
            prepare_attachments(self.locations,self.store,task,attempt,stop,tools.execution)
            if self.engine:
                adapter_entered=True;result=self.engine(self,task,attempt,tools,stop,resume,note)
            elif task['spec']['provider']=='codex':
                adapter_entered=True
                if task['spec'].get('execution')=='codex_native':
                    from .native import run
                    result=run(self.locations,self.store,task,attempt,stop,resume,note,control_lock=self.lock)
                else:result=codex_run(self.locations,self.store,task,attempt,tools,stop,resume,note)
            else:
                secret=self.profiles.credentials().get(task['spec']['provider'])
                if secret is None:raise ValueError('本地指定凭据缺失。')
                if task['spec']['provider']=='qwen' and secret.billing_kind!='general_api':raise ValueError('当前仅支持通用API Key。')
                adapter_entered=True;result=sdk_run(self.locations,self.store,task,attempt,tools,stop,secret,CAMPAIGN_LIMITS,resume,note,control_lock=self.lock)
            from .cooperation import run_checks
            run_checks(self,task,attempt,tools,stop,result)
        except Exception as exc:
            with self.store.read() as db:
                sent=db.execute("SELECT 1 FROM requests WHERE attempt_id=? UNION SELECT 1 FROM events WHERE attempt_id=? AND kind='turn_send_intent'",(identity,identity)).fetchone()
            result={'state':'SEND_UNKNOWN' if sent else 'FAILED_NO_SEND','answer':'',
                    'errors':[{'class':type(exc).__name__,'message':str(exc)[:1200]}],
                    'owned_job':None if adapter_entered else {'active_processes':0,'no_process_started':True},
                    'known_tools_exited':not adapter_entered,'remote_model_state':'UNKNOWN' if adapter_entered else 'NOT_SENT','protocol':'UNKNOWN'}
        finally:
            stop.set()
            if result is None:result={'state':'SEND_UNKNOWN','answer':'','errors':[{'message':'missing worker result'}]}
            try:
                if task['spec'].get('execution','scoped')!='scoped':
                    from .native import inspect_outputs
                    from .pdf_visual import manifest as pdf_manifest,image_metadata
                    result['native_scope_check']=inspect_outputs(attempt['workspace'],task['spec'],pdf_manifest(attempt))
                    if pdf_manifest(attempt):result['prepared_pdf_images']=image_metadata(attempt)
                    if result['native_scope_check']['violations']:
                        result['state']='FAILED';result.setdefault('errors',[]).append({'native_scope_violation':result['native_scope_check']['violations']})
                manifest=workspace.current_manifest(attempt['workspace'],set(task['spec']['materials'])|set(task['spec']['writable']))
                result['manifest']=manifest
                base=task['spec'].get('integration_base')
                result['diffs']=workspace.differences(folder/('integration-base' if base is not None else 'input'),Path(attempt['workspace']),base if base is not None else task['spec']['materials'],task['spec']['writable'])
                evidence=folder/'attempts'/identity;evidence.mkdir(parents=True,exist_ok=True)
                for name in manifest:
                    path=workspace.io_path(evidence/'files'/name);path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(workspace.read_blob(attempt['workspace'],name)['raw'])
            except Exception as exc:
                result.setdefault('errors',[]).append({'manifest_error':str(exc)});result['state']='SEND_UNKNOWN'
            if result['state']=='COMPLETED' and (not result.get('known_tools_exited') or
                     (result.get('owned_job') or {}).get('active_processes',1)!=0):result['state']='SEND_UNKNOWN'
            with self.store.read() as db:
                checks=[json.loads(r['payload']) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='check_process_exited' ORDER BY seq",(identity,))]
            latest_checks={r['check_id']:r for r in checks}
            required={c['id'] for c in task['spec']['checks']}
            result['validation']={'status':'CHECKS_FAILED' if any(r['exit_code']!=0 or r.get('cancelled') for r in latest_checks.values()) else
                                  'CHECKS_PASSED_PENDING_REVIEW' if required and required<=latest_checks.keys() else 'NOT_FULLY_CHECKED',
                                  'latest_checks':{k:{'exit_code':v['exit_code'],'cancelled':v.get('cancelled')} for k,v in latest_checks.items()},
                                  'acceptance':'NOT_PERFORMED','not_protocol_status':True}
            if result.get('controller_checks_error'):result['validation'].update(status='CHECKS_FAILED',controller_error=result['controller_checks_error'])
            if task['spec'].get('controller_checks'):result['validation']['check_source']='EXACT_APPROVED_ARGV_AFTER_NATIVE_TURN'
            if task['spec'].get('execution')=='codex_native':
                with self.store.read() as db:
                    items=[json.loads(r[0]) for r in db.execute('SELECT result FROM tool_calls WHERE attempt_id=? AND result IS NOT NULL',(identity,))]
                result['validation']['native_command_observations']=[{'id':i.get('id'),'command':i.get('command'),'exit_code':i.get('exitCode'),'status':i.get('status')} for i in items if i.get('type')=='commandExecution']
                if not task['spec'].get('controller_checks'):result['validation']['status']='NATIVE_COMMAND_RESULTS_SAVED_PENDING_ACCEPTANCE_CHECK'
            result.update(attempt_id=identity,task_id=task['id'],owner=attempt['owner'],ended_at=time.time(),independent_review=False,human_acceptance=False,
                          continuation_mode=resume_mode,session_memory_claim='NOT_INDEPENDENTLY_ATTESTED',
                          implementation_revision=self.implementation,
                          specification_sha256=task['spec_hash'],os_isolation='NOT_PROVIDED',
                          check_network_isolation='UNKNOWN',remaining_quota='UNKNOWN')
            from .attachments import summary as attachment_summary
            result['attachments']=attachment_summary(self.store,task,attempt)
            try:atomic_json(folder/'attempts'/identity/'result.json',result)
            except Exception as exc:
                result['state']='SEND_UNKNOWN';result.setdefault('errors',[]).append({'artifact_save_error':str(exc)})
            with self.store.write() as db:
                if not self.operations.is_current(db,task['id']):
                    result['historical_only']=True
                    try:atomic_json(folder/'attempts'/identity/'result.json',result)
                    except Exception as exc:
                        result['state']='SEND_UNKNOWN';result.setdefault('errors',[]).append({'artifact_save_error':str(exc)})
                    db.execute('UPDATE attempts SET state=?,result=? WHERE id=?',(result['state'],canonical(result),identity))
                    self.store.event(task['id'],identity,'late_result_historical_only',result,db);return
                self.store.update_attempt(identity,result['state'],db=db,result=result)
                db.execute("UPDATE approvals SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state='PENDING'",(time.time(),identity))
                db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY','REPLY_INTENT')",(time.time(),identity))
                intentions=db.execute("SELECT kind FROM events WHERE attempt_id=? AND kind IN ('pause_requested','cancel_requested') ORDER BY seq DESC LIMIT 1",(identity,)).fetchone()
                if intentions and result['state'] in TERMINAL and result.get('known_tools_exited') and (result.get('owned_job') or {}).get('active_processes')==0:
                    projected='CANCELLED' if intentions['kind']=='cancel_requested' else 'PAUSED'
                    db.execute('UPDATE tasks SET state=?,updated=? WHERE id=?',(projected,time.time(),task['id']))
                self.store.event(task['id'],identity,'attempt_result_saved',result,db)
                self.collaboration.finished(db,task,attempt,result)
                self.operations.finished(db,task)
