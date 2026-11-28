"""Task persistence adapter for the Go-owned official partner run.

This module observes one exact native run. It never opens credentials, starts
an Agent, executes a tool or retries an ambiguous dispatch.
"""
from copy import deepcopy
from pathlib import Path
import time

from .store import ACTIVE, UNKNOWN
from .partners.native_features import feature


def native_continuation(binding, *, automatic=False, access=None):
    value = feature(binding, 'resume', 'workspace' if access == 'inherit' else access)
    return bool(value.get('method') and (not automatic or value.get('automatic') is True))


def resume_reference(task, prior):
    """Require the prior turn's original result, not a provisional session ID."""
    if not native_continuation(task['spec'].get('partner_connection') or {}, access=task['spec'].get('access', 'workspace')):
        raise ValueError('此工作端版本的准确原生会话续跑尚未核实；原会话保留。')
    return completed_reference(task, prior)


def completed_reference(task, prior):
    result = prior.get('result') or {}
    receipt = result.get('native_result') or {}
    run_id, session_id = result.get('partner_run_id'), prior.get('session_id')
    if (prior.get('task_id') != task['id'] or prior.get('state') != 'COMPLETED' or prior.get('stop_requested')
            or result.get('state') != 'COMPLETED' or result.get('known_tools_exited') is not True
            or (result.get('owned_job') or {}).get('active_processes') != 0
            or result.get('stop_requested') is True or not run_id or not session_id
            or receipt.get('type') != 'result' or receipt.get('receipt_unknown') is True
            or receipt.get('completion_scope') != 'OFFICIAL_NATIVE_TURN'
            or (receipt.get('run_id'), receipt.get('session_id'), receipt.get('partner_id'))
               != (run_id, session_id, task['spec']['provider'])):
        raise ValueError('原回合没有已完成的原生会话及退出证据，未重新派发。')
    return {'resume_session': session_id, 'resume_run_id': run_id, 'resume_attempt_id': prior['id']}


def is_binding(value):
    return isinstance(value, dict) and value.get('execution') == 'partner_native'


def selected(task):
    return task['spec'].get('execution') == 'partner_native'


def descriptor(binding):
    from .partners.providers import catalog
    data,_=catalog()
    partner = next((p for p in data['partners'] if p['id'] == binding.get('partner_id', binding.get('provider'))), None)
    if not partner:
        raise ValueError('所选伙伴未登记，未启动。')
    from .native_permissions import catalog as permissions
    return {'provider': partner['id'], 'name': partner['name'], 'harness': partner['name'] + ' 官方工作端',
            'execution': 'partner_native', 'native_resume': native_continuation(binding), 'context_resume': False,
            'native_tools': True, 'origin': 'MODEL_VENDOR_OFFICIAL', 'agent_loop': 'VENDOR_RUNTIME',
            'third_party_client': False, 'configuration': 'FROZEN_PARTNER_CONNECTION',
            'scope': 'INDEPENDENT_WORK', 'access_modes': permissions(partner['id']),
            'native_controls': [], 'connection_id': binding['id'], 'connection_revision': binding['revision'],
            'release_id': binding.get('release_id'), 'route': binding.get('route'),
            'api_provider': binding.get('api_provider'), 'account_id': binding.get('account_id')}


def model_selection(binding):
    return {'source': 'FROZEN_PARTNER_CONNECTION', 'provider': binding['provider'],
            'model': binding.get('model', ''), 'connection_id': binding['id'],
            'connection_revision': binding['revision'], 'route': binding.get('route'),
            'api_provider': binding.get('api_provider'), 'account_id': binding.get('account_id'),
            'secret_version': binding.get('secret_version'), 'release_id': binding.get('release_id')}


def prompt_for(task, note='', resume=False, handoff=False):
    from .scene_runtime import prompt, skills
    text = prompt(task, note if resume or handoff else task['spec']['goal'], resume, mcp_label='本次选用的 MCP')
    material = task['spec'].get('materials') or {}
    if material:
        text += '\n\n本次已选择的项目材料（由官方工作端的文件工具读取）：\n' + '\n'.join(material)
    references = skills(task)
    if references:
        text += '\n\n本次选用的技能文档，请用官方文件工具读取并按文档处理；这些路径未另作原生技能注册：\n'
        text += '\n'.join(item['name'] + ': ' + item['path'] for item in references)
    return text


def checked_scene_mcp(task, workdir):
    from .capabilities import mcp_config
    from .scene_runtime import selected
    scene = selected(task) or {}
    servers = scene.get('mcp', [])
    access = task['spec'].get('access') or 'workspace'
    supported = feature(task['spec'].get('partner_connection') or {}, 'mcp', 'workspace' if access == 'inherit' else access)
    if servers and not supported.get('transports'):
        raise ValueError('此场景使用 MCP；当前工作端版本、连接或权限尚未接入每任务 MCP。')
    for server in servers:
        config = mcp_config(server['config'])
        if config['transport'] not in supported.get('transports', ()):
            raise ValueError('当前工作端未核实这种每任务 MCP 传输方式。')
        if (supported.get('stdio_cwd') == 'project' and config['transport'] == 'stdio'
                and Path(config['cwd']).resolve() != Path(workdir).resolve()):
            raise ValueError('当前工作端的本机 MCP 须使用本次项目目录；不同目录未发送。')
    return servers


def _validate_run(run, task, attempt, binding, run_id=None):
    if not isinstance(run, dict) or not isinstance(run.get('id'), str) or not run['id']:
        raise ValueError('核心未返回准确伙伴运行身份；保留待核对。')
    expected = {'parent_task_id': task['id'], 'parent_attempt_id': attempt['id'],
                'parent_spec_hash': task['spec_hash'], 'connection_id': binding['id'],
                'connection_revision': binding['revision'],
                'partner_id': task['spec']['provider'], 'account_id': binding.get('account_id')}
    if run_id is not None:
        expected['id'] = run_id
    if binding.get('secret_version') is not None:
        expected['secret_version'] = binding['secret_version']
    previous = (attempt.get('baseline') or {}).get('partner_resume', {})
    if previous:
        expected.update(previous)
        if run.get('session_id') and run['session_id'] != previous.get('resume_session'):
            raise ValueError('伙伴工作端回执中的会话与准确续跑身份不同，保留待核对，不重发。')
    for key, value in expected.items():
        if value is None or str(run.get(key)) != str(value):
            raise ValueError('伙伴运行与原任务、尝试或准确连接不一致：' + key)
    snapshot = run.get('connection_snapshot') or {}
    if snapshot.get('route') != binding.get('route'):
        raise ValueError('伙伴运行的账号／API路线与原连接不一致。')
    for key in ('id','revision','account_id','release_id'):
        if binding.get(key) is not None and str(snapshot.get(key))!=str(binding[key]):
            raise ValueError('伙伴运行冻结的连接已变化：'+key)
    if binding.get('route')=='api':
        api=snapshot.get('api_provider') or snapshot.get('driver_provider') or 'deepseek'
        if api!=binding.get('api_provider') or snapshot.get('model')!=binding.get('model'):
            raise ValueError('伙伴运行的实际 API 厂商或模型与原选择不一致。')
    return run


def normalize(run, task, attempt, *, stop_requested=False):
    binding = task['spec']['partner_connection']
    _validate_run(run, task, attempt, binding)
    state = run.get('state')
    exited = run.get('local_exit_confirmed') is True
    result = run.get('result') or {}
    receipt = (result.get('type') == 'result' and result.get('completion_scope') == 'OFFICIAL_NATIVE_TURN'
               and result.get('receipt_unknown') is not True
               and bool(run.get('session_id'))
               and all(result.get(k) == run.get('id' if k == 'run_id' else k)
                       for k in ('run_id', 'partner_id', 'session_id')))
    stopped = stop_requested or run.get('stop_requested') is True
    if state == 'COMPLETED':
        normalized = 'STOPPED_RUNTIME' if stopped and exited else 'COMPLETED' if receipt and exited else 'SEND_UNKNOWN'
    elif state in {'FAILED', 'FAILED_NO_SEND', 'CANCELLED', 'STOPPED_RUNTIME'}:
        normalized = state if exited or state == 'FAILED_NO_SEND' else 'STOP_UNKNOWN' if stopped else 'SEND_UNKNOWN'
    elif state in UNKNOWN:
        normalized = state
    else:
        normalized = 'STOP_UNKNOWN' if stopped else 'SEND_UNKNOWN'
    answer = result.get('text', result.get('output', ''))
    if not isinstance(answer, str):
        answer = ''
    remote=run.get('remote_state','NOT_SENT' if state=='FAILED_NO_SEND' else 'UNKNOWN')
    if normalized=='COMPLETED':remote='completed'
    return {'state': normalized, 'answer': answer, 'task_id': task['id'], 'attempt_id': attempt['id'],
            'owner': attempt['owner'], 'execution': 'partner_native', 'protocol': 'GO_OWNED_OFFICIAL_PARTNER',
            'partner_run_id': run['id'], 'partner_id': run['partner_id'], 'session_id': run.get('session_id'),
            'known_tools_exited': exited or state == 'FAILED_NO_SEND',
            'owned_job': {'active_processes': 0, 'source': 'CORE_OWNED_PARTNER_EXIT'} if exited or state == 'FAILED_NO_SEND' else None,
            'remote_model_state': remote, 'native_remote_state':run.get('remote_state'),
            'connection_snapshot': deepcopy(run.get('connection_snapshot')),
            'native_result': deepcopy(result), 'actual_model': result.get('model') or run.get('resolved_model'),
            'errors': [{'message': run['error']}] if run.get('error') else [],
            'usage_source': 'CORE_PARTNER_REQUEST_RECEIPTS', 'usage_mirrored': False,
            'stop_requested': stopped, 'ended_at': time.time(), 'specification_sha256':task['spec_hash']}


def inspect(service,task,attempt,db,active):
    """Read only the Go owner's saved run; never initialize a native runtime."""
    from .partner_usage import objects
    from .store import digest,TERMINAL
    report={'checked_at':time.time(),'local_worker_owned_alive':active,'saved_state':attempt['state'],
            'no_resend':True,'known_local_scopes_exited':False,'remote_model_state':'UNKNOWN',
            'arbitrary_external_processes':'UNKNOWN','protocol_terminal':None,
            'protocol':'GO_OWNED_OFFICIAL_PARTNER','source':'SAVED_CORE_PARTNER_RUN'}
    matches=[r for r in objects(db,'run') if r.get('parent_task_id')==task['id'] and r.get('parent_attempt_id')==attempt['id']]
    if len(matches)!=1:
        report['reason']='未找到唯一的原伙伴运行；保留未知，不重新发送。'
        return report
    run=matches[0]
    try:
        result=normalize(run,task,attempt,stop_requested=bool(attempt.get('stop_requested')))
        known_id=(attempt.get('result') or {}).get('partner_run_id')
        if known_id and run['id']!=known_id:
            raise ValueError('保存的伙伴运行身份已变化。')
    except (ValueError,KeyError,TypeError) as exc:
        report['reason']=str(exc)
        return report
    report.update(partner_run_id=run['id'],known_local_scopes_exited=result['known_tools_exited'],
                  remote_model_state=result['remote_model_state'],runtime_exit=result['owned_job'],
                  saved_partner_state=run.get('state'))
    if result['state']=='COMPLETED':
        report.update(protocol_terminal='completed',exact_turn={'attempt_id':attempt['id'],
            'session_id':run.get('session_id'),'turn_id':attempt.get('turn_id'),
            'connection_fingerprint':digest(task['spec'].get('execution_connection')),
            'account_verified':True,'source':'EXACT_CORE_CONNECTION_AND_OFFICIAL_RESULT'})
    if result['state'] in TERMINAL and result['known_tools_exited']:
        report['recovered_frozen_result']=result
    return report


def _observe(service, task, attempt, run, *, initial=False):
    """Short ledger transaction only; never call the core while it is open."""
    session = run.get('session_id')
    projected = run.get('state')
    if projected not in ACTIVE:
        projected = 'SEND_INTENT' if projected == 'QUEUED' else 'RUNNING'
    value = {'partner_run_id': run['id'], 'state': projected, 'execution': 'partner_native',
             'task_id':task['id'],'attempt_id':attempt['id'],'owner':attempt['owner'],
             'connection_id': run['connection_id'], 'connection_revision': run['connection_revision'],
             'session_id': session, 'usage_source': 'CORE_PARTNER_REQUEST_RECEIPTS'}
    with service.store.write() as db:
        current = service.store.attempt(attempt['id'], db)
        if current['stop_requested']:
            projected = 'STOP_REQUESTED'
        changed = initial or (current.get('result') or {}).get('partner_run_id') != run['id'] or current['state'] != projected or session and current['session_id'] != session
        if changed:
            fields = {'result': {**value, 'state': projected}}
            if session:
                fields['session_id'] = session
            service.store.update_attempt(attempt['id'], projected, db=db, **fields)
            service.store.event(task['id'], attempt['id'], 'partner_run_bound' if initial else 'partner_run_observed', value, db)


def record_mcp_configuration(service, task, attempt, run, response):
    from .scene_runtime import receipt
    for row in response.get('events', []):
        event = row.get('event') or {}
        if (event.get('kind') == 'native_scene_mcp_configured' and event.get('run_id') == run['id']
                and event.get('partner_id') == run['partner_id'] and event.get('session_id') == run.get('session_id')):
            receipt(service.store, task, attempt, 'CONFIGURED',
                    {k: event[k] for k in ('source', 'servers', 'session_id', 'connection_state') if k in event})


def run(service, task, attempt, stop, resume=None, note='', *, rpc=None, poll_interval=.25):
    from .bridge import call
    rpc = rpc or call
    binding = task['spec'].get('partner_connection')
    if not is_binding(binding):
        raise ValueError('任务缺少已冻结伙伴连接，未启动。')
    previous = resume_reference(task, resume) if resume else {}
    if previous != (attempt.get('baseline') or {}).get('partner_resume', {}):
        raise ValueError('原生续跑与本次预订记录不符，未派发。')
    checked_scene_mcp(task, attempt['workspace'])
    handoff = (attempt.get('baseline') or {}).get('partner_handoff')
    if handoff:
        from .result_records import delivered_answer
        with service.store.read() as db:
            messages = []
            rows = db.execute('SELECT * FROM attempts WHERE task_id=? AND started<? ORDER BY started DESC LIMIT 8',
                              (task['id'], attempt['started'])).fetchall()
            for row in reversed(rows):
                turn = service.store.attempt_row(row)
                user = db.execute("SELECT COALESCE(json_extract(payload,'$.user_message'),json_extract(payload,'$.human_note')) FROM events WHERE attempt_id=? AND kind IN ('execution_reserved','single_task_continued') ORDER BY seq DESC LIMIT 1",(turn['id'],)).fetchone()
                if user and user[0]:
                    messages.append('用户：'+user[0][:4000])
                answer = delivered_answer(db, turn)
                if answer:
                    messages.append('助手：'+answer[:8000])
        note = ('原任务目标：'+task['spec']['goal']+'\n\n近期对话摘录（此前回合）：\n'+'\n'.join(messages)+'\n\n本轮用户要求：'+note)
    text = prompt_for(task, note, bool(resume), bool(handoff))
    identity = {'task_id': task['id'], 'attempt_id': attempt['id'], 'spec_hash': task['spec_hash']}
    if stop.is_set() or attempt.get('stop_requested'):
        return {'state': 'FAILED_NO_SEND', 'answer': '', 'known_tools_exited': True,
                'owned_job': {'active_processes': 0, 'no_process_started': True}, 'remote_model_state': 'NOT_SENT',
                'execution': 'partner_native', 'stop_requested': True, 'errors': []}
    # Start is keyed by the committed parent attempt in Go. Its reply may be
    # lost: that is an unknown delivery, never a reason to call start again.
    started = False
    run_id = None
    try:
        with service.store.write() as db:
            latest=service.store.attempt(attempt['id'],db)
            if latest.get('stop_requested') or stop.is_set():
                return {'state':'FAILED_NO_SEND','answer':'','known_tools_exited':True,
                        'owned_job':{'active_processes':0,'no_process_started':True},'remote_model_state':'NOT_SENT',
                        'execution':'partner_native','stop_requested':True,'errors':[]}
            service.store.update_attempt(attempt['id'],'SEND_INTENT',db=db)
            service.store.event(task['id'],attempt['id'],'partner_dispatch_intent',
                                {**identity,'connection_id':binding['id'],'connection_revision':binding['revision'],'no_retry_on_unknown':True},db)
        started = True
        response = rpc('partner.task.start', {**identity, **previous, 'connection_id': binding['id'],
            'connection_revision': binding['revision'], 'workspace': str(Path(attempt['workspace'])), 'prompt': text})
        current = _validate_run(response['run'], task, attempt, binding)
        run_id = current['id']
        _observe(service, task, attempt, current, initial=True)
        record_mcp_configuration(service, task, attempt, current, response)
        cursor = response.get('cursor', 0)
        stop_sent = False
        while True:
            stop_wanted = stop.is_set() or service.store.attempt(attempt['id']).get('stop_requested')
            if stop_wanted and not stop_sent:
                stop_sent = True
                response = rpc('partner.task.stop', {**identity, 'run_id': run_id})
                current = _validate_run(response['run'], task, attempt, binding, run_id)
            state = current.get('state')
            if state in {'COMPLETED', 'FAILED', 'FAILED_NO_SEND', 'CANCELLED', 'STOPPED_RUNTIME'} | UNKNOWN:
                return normalize(current, task, attempt, stop_requested=bool(stop_wanted))
            # Event.wait returns immediately after stop; use time.sleep then
            # continue polling the same owner until its exit/unknown receipt.
            time.sleep(poll_interval)
            response = rpc('partner.task.poll', {**identity, 'run_id': run_id, 'after_seq': cursor})
            current = _validate_run(response['run'], task, attempt, binding, run_id)
            cursor = response.get('cursor', cursor)
            _observe(service, task, attempt, current)
            record_mcp_configuration(service, task, attempt, current, response)
    except Exception as exc:
        stopped = stop.is_set() or attempt.get('stop_requested')
        return {'state': 'STOP_UNKNOWN' if stopped and started else 'SEND_UNKNOWN' if started else 'FAILED_NO_SEND',
                'answer': '', 'execution': 'partner_native', 'partner_run_id': run_id,
                'known_tools_exited': not started, 'owned_job': None if started else {'active_processes': 0},
                'remote_model_state': 'UNKNOWN' if started else 'NOT_SENT', 'errors': [{'class': type(exc).__name__, 'message': str(exc)[:1200]}],
                'usage_source': 'CORE_PARTNER_REQUEST_RECEIPTS', 'usage_mirrored': False}
