"""UI-independent task commands, ownership, scoped execution and reconciliation."""
import json
from contextlib import nullcontext
import threading
import time
from . import PROTOCOL_VERSION
from .paths import Locations, freeze_implementation
from .profiles import Profiles
from .store import Store, ACTIVE, digest, uid
from .collaboration import Collaboration
from .operations import Operations
from .persistence import Persistence
from .verification import Verification
from .storage import LocalStorage
from .setup_account import AccountSetup
from .extensions import Extensions
from .contracts import CAMPAIGN, CAMPAIGN_LIMITS, NATIVE_CAMPAIGN, NATIVE_LIMITS, bounded_text, integer
from .task_drafts import TaskDraftCommands
from .task_execution import TaskExecutionCommands


class Service(TaskDraftCommands, TaskExecutionCommands):
    def __init__(self, locations=None, engine=None):
        self.locations=locations or Locations.resolve();self.store=Store(self.locations.data)
        from .bridge import binding
        core = binding() if self.store.managed else None
        self.profiles=Profiles(self.locations,self.store);self.engine=engine
        from .independent import IndependentWork
        self.independent=IndependentWork(self)
        self.implementation=core['implementation'] if core else freeze_implementation(self.locations)
        self.instance=core['process_instance'] if core else uid();self.workers={};self.lock=threading.RLock()
        # Startup is reconciliation only: no model call and no automatic replay.
        if not core:
            with self.store.write() as db:
                rows=db.execute('SELECT * FROM attempts WHERE state IN ('+','.join('?' for _ in ACTIVE)+')',tuple(ACTIVE)).fetchall()
                for row in rows:
                    db.execute("UPDATE attempts SET state='ORPHANED_UNKNOWN' WHERE id=?",(row['id'],))
                    db.execute("UPDATE tasks SET state='ORPHANED_UNKNOWN',updated=? WHERE id=?",(time.time(),row['task_id']))
                    self.store.event(row['task_id'],row['id'],'restart_requires_reconciliation',
                                     {'old_worker_instance':row['worker_instance'],'no_resend':True},db)
                    db.execute("UPDATE approvals SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state='PENDING'",(time.time(),row['id']))
                    db.execute("UPDATE native_requests SET state='CANCELLED',resolved=? WHERE attempt_id=? AND state IN ('PENDING','READY','REPLY_INTENT')",(time.time(),row['id']))
        self.collaboration=Collaboration(self)
        from .work_records import WorkRecords
        self.work_records=WorkRecords(self)
        from .work_items import WorkItems
        self.work_items=WorkItems(self)
        from .dispatch import Dispatch
        self.dispatch=Dispatch(self)
        from .execution_resources import ExecutionResources
        self.resources=ExecutionResources(self)
        from .teamwork import Teamwork
        self.teamwork=Teamwork(self)
        self.operations=Operations(self);self.persistence=Persistence(self);self.retiring=False
        self.local_storage=LocalStorage(self)
        self.local_storage.initialize_library()
        self.local_storage.reconcile()
        self.account_setup=AccountSetup(self)
        self.verification=Verification(self)
        self.extensions=Extensions(self)
        from .capabilities import Capabilities
        from .scenes import Scenes
        self.capabilities=Capabilities(self);self.scenes=Scenes(self)
        from .file_inspector import FileInspector
        from .result_records import Results
        from .runtime_monitor import RuntimeMonitor
        from .resource_space import ResourceSpace
        self.file_inspector=FileInspector(self);self.results=Results(self);self.monitor=RuntimeMonitor(self)
        self.resource_space=ResourceSpace(self.store)
        from .work_runtime import WorkRuntime
        self.work_runtime=WorkRuntime(self)
        from .ui_features import UIFeatures
        self.ui_features=UIFeatures(self)
        from .native_controls import NativeControls
        self.native_controls=NativeControls(self);self.store.native_controls=self.native_controls
        from .planning import Planning
        from .project_manager import Projects
        from .library_manager import Libraries
        self.planning=Planning(self);self.projects_manager=Projects(self);self.libraries=Libraries(self)
        from .work_drivers import WorkDrivers
        self.work_drivers=WorkDrivers(self)
        from .work_units import WorkUnits
        self.work_units=WorkUnits(self)
        from .work_artifacts import WorkArtifacts
        self.work_artifacts=WorkArtifacts(self)
        from .work_leader import WorkLeader
        self.leader=WorkLeader(self)
        from .work_leader_revisions import WorkLeaderRevisions
        self.leader_revisions=WorkLeaderRevisions(self)
        from .work_flows import WorkFlows
        self.work_flows=WorkFlows(self)
        from .work_fixed_preparation import WorkFixedPreparation
        self.fixed_preparation=WorkFixedPreparation(self)
        from .work_leader_preparation import WorkLeaderPreparation
        self.leader_preparation=WorkLeaderPreparation(self)
        from .work_host_experts import WorkHostExperts
        self.host_experts=WorkHostExperts(self)
        from .work_host_experts_preparation import WorkHostExpertsPreparation
        self.host_preparation=WorkHostExpertsPreparation(self)
        from .cooperation_conversation import CooperationConversation
        self.cooperation_conversation=CooperationConversation(self)
        from .work_management import WorkManagement
        self.management=WorkManagement(self)
        from .work_proposals import WorkProposals
        self.work_proposals=WorkProposals(self)
        from .work_transitions import WorkTransitions
        self.work_transitions=WorkTransitions(self)
        from .work_round_revisions import WorkRoundRevisions
        self.work_round_revisions=WorkRoundRevisions(self)
        from .work_rework_diagnostics import WorkReworkDiagnostics
        self.work_rework_diagnostics=WorkReworkDiagnostics(self)
        from .work_file_rework_diagnostics import WorkFileReworkDiagnostics
        self.work_file_rework_diagnostics=WorkFileReworkDiagnostics(self)
        from .work_rework_applications import WorkReworkApplications
        self.work_rework_applications=WorkReworkApplications(self)
        from .work_unit_revision_applications import WorkUnitRevisionApplications
        self.work_unit_revision_applications=WorkUnitRevisionApplications(self)
        from .work_file_rework_applications import WorkFileReworkApplications
        self.work_file_rework_applications=WorkFileReworkApplications(self)
        from .work_file_handoffs import WorkFileHandoffs
        self.work_file_handoffs=WorkFileHandoffs(self)
        from .work_file_handoff_ledger import WorkFileHandoffLedger
        self.work_file_handoff_ledger=WorkFileHandoffLedger(self)
        from .work_file_handoff_apply import WorkFileHandoffRevisionApplications
        self.work_file_handoff_applications=WorkFileHandoffRevisionApplications(self)
        from .work_coordinator_handoff_post_round_v3_restart import reconcile
        self.post_round_v3_restart=reconcile(self)
        if not core:self.collaboration.start_scheduler()

    def health(self):
        return {'protocol':2 if self.store.managed else PROTOCOL_VERSION,'instance':self.instance,'service':'Workdesk task control',
                'implementation':self.implementation,'program_base':str(self.locations.base),
                'display_independent':True,'active_owned_workers':sum(w['thread'].is_alive() for w in self.workers.values())+self.ui_features.chats.api.active(),
                'active_api_chats':self.ui_features.chats.api.active(),'active_native_controls':self.native_controls.active(),
                'active_quota_readers':self.resources.active(),
                'post_round_v3_restart':self.post_round_v3_restart,
                'os_isolation':'NOT_PROVIDED','network_isolation_for_check_commands':'UNKNOWN',
                **({'core_engine':'go','legacy_bridge_protocol':1} if self.store.managed else {})}

    def catalog(self,view=None):
        if view not in (None,'workbench'):raise ValueError('未知工作区目录视图。')
        with self.store.read() as db:
            projects=self.work_records.projects(db)
            visible=self.work_records.visible('task','tasks.id')
            if view=='workbench':
                # The sidebar needs identity and grouping, not every saved goal,
                # material manifest or authorization. Read full specs on task open.
                fields=('interaction','work_scene','work_mode','provider','model')
                selection=','.join("json_extract(spec,'$."+key+"') AS "+key for key in fields)
                tasks=[]
                for row in db.execute("SELECT id,project_id,title,state,created,updated,"+selection+" FROM tasks WHERE coalesce(json_extract(spec,'$.interaction'),'')!='chat' AND "+visible+" ORDER BY created DESC LIMIT 200"):
                    item=dict(row);item['spec']={key:item.pop(key) for key in fields}
                    tasks.append(item)
            else:
                tasks=[self.store.task_row(r) for r in db.execute('SELECT * FROM tasks WHERE '+visible+' ORDER BY created DESC LIMIT 200')]
            approvals=[dict(r) for r in db.execute("SELECT * FROM approvals WHERE state='PENDING' ORDER BY created")]
        from .attachments import choices
        profiles=self.profiles.list()
        return {'protocol':PROTOCOL_VERSION,'health':self.health(),'projects':projects,'tasks':tasks,'profiles':profiles,
                'task_catalog_scope':'RECENT_200; FULL_RECORDS_USE_RECORD_CATALOG',
                'independent_work':self.independent.catalog(profiles),'attachment_capabilities':choices(self.locations),
                'pending_approvals':approvals,'campaign_usage':self.store.usage(campaign=CAMPAIGN),
                'native_campaign_usage':self.store.usage(campaign=NATIVE_CAMPAIGN),'native_campaign_limits':NATIVE_LIMITS,
                'campaign_limits':CAMPAIGN_LIMITS,'current_provider_connections':'UNKNOWN',
                'execution_resources':self.resources.public_catalog(profiles=profiles)}

    def detail(self,identity,cursor=0):
        with self.store.read() as db:
            task=self.store.task(identity,db)
            attempts=[self.store.attempt_row(r) for r in db.execute('SELECT * FROM attempts WHERE task_id=? ORDER BY started',(identity,))]
            reverted={a for r in db.execute("SELECT payload FROM events WHERE task_id=? AND kind='native_history_reverted'",(identity,)) for a in json.loads(r[0]).get('attempt_ids',[])}
            for attempt in attempts:
                attempt['native_history_reverted']=attempt['id'] in reverted
                saved=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",(attempt['id'],)).fetchone()
                attempt['model_configuration']=(json.loads(saved[0]).get('configuration_snapshot') if saved else None) or {'status':'LEGACY_NOT_FROZEN'}
                attempt['served_model_observations']=[json.loads(r['payload']) for r in db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='model_served_configuration_observation' ORDER BY seq",(attempt['id'],))]
            approvals=[dict(r) for r in db.execute('SELECT * FROM approvals WHERE task_id=? ORDER BY created',(identity,))]
            native_requests=[{**dict(r),'params':json.loads(r['params']),'response':json.loads(r['response']) if r['response'] else None} for r in db.execute('SELECT * FROM native_requests WHERE task_id=? ORDER BY created',(identity,))]
            events=self.store.events(identity,cursor,db=db)
            latest=db.execute('SELECT MAX(seq) FROM events WHERE task_id=?',(identity,)).fetchone()[0] or 0
            usage=self.store.usage(task=identity,db=db)
            work_items=[{'id':r['id'],'title':r['title'],'revision':r['revision']} for r in db.execute(
                "SELECT w.id,w.title,w.revision FROM work_items w JOIN work_item_records r ON r.item_id=w.id WHERE r.kind='task' AND r.record_id=?",(identity,))]
        live=[]
        for row in attempts:
            worker=self.workers.get(row['id'])
            live.append({'attempt_id':row['id'],'controller_worker':'OWNED_THREAD_ALIVE' if worker and worker['thread'].is_alive() else 'NOT_OWNED_OR_EXITED',
                         'model_connection':'UNKNOWN','protocol':(row['result'] or {}).get('protocol','见原始结果')})
        with self.store.read() as db:
            candidates=[self.collaboration.candidate_view(db,c) for c in self.collaboration.all(db,'candidate') if c['task_id']==identity]
            member=db.execute('SELECT chain_id FROM chain_members WHERE task_id=?',(identity,)).fetchone()
            chain_usage=self.store.usage(chain=member[0],db=db) if member else None
        independent_service=task['spec'].get('independent_service')
        if task['spec'].get('runtime_mode')=='official':independent_service=self.independent.describe(task['spec']['provider'],task['spec'].get('qwen_runtime'))
        if independent_service and task['spec'].get('cooperative'):independent_service={**independent_service,'scope':'COOPERATIVE_UNIT'}
        if not independent_service and task['spec'].get('work_mode','independent')=='independent' and task['spec'].get('execution','scoped')!='scoped':
            independent_service=self.independent.describe(task['spec']['provider'])
            if task['spec']['provider']=='deepseek':independent_service['native_resume']=False
        return {'task':task,'work_items':work_items,'attempts':attempts,'events':events,'cursor':events[-1]['seq'] if events else int(cursor),
                'native_controls':self._native_control_rows(identity),
                'independent_service':independent_service,
                'has_more_events':bool(latest>(events[-1]['seq'] if events else int(cursor))),
                'approvals':approvals,'native_requests':native_requests,'usage':usage,'runtime_observation':live,
                'candidates':candidates,'chain_usage':chain_usage,
                'independent_review':'SEE_VERSION_BOUND_CANDIDATES' if candidates else 'NOT_PERFORMED',
                'human_acceptance':'SEE_VERSION_BOUND_CANDIDATES' if candidates else 'NOT_RECORDED'}

    def command(self,kind,payload,command_id):
        if not isinstance(payload,dict):raise ValueError('命令正文须为对象。')
        handlers={'project.create':self._project,'task.create':self._create,'task.approve':self._approve,
                  'task.start':self._start,'task.resume':self._resume,'task.stop':self._stop,
                  'task.pause':self._pause,'task.cancel':self._cancel,'approval.resolve':self._approval,
                  'task.reconcile':self._reconcile,'task.revise':self._revise}
        from .native import resolve_request
        handlers['native.resolve']=lambda db,payload:resolve_request(self,db,payload)
        handlers['service.reload']=self._reload
        handlers['independent.create']=self.independent.create
        from .official_work import send as official_send,recover as official_recover
        handlers['independent.send']=self.file_inspector.send
        handlers['independent.qwen-runtime-select']=self.independent.select_qwen
        handlers['independent.recover']=lambda db,payload:official_recover(self,db,payload)
        handlers['native.control']=self.native_controls.request
        handlers['native.access']=self.native_controls.access
        handlers['native.control-stop']=self.native_controls.stop
        from . import workbench
        handlers['work.record']=lambda db,payload:workbench.record(self,db,payload)
        handlers['work.records-organize']=self.work_records.organize
        handlers['work.records-delete']=self.work_records.delete
        handlers['work.records-restore']=self.work_records.restore_deleted
        handlers['work.project-record']=self.work_records.project_record
        handlers['work.projects-remove']=self.work_records.remove_projects
        handlers['work.project-history-delete']=self.work_records.delete_project_history
        handlers['work.configure']=lambda db,payload:workbench.configure(self,db,payload)
        handlers['project.scratch']=lambda db,payload:workbench.scratch(self,db,payload)
        handlers['profile.select']=self.profiles.select
        from .selection import change as change_selection
        handlers['task.select-model']=lambda db,payload:change_selection(self,db,payload)
        from .cooperation import select_mode
        handlers['task.select-work-mode']=lambda db,payload:select_mode(self,db,payload)
        handlers.update(self.collaboration.handlers())
        handlers.update(self.operations.handlers());handlers.update(self.persistence.handlers())
        handlers.update(self.local_storage.handlers())
        handlers.update(self.verification.handlers())
        handlers.update(self.extensions.handlers())
        handlers.update(self.capabilities.handlers());handlers.update(self.scenes.handlers())
        handlers.update(self.file_inspector.handlers());handlers.update(self.results.handlers());handlers.update(self.monitor.handlers())
        handlers.update(self.resource_space.handlers())
        handlers.update(self.ui_features.handlers())
        handlers.update(self.management.handlers())
        handlers.update(self.work_items.handlers())
        handlers.update(self.teamwork.handlers())
        handlers.update(self.work_drivers.handlers())
        handlers.update(self.work_units.handlers())
        handlers.update(self.work_artifacts.handlers())
        handlers.update(self.work_flows.handlers())
        handlers.update(self.fixed_preparation.handlers())
        handlers.update(self.leader_preparation.handlers())
        handlers.update(self.host_preparation.handlers())
        from .work_pdca import START as pdca_start, start as start_pdca
        handlers[pdca_start] = lambda db, payload: start_pdca(self, db, payload)
        from .cooperation_conversation import COMMANDS as cooperation_conversation_commands
        handlers.update(self.cooperation_conversation.handlers())
        handlers.update(self.host_experts.handlers())
        handlers.update(self.leader_revisions.handlers())
        handlers.update(self.work_proposals.handlers())
        handlers.update(self.work_transitions.handlers())
        handlers.update(self.work_round_revisions.handlers())
        handlers.update(self.work_rework_diagnostics.handlers())
        handlers.update(self.work_file_rework_diagnostics.handlers())
        handlers.update(self.work_rework_applications.handlers())
        handlers.update(self.work_file_rework_applications.handlers())
        handlers.update(self.work_file_handoffs.handlers())
        handlers.update(self.work_file_handoff_applications.handlers())
        handlers.update(self.work_unit_revision_applications.handlers())
        from .work_coordinator_handoff_post_round_v3_save_public import COMMAND as v3_save_kind, save as v3_save, request_shape as v3_request_shape
        handlers[v3_save_kind]=lambda db,p:v3_save(self,db,p)
        from .work_coordinator_handoff_post_round_v3_responsibility_records import COMMAND as v3_responsibility_kind, request_shape as v3_responsibility_shape
        from .work_coordinator_handoff_post_round_v3_responsibility_apply import WorkCoordinatorHandoffPostRoundV3ResponsibilityApplications
        handlers[v3_responsibility_kind]=lambda db,p:WorkCoordinatorHandoffPostRoundV3ResponsibilityApplications(self).change(db,p)
        from .work_coordinator_handoff_text_rework_contracts import SEND as text_review_send, DRIVER_CHANGE as text_driver_change
        from .work_coordinator_handoff_text_rework_commands import reserve_review
        from .work_coordinator_handoff_text_rework_responsibility import change as text_change
        handlers[text_review_send] = lambda db, p: reserve_review(self, db, p)
        handlers[text_driver_change] = lambda db, p: text_change(self, db, p)
        handlers['work-item.control']=self.work_runtime.hold
        handlers.update(self.resources.handlers())
        handlers.update(self.planning.handlers());handlers.update(self.projects_manager.handlers());handlers.update(self.libraries.handlers())
        if kind not in handlers:raise ValueError('本批次尚未接通该命令。')
        if kind==v3_save_kind:v3_request_shape(payload)
        if kind==v3_responsibility_kind:v3_responsibility_shape(payload)
        with self.lock:
            if (self.locations.data/'retired-target.json').exists():self.retiring=True
            if self.retiring:raise ValueError('控制服务正在切换数据目录；先重读新实例，不重发旧命令。')
            self.command_identity=command_id
            self.command_kind=kind;self.command_payload_hash=digest(payload)
            self.local_storage.reconcile()
            def perform(db):
                from .work_coordinator_handoff_post_round_successor_commands import command as successor_command, finish as successor_finish
                from .work_coordinator_handoff_post_round_v2_command_scope import command as v2_command
                from .work_coordinator_handoff_post_round_v2_commands import finish as v2_finish
                command_plan = self.teamwork.current(db, payload.get('id')) if kind in {'work-item.unit-send','work-item.proposal-check'} else None
                is_v2 = bool(command_plan and command_plan.get('coordinator_file_handoff_post_round_lineage', {}).get('protocol') == 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V2')
                from .work_coordinator_handoff_post_round_v3_public import is_v3
                from .work_coordinator_handoff_post_round_v3_command_scope import command as v3_command
                from .work_coordinator_handoff_post_round_v3_commands import finish as v3_finish
                command_scope, command_finish = ((v3_command, v3_finish) if is_v3(command_plan) else
                    (v2_command, v2_finish) if is_v2 else (successor_command, successor_finish))
                from .work_coordinator_handoff_text_rework_integration import command_pair
                text_pair = command_pair(self, db, kind, payload, command_plan)
                if text_pair is not None:
                    command_scope, command_finish = text_pair
                with (self.profiles.transaction(db) if text_pair is not None or kind==pdca_start else nullcontext()), command_scope(self, db, kind, payload):
                    routed,effective,control=self.work_runtime.route(db,payload) if kind=='work-item.control' else (kind,payload,None)
                    workspace_configuration = (kind=='work-item.control' and routed=='native.control' and
                        (control.get('action')=='native-extensions' and effective.get('operation')=='plugins.workspace'
                         or control.get('action')=='native-extension-activation' and effective.get('operation') in {'plugins.enable','plugins.disable'}))
                    if routed in {'native.control','native.access'} and effective.get('task_id') and self.store.task(effective['task_id'],db)['spec'].get('work_mode')=='cooperative' and not workspace_configuration:
                        raise ValueError('合作单元不通过原生控制更改输入、目标或权限；停止、审批及准确回合核对仍可使用。')
                    if routed in {'task.start','task.resume','independent.send','native.control','native.access'}:self.monitor.guard(db,effective,allow_driver=workspace_configuration)
                    effective,work_ref=self.dispatch.before(db,effective)
                    effective,resource_ref=self.resources.before(db,routed,effective)
                    receipt=handlers[routed](db,effective)
                    from copy import deepcopy
                    text_self_contained = (kind == text_driver_change or kind == 'work-item.rework-apply' and
                        isinstance(receipt.get('registration'),dict) and receipt['registration'].get('id','').startswith('work_coordinator_handoff_text_rework_registration_v1:'))
                    if text_self_contained:
                        text_final_response, text_final_changes = deepcopy(receipt), db.total_changes
                    if kind==v3_responsibility_kind:
                        from copy import deepcopy
                        responsibility_receipt=deepcopy(receipt)
                    def responsibility_guard(value):
                        if kind==v3_responsibility_kind and value!=responsibility_receipt:
                            raise ValueError('V3责任完整回执在提交前发生变化。')
                        return value
                    if kind==v3_save_kind:
                        from copy import deepcopy
                        saved_v3_receipt=deepcopy(receipt)
                    receipt=self.resources.complete(db,receipt,resource_ref)
                    receipt=responsibility_guard(receipt)
                    if routed in {'work-item.unit-send',text_review_send}:work_ref=self.work_units.work_ref(db,receipt)
                    from .work_fixed_preparation import START as fixed_prepare
                    # Preparation binds the exact dispatch before acquiring its driver in the same transaction.
                    if routed not in {fixed_prepare, self.leader_preparation.start_kind, self.host_preparation.start_kind, pdca_start, *cooperation_conversation_commands}:
                        receipt=self.dispatch.complete(db,routed,effective,receipt,work_ref)
                    receipt=responsibility_guard(receipt)
                    if routed=='work-item.unit-send':receipt=self.work_units.bound(db,receipt)
                    if routed==text_review_send:
                        from .work_coordinator_handoff_text_rework_commands import bind_review
                        receipt=bind_review(self,db,receipt)
                    receipt = self.work_runtime.recorded(db,receipt,control)
                    receipt=responsibility_guard(receipt)
                    receipt=command_finish(self, db, kind, payload, receipt)
                    receipt=responsibility_guard(receipt)
                    if kind==v3_save_kind and receipt!=saved_v3_receipt:
                        raise ValueError('V3保存完整回执在提交前发生变化。')
                    if text_self_contained and (receipt != text_final_response or db.total_changes != text_final_changes):
                        raise ValueError('文字返工保存/责任完整响应或SQL尾在最终提交前变化。')
                    return receipt
            from .work_coordinator_handoff_post_round_materials import transaction as post_material_transaction
            from .workspace import task_snapshot_transaction
            pdca_snapshot = kind == pdca_start or kind in cooperation_conversation_commands
            if kind == 'work-item.unit-send' and isinstance(payload.get('id'), str):
                with self.store.read() as db:
                    plan = self.teamwork.current(db, payload['id'])
                    pdca_snapshot = bool(plan and plan.get('pdca'))
            snapshot_guard = task_snapshot_transaction(self.locations.data/'tasks') if pdca_snapshot else nullcontext()
            try:
                with post_material_transaction(self), snapshot_guard:
                    receipt,fresh=self.store.command(command_id,kind,payload,perform)
            except Exception as exc:
                self.local_storage.reconcile()
                import sqlite3
                if (kind=='work-item.unit-send' and payload.get('post_round_adjustment') is True and
                        isinstance(exc,sqlite3.OperationalError) and
                        (getattr(exc,'sqlite_errorcode',0) & 255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)):
                    raise ValueError('另一写入命令尚未结束；本次未提交，请稍后使用同一命令身份核对。') from exc
                raise
            self.local_storage.reconcile()
            launches=receipt.get('launch_attempts',[]) or ([receipt['launch_attempt']] if receipt.get('launch_attempt') else [])
            for identity in launches if fresh else []:
                stop=threading.Event()
                thread=threading.Thread(target=self._run,args=(identity,stop,receipt.get('resume_mode'),receipt.get('note','')),daemon=True)
                self.workers[identity]={'thread':thread,'stop':stop}
                try:thread.start()
                except Exception:
                    self.store.update_attempt(identity,'FAILED_NO_SEND',result={'state':'FAILED_NO_SEND','known_tools_exited':True,
                         'owned_job':{'active_processes':0,'no_process_started':True},'remote_model_state':'NOT_SENT',
                         'ended_at':time.time(),'errors':[{'message':'controller thread did not start'}]})
            if fresh and receipt.get('retire_service'):self.retiring=True
            if fresh and receipt.get('launch_extension'):self.extensions.launch(receipt['launch_extension'])
            if fresh and receipt.get('launch_native_control'):self.native_controls.launch(receipt['launch_native_control'])
            if fresh and receipt.get('stop_native_control'):self.native_controls.signal_stop(receipt['stop_native_control'])
            if fresh and receipt.get('signal_stop'):
                worker=self.workers.get(receipt['signal_stop'])
                if worker:worker['stop'].set()
            if fresh and receipt.get('launch_chat_turn'):self.ui_features.chats.api.launch(receipt['launch_chat_turn'])
            if fresh and receipt.get('stop_chat_turn'):self.ui_features.chats.api.signal(receipt['stop_chat_turn'])
        return {'receipt':receipt,'replayed':not fresh,'command_id':command_id}

    def _reload(self,db,payload):
        if self.account_setup.active():raise ValueError('账号登录仍在等待，请先完成或取消后再停止服务。')
        if self.resources.active():raise ValueError('额度查询仍在途，不能重载。')
        if self.native_controls.active():raise ValueError('原生控制操作仍在途，不能重载。')
        if self.ui_features.chats.api.active():raise ValueError('API聊天仍在途，不能重载。')
        if self.extensions.active():raise ValueError('扩展操作仍在途，不能重载。')
        if any(w['thread'].is_alive() for w in self.workers.values()) or db.execute('SELECT 1 FROM attempts WHERE state IN ('+','.join('?' for _ in ACTIVE)+')',tuple(ACTIVE)).fetchone():
            raise ValueError('控制服务仍有活动执行，不能重载。')
        self.store.event(None,None,'idle_service_reload',{'no_dispatch':True,'instance':self.instance},db)
        return {'retire_service':True,'instance':self.instance,'no_dispatch':True,'history_preserved':True}

    def close(self):
        self.account_setup.close()
        self.native_controls.close()
        self.ui_features.chats.api.close()
        self.collaboration.close()
        self.extensions.close()
        for worker in self.workers.values():worker['stop'].set()
        for worker in self.workers.values():worker['thread'].join(timeout=12)

    def _native_control_rows(self,identity):
        with self.store.read() as db:return self.native_controls.rows(db,identity)
