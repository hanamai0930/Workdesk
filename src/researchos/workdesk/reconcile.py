"""Read-only evidence recovery. Never resend, restore a turn, or kill a PID."""
import ast
import json
import time
from hashlib import sha256

from . import workspace
from .paths import process_identity


def rejected_input(task,attempt,result):
    """Only an explicit local App Server size rejection proves no turn began.

    Historic RpcError records contain the server's literal response. Timeouts,
    transport loss, other server errors and an accepted turn remain unknown.
    """
    if (task['spec'].get('execution')!='codex_native' or attempt.get('turn_id')
            or not attempt.get('session_id') or not result
            or result.get('state')!='SEND_UNKNOWN' or result.get('protocol')!='UNKNOWN'):
        return None
    errors=result.get('errors',[])
    if not isinstance(errors,list) or len(errors)!=1 or not isinstance(errors[0],dict) or errors[0].get('class')!='RpcError':return None
    message=errors[0].get('message','');prefix='turn/start: server rejected request: '
    if not isinstance(message,str) or len(message)>1200 or not message.startswith(prefix):return None
    try:
        error=ast.literal_eval(message[len(prefix):]);data=error.get('data',{})
        if (error.get('code')!=-32602 or data.get('input_error_code')!='input_too_large'
                or type(data.get('max_chars')) is not int or type(data.get('actual_chars')) is not int
                or not 0<data['max_chars']<data['actual_chars']):return None
    except (ValueError,SyntaxError,TypeError,AttributeError):return None
    return error


def exited(record):
    if not record.get('creation'):return None,{'saved':record,'current':'IDENTITY_MISSING'}
    current=process_identity(record['pid'])
    gone=current['alive'] is False or bool(current.get('creation') and current['creation']!=record['creation'])
    return gone if current['alive'] is not None else None,{'saved':record,'current':current,'original_exited':gone}


def inspect(service,task,attempt,db,active,probe_codex=False):
    if task['spec'].get('execution')=='partner_native':
        from .partner_task import inspect as inspect_partner
        return inspect_partner(service,task,attempt,db,active)
    report={'checked_at':time.time(),'local_worker_owned_alive':active,'saved_state':attempt['state'],
            'no_resend':True,'known_local_scopes_exited':False,'remote_model_state':'UNKNOWN',
            'arbitrary_external_processes':'UNKNOWN','protocol_terminal':None}
    result=attempt['result']
    if not result:
        path=service.locations.data/'tasks'/task['id']/'attempts'/attempt['id']/'result.json'
        if path.is_file() and path.stat().st_size<=16*1024*1024:
            candidate=json.loads(path.read_text(encoding='utf-8'))
            if (candidate.get('attempt_id')==attempt['id'] and candidate.get('task_id')==task['id']
                    and candidate.get('owner')==attempt['owner'] and candidate.get('specification_sha256')==task['spec_hash']):
                root=path.parent/'files'
                observed=workspace.current_manifest(root,set(candidate.get('manifest',{})))
                if observed==candidate.get('manifest'):result=candidate;report['recovered_frozen_result']=candidate
    report['runtime_exit']=(result or {}).get('owned_job','UNKNOWN')
    report['protocol']=(result or {}).get('protocol','UNKNOWN')
    if not active and result and result.get('known_tools_exited') and (result.get('owned_job') or {}).get('active_processes')==0:
        report['known_local_scopes_exited']=True
    elif not active:
        events=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind IN ('runtime_process_started','check_process_started') ORDER BY seq",(attempt['id'],)).fetchall()
        observations=[];proof=[]
        for row in events:
            data=json.loads(row['payload']);gone,observation=exited(data);observations.append(observation)
            controller=data.get('job_controller')
            controller_gone,controller_observation=exited(controller) if controller else (None,None)
            observation['job_controller']=controller_observation
            proof.append(gone is True and controller_gone is True and data.get('kill_on_job_close') is True)
        report['process_observations']=observations
        report['known_local_scopes_exited']=bool(proof and all(proof))
    if report['known_local_scopes_exited']:
        report['observed_manifest']=workspace.current_manifest(attempt['workspace'],set(task['spec']['materials'])|set(task['spec']['writable']))
        rejection=rejected_input(task,attempt,result)
        activity=db.execute("SELECT 1 FROM events WHERE attempt_id=? AND kind IN ('model_message','model_delta','native_tool_started','protocol_terminal') LIMIT 1",(attempt['id'],)).fetchone()
        if rejection and not activity:
            report['turn_start_rejected_no_generation']=rejection
            report['remote_model_state']='NOT_SENT';report['protocol']='REJECTED_BEFORE_TURN'
            # Keep the original frozen result.json and its conservative UNKNOWN.
            # Persist a separate, auditable disposition in the controller record.
            report['recovered_rejected_result']={**result,'state':'FAILED_NO_SEND','protocol':'REJECTED_BEFORE_TURN',
                'remote_model_state':'NOT_SENT','input_rejection_reconciliation':{'original_state':result['state'],
                'checked_at':report['checked_at'],'server_rejection':rejection,'original_frozen_result_preserved':True}}
    from .execution_constraints import remote_state,REMOTE_TERMINAL
    previous=attempt.get('reconciliation') or {}
    if remote_state(task,attempt) in REMOTE_TERMINAL and previous.get('exact_turn'):
        report.update({k:previous[k] for k in ('exact_turn','protocol_terminal') if k in previous})
        report['remote_model_state']=previous['protocol_terminal']
    if (report['known_local_scopes_exited'] and task['spec'].get('qwen_runtime')=='daemon'
            and attempt['turn_id'] and not report['protocol_terminal']):
        from .qwen_daemon_history import terminal
        from .store import digest
        evidence=terminal(db,attempt)
        saved=db.execute("SELECT payload FROM events WHERE attempt_id=? AND kind='execution_reserved' ORDER BY seq LIMIT 1",
                         (attempt['id'],)).fetchone()
        reservation=json.loads(saved[0]) if saved else {}
        if (evidence and reservation.get('owner')==attempt['owner'] and reservation.get('execution_connection')
                and reservation['execution_connection']==task['spec'].get('execution_connection')
                and reservation.get('configuration_snapshot',{}).get('qwen_runtime')=='daemon'):
            # These receipts were recorded by the worker admitted through the
            # frozen connection. No current vendor or account lookup is needed.
            report['exact_turn']={'attempt_id':attempt['id'],'session_id':attempt['session_id'],'turn_id':attempt['turn_id'],
                                  'connection_fingerprint':digest(task['spec'].get('execution_connection')),
                                  'account_verified':True,'source':'FROZEN_CONNECTION_AND_NATIVE_DAEMON_RECEIPTS'}
            report['qwen_daemon_evidence']=evidence
            report['protocol_terminal']=report['remote_model_state']=evidence['outcome']
    if probe_codex and not active and task['spec']['provider']=='codex' and attempt['session_id'] and attempt['turn_id']:
        rpc=None;job=None
        try:
            from .adapters import open_codex,owned
            locations=service.resources.task_context(task,db)
            end=time.monotonic()+10
            rpc=open_codex(locations,attempt['workspace'],lambda message:None)
            job=owned(locations,rpc.process)
            def read(method,params):
                left=end-time.monotonic()
                if left<=0:raise TimeoutError('Read-only reconciliation time limit')
                return rpc.request(method,params,timeout=min(5,left))
            metadata=read('account/read',{'refreshToken':False});account=metadata.get('account') or {}
            account_id=(metadata.get('workspaceRouting') or {}).get('chatgptAccountId')
            if not account_id:account_id=read('account/rateLimits/read',{}).get('accountId')
            email=account.get('email')
            fingerprint=sha256(json.dumps({'email':email.strip().casefold(),'account_id':account_id},sort_keys=True).encode()).hexdigest() if account.get('type')=='chatgpt' and isinstance(email,str) and email.strip() and isinstance(account_id,str) and account_id else None
            bound=db.execute("SELECT payload FROM events WHERE task_id=? AND kind='codex_session_account_bound' AND json_extract(payload,'$.session_id')=? ORDER BY seq LIMIT 1",(task['id'],attempt['session_id'])).fetchone()
            if not fingerprint or not bound or json.loads(bound[0]).get('fingerprint')!=fingerprint:
                raise ValueError('原账号身份缺失或不符；未查询或恢复旧会话。')
            cursor=None;seen=set();matched=None
            for page in range(5):
                response=read('thread/turns/list',{'threadId':attempt['session_id'],'limit':20,'itemsView':'notLoaded',**({'cursor':cursor} if cursor else {})})
                report['codex_probe_pages']=page+1
                matched=next((item for item in response.get('data',response.get('turns',[])) if item.get('id')==attempt['turn_id']),None)
                cursor=response.get('nextCursor')
                if matched or not cursor or cursor in seen:break
                seen.add(cursor)
            report['exact_turn']={'attempt_id':attempt['id'],'session_id':attempt['session_id'],'turn_id':attempt['turn_id'],
                'spec_hash':task['spec_hash'],'account_verified':True,'found':bool(matched),'raw_identity_saved':False}
            from .store import digest
            report['exact_turn']['connection_fingerprint']=digest(task['spec'].get('execution_connection'))
            if matched and matched.get('status') in {'completed','failed','interrupted'}:
                report['protocol_terminal']=matched['status'];report['remote_model_state']=matched['status']
            report['codex_read_only_probe']='EXACT_TURN_LOOKUP_NO_GENERATION'
        except Exception as exc:report['codex_probe_error']={'class':type(exc).__name__,'message':str(exc)[:500]}
        finally:
            if rpc:rpc.close()
            if job:report['probe_owned_job']=job.close()
    report['note']='本地已知归属范围与远端状态分别核对；未知请求费用保留，不重发。'
    return report
