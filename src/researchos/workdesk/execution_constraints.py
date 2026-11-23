"""Frozen execution limits and evidence-based recovery; no provider calls on read."""
import json

from .store import ACTIVE, UNKNOWN, digest
from .work_read_observation import database_fact


PENDING_OPS={'QUEUED','SEND_INTENT','DELIVERY_UNKNOWN','UNKNOWN_AFTER_RESTART'}
REMOTE_TERMINAL={'completed','failed','interrupted','NOT_SENT'}


def remote_state(task,attempt):
    result=attempt.get('result') or {};report=attempt.get('reconciliation') or {}
    proof=report.get('exact_turn') or {}
    if (proof.get('attempt_id')==attempt['id'] and proof.get('session_id')==attempt['session_id']
            and proof.get('turn_id')==attempt['turn_id']
            and proof.get('connection_fingerprint')==digest(task['spec'].get('execution_connection'))
            and proof.get('account_verified') is True and report.get('known_local_scopes_exited')
            and report.get('protocol_terminal') in REMOTE_TERMINAL):
        return report['protocol_terminal']
    return result.get('remote_model_state','UNREPORTED')


def _task_database_blockers(service, db, task):
    reasons=[]
    # The monitor needs only unknown requests. Avoid materializing every historical
    # attempt/result and tool call once per task; preserve restored task debt.
    unknown=db.execute("SELECT COUNT(*) FROM requests WHERE task_id=? AND state='UNKNOWN'",(task['id'],)).fetchone()[0]
    offset=db.execute("SELECT data FROM usage_offsets WHERE scope='task' AND id=?",(task['id'],)).fetchone()
    if offset:unknown+=json.loads(offset[0]).get('unknown_requests',0)
    if unknown:reasons.append('REQUEST_UNKNOWN')
    if any(o['state'] in PENDING_OPS for o in service.native_controls.rows(db,task['id'])):reasons.append('NATIVE_OPERATION')
    if db.execute("SELECT 1 FROM native_requests WHERE task_id=? AND state IN ('READY','REPLY_INTENT') LIMIT 1",(task['id'],)).fetchone():reasons.append('NATIVE_REPLY')
    return reasons


def blockers(service,db,task,attempt,*,include_state=True):
    reasons=[]
    if include_state and (task['state'] in ACTIVE|UNKNOWN or attempt['state'] in ACTIVE|UNKNOWN):reasons.append('CURRENT_TURN')
    if any(w['thread'].is_alive() and service.store.attempt(i,db)['task_id']==task['id'] for i,w in service.workers.items()):reasons.append('LOCAL_WORKER')
    if not service.operations.exit_proved(attempt):reasons.append('EXIT_UNPROVED')
    if remote_state(task,attempt)=='UNKNOWN':reasons.append('REMOTE_UNKNOWN')
    reasons.extend(database_fact(service, db, ('execution_task_blockers', task['id']),
        lambda: _task_database_blockers(service, db, task)))
    return reasons


def snapshot(task,attempt):
    spec=task['spec'];official=spec.get('runtime_mode')=='official';native=spec.get('execution','scoped')!='scoped'
    access=spec.get('access')
    if official:
        from .native_permissions import catalog
        selected=next((a for a in catalog(spec['provider']) if a['id']==access),None)
        policy={'choice':access or 'inherit','name':selected['name'] if selected else '历史工作端配置',
                'native_mode':selected['native_mode'] if selected else 'UNRECORDED'}
    else:policy={'native_policy':spec.get('native',{}),'host_command_permission':spec.get('host_command_permission',False)}
    return {'schema':1,'source':'DISPATCH_SNAPSHOT','spec_hash':task['spec_hash'],
            'mode':'OFFICIAL' if official else 'FINITE','execution':spec.get('execution','scoped'),
            'workspace':attempt['workspace'],'workspace_is_isolation':False,'instruction_enforcement':'PROMPT_ONLY',
            'access':policy,'budget':dict(spec.get('budget',{})),'deadline':attempt['deadline'] or None,
            'budget_enforcement':'VENDOR_CONFIGURATION_NO_WORKDESK_CAP' if official else 'WORKDESK_TASK_AND_CHAIN',
            'tool_enforcement':'VENDOR_CONFIGURATION_NO_WORKDESK_CAP' if official else 'OBSERVE_THEN_INTERRUPT' if native else 'WORKDESK_TOOL_GATE',
            'hard_balance_enforcement':'UNSUPPORTED','quota_enforcement':'INFORMATION_ONLY',
            'filesystem_enforcement':'NATIVE_POLICY_NOT_EXACT_MATERIAL_SCOPE' if native and spec['provider']=='codex'
                else 'NATIVE_TOOLS_NO_WORKDESK_OS_ISOLATION' if native else 'WORKDESK_FILE_GATE; CHECK_COMMANDS_NOT_OS_ISOLATED',
            'constraints_fingerprint':digest({'budget':spec.get('budget',{}),'access':policy,'workspace':attempt['workspace']})}


def view(service,db,task,attempt,dispatch):
    frozen=(dispatch or {}).get('constraints')
    value=dict(frozen) if frozen else {**snapshot(task,attempt),'source':'CURRENT_RECORD_RECONSTRUCTION'}
    observed=db.execute("SELECT seq,kind,payload FROM events WHERE attempt_id=? AND kind='runtime_configuration' ORDER BY seq DESC LIMIT 1",(attempt['id'],)).fetchone()
    # Only this event contains the checked Codex sandbox response. SDK launch
    # settings alone do not prove OS enforcement.
    value['policy_evidence']={'status':'NO_RUNTIME_RECEIPT'}
    if observed and isinstance(json.loads(observed['payload']).get('observed_sandbox'),dict):
        payload=json.loads(observed['payload'])
        value['policy_evidence']={'status':'NATIVE_CONFIGURATION_RETURNED','event_seq':observed['seq'],
            'observed_sandbox':payload.get('observed_sandbox'),'permission_profile':payload.get('permission_profile')}
    value['usage']=service.store.usage(task=task['id'],db=db)
    return value
