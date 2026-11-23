"""Semantic original execution ledger, independent of responsibility actions.

Only reconstructed rows receive carrier identities. The Codex branch covers
successful first turns with bounded tools and complete spawned child observations.
Scenes and other Human interaction variants remain closed.
Other native/SDK branches remain closed, including unknown telemetry shapes.
"""
import json
from hashlib import sha256
from uuid import UUID

from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_post_round_v3_execution_shapes import (
    original_input, user_message, sandbox_observation, turn_observation, command_observation,
    file_change_observation, external_tool_observation)
from .work_coordinator_handoff_post_round_v3_execution_children import (
    TOOL_KINDS, collab_observation, stable_tool, child_events, bind_spawns)
from .work_coordinator_handoff_post_round_v3_execution_requests import request_events
from .store import canonical, digest


def dispatch_session(service, db, checkpoints, frame, dispatch):
    """Keep the original reservation distinct from a returned native thread.

    Native thread/start updates attempt.session_id after the committed dispatch.
    A changed session is admitted only by the complete bounded native replay;
    no caller flag or claimed returned thread supplies this exception.
    """
    attempt = service.store.attempt(frame['run']['attempt_id'], db)
    original = dispatch['session_id']
    if original == attempt['session_id']:
        return original
    try:
        if not isinstance(original, str) or str(UUID(original)) != original:
            raise ValueError('原派发session须是准确预留UUID。')
    except (TypeError, AttributeError) as exc:
        raise ValueError('原派发session无效。') from exc
    if (attempt.get('result') or {}).get('execution') != 'codex_native':
        raise ValueError('V3派发session变化须由完整原生返回链重建。')
    execution_events(service, db, checkpoints, frame, dispatch)
    return original


def execution_events(service, db, checkpoints, frame, dispatch):
    run = frame['run']; task = service.store.task(run['task_id'], db)
    attempt = service.store.attempt(run['attempt_id'], db); spec = task['spec']
    result = attempt['result']; registry = {}
    if spec.get('runtime_mode') != 'official' or spec.get('work_mode') != 'cooperative':
        raise ValueError('V3完整current任务事件尚未核实其它执行路径。')

    def exact(kind, body, identity=attempt['id']):
        event = checkpoints._event(db, kind, body, None, task['id'], identity)
        registry[(kind, event['seq'])] = (body, task['id'], identity)
        return event

    created = exact('cooperative_unit_created', {'runtime_mode': 'official',
        'configuration': 'VENDOR_DEFAULTS', 'extra_task_limits': False}, None)
    reserved = exact('execution_reserved', {'owner': attempt['owner'], 'runtime_mode': 'official',
        'human_note': '', 'implementation_revision': result['implementation_revision'], 'resume_mode': None,
        'configuration_snapshot': {**{k: spec[k] for k in ('provider', 'model', 'effort', 'tier')},
            'access': spec.get('access', 'inherit'), 'configuration': 'VENDOR_DEFAULTS'},
        'credential_selection_revision': dispatch['credential_revision'],
        'execution_connection': spec['execution_connection'], 'extra_task_limits': False})
    frozen = exact('execution_resource_frozen', spec['execution_resource'])
    run_event = checkpoints._event(db, 'cooperative_unit_reserved', run, None, task['id'], attempt['id'])
    dispatch_event = checkpoints._event(db, 'work_dispatch_bound', dispatch, None, task['id'], attempt['id'])
    result_event = checkpoints._event(db, 'attempt_result_saved', result, None, task['id'], attempt['id'])
    if not created['seq'] < reserved['seq'] < frozen['seq'] < run_event['seq'] < dispatch_event['seq'] < result_event['seq']:
        raise ValueError('V3原任务创建、保留、派发与退出顺序不符。')
    prepared_events = []
    for attachment in spec.get('attachments', []):
        if attachment['route'] != 'native':
            raise ValueError('V3完整current尚未核实其它附件执行路径。')
        prepared = {k: attachment[k] for k in ('file', 'sha256', 'bytes', 'mime', 'route', 'delivery')}
        native_input = ((attachment['kind'] == 'image' and spec['provider'] == 'deepseek') or
            (spec['provider'] == 'codex' and attachment['delivery'] != 'NATIVE_FILE_TOOLS'))
        prepared.update(status='NATIVE_INPUT_PREPARED_NOT_COMPREHENSION' if native_input else 'AWAITING_NATIVE_TOOL_READ',
            comprehension='NOT_ATTESTED')
        prepared_events.append(exact('attachment_prepared', prepared))
    if result.get('execution') == 'codex_native':
        terminal = _codex(service, db, task, attempt, dispatch_event, result_event, exact, registry, dispatch)
    else:
        # Synthetic/local direct exits remain a bounded historical path. They
        # do not attest any native telemetry or permit a state-only allowlist.
        terminal = exact('attempt_state', {'previous': 'RESERVED', 'state': attempt['state']})
    if not dispatch_event['seq'] < terminal['seq'] < result_event['seq']:
        raise ValueError('V3原运行终态事件顺序不符。')
    if any(not dispatch_event['seq'] < e['seq'] < terminal['seq'] for e in prepared_events):
        raise ValueError('V3原附件准备顺序不符。')
    # A first-B delivery is written in the reservation transaction. Rebuild
    # its exact root-owned input/spec/material body before carrying its event.
    intent=run['input'].get('handoff_delivery_intent')
    if intent is not None:
        from .work_coordinator_handoff_records import DELIVERY,DELIVERY_KIND,WorkCoordinatorHandoffRecords,versioned
        records=WorkCoordinatorHandoffRecords(service)
        expected=versioned({'protocol':DELIVERY,'item_id':run['item_id'],'root':intent['root'],
            'marker':intent['marker'],'intent':intent,'run_id':run['id'],'run_version':digest(run),
            'input_version':run['input_version'],'task_spec_version':run['task_spec_version'],'materials':spec['materials']})
        rows=[v for v in service.collaboration.all(db,DELIVERY_KIND) if v.get('snapshot',{}).get('run_id')==run['id']]
        if len(rows)!=1 or records.get(db,DELIVERY_KIND,records.reference(rows[0]))['snapshot']!=expected:
            raise ValueError('V3原B派发缺少准确root交付与完整冻结意图。')
        delivery=exact('coordinator_handoff_delivered',rows[0])
        if not run_event['seq']<delivery['seq']<dispatch_event['seq']:
            raise ValueError('V3原B交付须属于预约之后、dispatch之前的准确事务。')
    # A session exception in the original saved audit must not admit an
    # unparsed runtime event. Close the whole task/attempt ledger through exit
    # here; later capture/proposal carriers are audited by the enclosing owner.
    complete = {**registry, ('cooperative_unit_reserved', run_event['seq']): (run, task['id'], attempt['id']),
        ('work_dispatch_bound', dispatch_event['seq']): (dispatch, task['id'], attempt['id']),
        ('attempt_result_saved', result_event['seq']): (result, task['id'], attempt['id'])}
    first_at = db.execute('SELECT at FROM events WHERE seq=?', (created['seq'],)).fetchone()[0]
    last_at = db.execute('SELECT at FROM events WHERE seq=?', (result_event['seq'],)).fetchone()[0]
    finite(first_at); finite(last_at)
    for row in db.execute('SELECT * FROM events WHERE (task_id=? OR attempt_id=?) AND seq<=? ORDER BY seq',
            (task['id'], attempt['id'], result_event['seq'])):
        finite(row['at'])
        if (row['seq'] < created['seq'] or not first_at <= row['at'] <= last_at or
                complete.get((row['kind'], row['seq'])) != (json.loads(row['payload']), row['task_id'], row['attempt_id'])):
            raise ValueError('V3原执行退出前存在未重建、错归属或越界事件：' + row['kind'])
    return registry


def _codex(service, db, task, attempt, dispatch, exited, exact, registry, dispatch_record):
    """Replay concrete primary observations and their deterministic effects.

    Process/config/account/token observations are ledger observations, not
    independent attestation of OS isolation, account identity or token billing.
    They never confer execution permission. Derived messages, terminal and
    state events must agree with the original terminal result and identities.
    """
    dispatch = db.execute('SELECT * FROM events WHERE seq=?', (dispatch['seq'],)).fetchone()
    exited = db.execute('SELECT * FROM events WHERE seq=?', (exited['seq'],)).fetchone()
    spec = task['spec']; result = attempt['result']
    if (spec['provider'] != 'codex' or spec.get('scene_snapshot') or attempt['stop_requested'] or
            attempt['state'] != 'COMPLETED' or result.get('protocol') != 'completed' or
            result.get('remote_model_state') != 'completed' or result.get('errors') != [] or
            type(result.get('native_tool_items')) is not int or result['native_tool_items'] < 0 or
            result.get('native_tool_limit_reached') is not False or
            result.get('native_tool_budget_overrun_items') != [] or result.get('known_tools_exited') is not True or
            type((result.get('owned_job') or {}).get('active_processes')) is not int or
            result['owned_job']['active_processes'] != 0):
        raise ValueError('V3 Codex事件只支持已核实的有限工具与补充输入首次成功回合。')
    if any(v.get('task_id') == task['id'] or v.get('attempt_id') == attempt['id']
            for v in service.collaboration.all(db, 'native_control')):
        raise ValueError('V3 Codex原生控制历史尚未接通。')
    if db.execute('SELECT COUNT(*) FROM attempts WHERE task_id=?', (task['id'],)).fetchone()[0] != 1:
        raise ValueError('V3合作任务须是原首次独立执行身份。')
    thread = attempt['session_id']; turn = attempt['turn_id']
    if not isinstance(thread, str) or not thread or not isinstance(turn, str) or not turn:
        raise ValueError('V3 Codex缺少原生thread/turn身份。')

    def rows(kind):
        found = []
        for row in db.execute('SELECT * FROM events WHERE kind=? AND (task_id=? OR attempt_id=?) ORDER BY seq',
                (kind, task['id'], attempt['id'])):
            if row['task_id'] != task['id'] or row['attempt_id'] != attempt['id']:
                raise ValueError('V3 Codex事件任务与尝试必须同时准确绑定。')
            finite(row['at'])
            if not dispatch['seq'] < row['seq'] < exited['seq'] or not dispatch['at'] <= row['at'] <= exited['at']:
                raise ValueError('V3 Codex事件越过原派发/退出边界。')
            found.append((row, json.loads(row['payload'])))
        return found

    def one(kind):
        found = rows(kind)
        if len(found) != 1:
            raise ValueError('V3 Codex缺少唯一实际事件：' + kind)
        return found[0]

    def fields(value, names):
        if not isinstance(value, dict) or set(value) != set(names.split()):
            raise ValueError('V3 Codex原生事件字段未核实。')

    def observe(kind, row, body):
        # A body is admitted only after this concrete interpreter validates
        # its full shape, binding, source path, ordering and derived effects.
        registry[(kind, row['seq'])] = (body, task['id'], attempt['id'])

    process, body = one('runtime_process_started')
    fields(body, 'pid alive creation job_controller kill_on_job_close execution')
    fields(body['job_controller'], 'pid alive creation')
    if (type(body['pid']) is not int or body['pid'] != attempt['pid'] or body['alive'] is not True or
            not isinstance(body['creation'], str) or not body['creation'].isdigit() or
            type(body['job_controller']['pid']) is not int or body['job_controller']['pid'] <= 0 or
            body['job_controller']['alive'] is not True or not isinstance(body['job_controller']['creation'], str) or
            not body['job_controller']['creation'].isdigit() or body['kill_on_job_close'] is not True or
            body['execution'] != 'codex_native'):
        raise ValueError('V3 Codex进程原身份或所属job观察不完整。')
    observe('runtime_process_started', process, body)
    account, identity = one('codex_account_identity_observed')
    fields(identity, 'fingerprint source status raw_identity_saved')
    fingerprint = identity['fingerprint']
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64 or
            any(c not in '0123456789abcdef' for c in fingerprint) or identity['status'] != 'OBSERVED' or
            identity['raw_identity_saved'] is not False or identity['source'] not in
            {'account/read.workspaceRouting', 'account/rateLimits/read.accountId'}):
        raise ValueError('V3 Codex原账号观察尚未准确绑定。')
    observe('codex_account_identity_observed', account, identity)
    config, configuration = one('runtime_configuration')
    fields(configuration, 'model tier selected_tier wire_tier auth execution native_policy observed_sandbox permission_profile resume')
    from .native import codex_tier_matches
    from .adapters import codex_wire_tier
    expected = {**configuration, 'model': spec['model'], 'selected_tier': spec['tier'],
        'wire_tier': codex_wire_tier(spec['tier']), 'auth': 'subscription', 'execution': 'codex_native',
        'native_policy': spec['native'], 'permission_profile': None, 'resume': False}
    sandbox = configuration['observed_sandbox']
    access = spec.get('access', 'inherit')
    sandbox_observation(sandbox, access)
    if (configuration != expected or not codex_tier_matches(spec['tier'], configuration['tier']) or
            access not in {'inherit', 'full', 'workspace'}):
        raise ValueError('V3 Codex原运行配置或sandbox观察未完整核实。')
    observe('runtime_configuration', config, configuration)
    bound = exact('codex_session_account_bound', {'session_id': thread, 'fingerprint': fingerprint,
        'actual_thread_response_validated': True, 'raw_identity_saved': False})
    from .native import prompt_for
    prompt = prompt_for(task, '', native_resume=False)
    sent_input = original_input(task, attempt, prompt)
    intent = exact('turn_send_intent', {'prompt_sha256': sha256(prompt.encode()).hexdigest(), 'execution': 'codex_native'})
    delivery = exact('attachment_delivery_intent', {'files': [i['file'] for i in spec.get('attachments', [])],
        'channel': 'CODEX_NATIVE_USER_INPUT', 'image_parts': sum(i['kind'] == 'image' for i in spec.get('attachments', [])),
        'comprehension': 'NOT_ATTESTED'})
    if not dispatch['seq'] < process['seq'] < account['seq'] < config['seq'] < bound['seq'] < intent['seq'] < delivery['seq']:
        raise ValueError('V3 Codex启动、账号、配置和原发送事件顺序不符。')

    # No states are accepted by their names alone: replay all exact transitions
    # with the original send, native turn and terminal protocol witnesses.
    states = rows('attempt_state')
    if (len(states) < 3 or [v for _, v in states[:2]] != [
            {'previous': 'RESERVED', 'state': 'SEND_INTENT'},
            {'previous': 'SEND_INTENT', 'state': 'RUNNING'}] or
            states[-1][1] != {'previous': 'RUNNING', 'state': 'COMPLETED'} or
            not intent['seq'] < states[0][0]['seq'] < delivery['seq'] < states[1][0]['seq']):
        raise ValueError('V3 Codex原完整attempt状态链未闭合。')
    for row, value in [*states[:2], states[-1]]: observe('attempt_state', row, value)

    def strings(value):
        if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
            raise ValueError('V3 Codex原生字符串数组无效。')

    def item_shape(item, starting):
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
            raise ValueError('V3 Codex原生item身份无效。')
        kind = item.get('type')
        if kind == 'agentMessage':
            # Preserve the previously checked minimal carrier and the complete
            # generated schema. A partially enriched shape is not either one.
            minimal = 'id type text phase'
            fields(item, minimal if set(item) == set(minimal.split()) else
                minimal + ' memoryCitation delivery questions')
            if (not isinstance(item['text'], str) or
                    item['phase'] not in ('commentary', 'partial_answer', 'final_answer', None)):
                raise ValueError('V3 Codex消息正文或phase无效。')
            citation = item.get('memoryCitation')
            if citation is not None:
                fields(citation, 'entries threadIds'); strings(citation['threadIds'])
                if not isinstance(citation['entries'], list):
                    raise ValueError('V3 Codex引用条目须是数组。')
                for entry in citation['entries']:
                    fields(entry, 'path lineStart lineEnd note')
                    if (not isinstance(entry['path'], str) or not isinstance(entry['note'], str) or
                            any(type(entry[k]) is not int or entry[k] < 0 for k in ('lineStart', 'lineEnd')) or
                            entry['lineEnd'] < entry['lineStart']):
                        raise ValueError('V3 Codex引用完整范围无效。')
            if item.get('delivery') not in (None, 'async'):
                raise ValueError('V3 Codex消息投递观察无效。')
            questions = item.get('questions')
            if questions is not None:
                if not isinstance(questions, list): raise ValueError('V3 Codex问题须是数组。')
                for question in questions:
                    fields(question, 'title options')
                    if not isinstance(question['title'], str): raise ValueError('V3 Codex问题标题无效。')
                    if question['options'] is not None: strings(question['options'])
        elif kind == 'plan':
            fields(item, 'id type text')
            if not isinstance(item['text'], str): raise ValueError('V3 Codex计划正文无效。')
        elif kind == 'reasoning':
            fields(item, 'id type summary content'); strings(item['summary']); strings(item['content'])
        elif kind == 'contextCompaction':
            fields(item, 'id type')
        elif kind == 'userMessage':
            user_message(item, sent_input)
        elif kind == 'commandExecution':
            command_observation(item, starting)
        elif kind == 'collabAgentToolCall':
            collab_observation(item, starting)
        elif kind == 'fileChange':
            file_change_observation(item, starting)
        elif kind in ('mcpToolCall', 'dynamicToolCall'):
            external_tool_observation(item, starting)
        else:
            raise ValueError('V3 Codex尚未开放此原生item路径。')

    messages = []; started = {}; completed = set(); deltas = {}; terminal = None; turn_started = None
    item_clocks = {}; reasoning_indices = []
    initial_turn = None; completed_items = {}; user_id = None
    tools = {}; tool_ends = {}; native_rows = rows('native_event')
    closing_row, _ = one('native_terminal_transport_closing')
    child_seqs, child_tools, child_ends, children, child_ids = child_events(
        native_rows, rows('native_child_session'), thread, states[1][0]['seq'],
        closing_row['seq'], item_shape, observe)
    for row, value in native_rows:
        if row['seq'] in child_seqs: continue
        fields(value, 'method params'); method = value['method']; params = value['params']
        if not isinstance(method, str) or not isinstance(params, dict) or params.get('threadId') != thread:
            raise ValueError('V3 Codex原生协议thread归属错误。')
        if 'turn' in params and not isinstance(params['turn'], dict):
            raise ValueError('V3 Codex原生turn须是完整对象。')
        actual_turn = params.get('turnId') or (params.get('turn') or {}).get('id')
        if actual_turn != turn or terminal is not None or not states[1][0]['seq'] < row['seq'] < states[-1][0]['seq']:
            raise ValueError('V3 Codex原生协议turn或顺序错误。')
        if method in {'turn/started', 'turn/completed'}:
            fields(params, 'threadId turn')
            turn_observation(params['turn'], 'inProgress' if method == 'turn/started' else result['protocol'],
                turn, [] if method == 'turn/started' else
                [completed_items[key] for key in started if key in completed_items], initial_turn)
            if method == 'turn/started':
                if turn_started is not None or started or params['turn']['status'] != 'inProgress':
                    raise ValueError('V3 Codex原turn开始重复或错序。')
                turn_started = row['seq']
                initial_turn = params['turn']
            else:
                if params['turn']['status'] != result['protocol']: raise ValueError('V3 Codex原终态不符。')
                terminal = row['seq']
        elif method in {'item/started', 'item/completed'}:
            clock = 'startedAtMs' if method == 'item/started' else 'completedAtMs'
            fields(params, 'threadId turnId item' if clock not in params else 'threadId turnId item ' + clock)
            item = params['item']; item_shape(item, method == 'item/started')
            identity = item['id']
            if clock in params:
                if type(params[clock]) is not int or params[clock] < 0:
                    raise ValueError('V3 Codexitem时间须是非负整数毫秒。')
                if method == 'item/completed' and identity in item_clocks and params[clock] < item_clocks[identity]:
                    raise ValueError('V3 Codexitem完成早于开始观察。')
            if method == 'item/started':
                if identity in child_ids: raise ValueError('V3主子会话item身份冲突。')
                if item['type'] == 'collabAgentToolCall' and item['senderThreadId'] != thread:
                    raise ValueError('V3主会话spawn发送者不符。')
                if identity in started or identity in completed: raise ValueError('V3 Codex消息开始重复。')
                if item['type'] == 'userMessage':
                    if user_id is not None or started:
                        raise ValueError('V3首次发送须是唯一且在其它item之前的用户消息。')
                    user_id = identity
                started[identity] = item['type']
                if item['type'] in TOOL_KINDS:
                    tools[identity] = (row, item)
                if clock in params: item_clocks[identity] = params[clock]
            else:
                if identity not in started or identity in completed: raise ValueError('V3 Codex消息完成缺少唯一开始。')
                if item['type'] != started[identity]: raise ValueError('V3 Codexitem类型在生命周期中变化。')
                if item['type'] in TOOL_KINDS:
                    stable_tool(tools[identity][1], item)
                if identity in tools: tool_ends[identity] = (row, item)
                if identity in deltas and ''.join(deltas[identity]) != item['text']: raise ValueError('V3 Codex增量与完整消息不符。')
                for target, field, index in reasoning_indices:
                    if target == identity and index >= len(item[field]):
                        raise ValueError('V3 Codex推理增量索引超出完整item。')
                completed.add(identity)
                completed_items[identity] = item
                if item['type'] == 'agentMessage': messages.append((row['seq'], item))
        elif method == 'item/commandExecution/outputDelta':
            fields(params, 'threadId turnId itemId delta')
            target = params['itemId']
            if (not isinstance(target, str) or started.get(target) != 'commandExecution' or
                    target in completed or not isinstance(params['delta'], str)):
                raise ValueError('V3命令输出增量未绑定原进行中命令。')
            # Streaming and aggregated output are separate primary observations;
            # the local schema gives no untruncated concatenation guarantee.
        elif method == 'item/mcpToolCall/progress':
            fields(params, 'threadId turnId itemId message')
            target = params['itemId']
            if (not isinstance(target, str) or started.get(target) != 'mcpToolCall' or
                    target in completed or not isinstance(params['message'], str)):
                raise ValueError('V3 MCP进度未绑定原进行中工具。')
        elif method == 'item/agentMessage/delta':
            fields(params, 'threadId turnId itemId delta')
            target = params['itemId']
            if (not isinstance(target, str) or started.get(target) != 'agentMessage' or
                    target in completed or not isinstance(params['delta'], str)):
                raise ValueError('V3 Codex消息增量归属不符。')
            deltas.setdefault(target, []).append(params['delta'])
        elif method in {'item/plan/delta', 'item/reasoning/summaryTextDelta',
                'item/reasoning/summaryPartAdded', 'item/reasoning/textDelta'}:
            plan = method == 'item/plan/delta'; part = method == 'item/reasoning/summaryPartAdded'
            index_key = 'contentIndex' if method == 'item/reasoning/textDelta' else 'summaryIndex'
            fields(params, 'threadId turnId itemId' + ('' if part else ' delta') + ('' if plan else ' ' + index_key))
            target = params['itemId']
            if (not isinstance(target, str) or started.get(target) != ('plan' if plan else 'reasoning') or
                    target in completed or not part and not isinstance(params['delta'], str)):
                raise ValueError('V3 Codex计划/推理增量归属错误。')
            if not plan:
                index = params[index_key]
                if type(index) is not int or index < 0: raise ValueError('V3 Codex推理索引无效。')
                reasoning_indices.append((target, 'content' if index_key == 'contentIndex' else 'summary', index))
            # These are primary streaming observations. In particular the
            # protocol explicitly disclaims plan-delta concatenation equality.
        else:
            raise ValueError('V3 Codex未核实原生协议路径：' + str(method))
        observe('native_event', row, value)
    if terminal is None or set(started) != completed:
        raise ValueError('V3 Codex原生协议缺少完整结束。')
    request_events(db, task, attempt, thread, turn, rows, observe, states, turn_started, terminal, tools, tool_ends, dispatch_record)
    if result['native_tool_items'] != len(tools):
        raise ValueError('V3原退出工具数与实际开始观察不符。')
    actual_tools = {r['id']: dict(r) for r in db.execute(
        'SELECT * FROM tool_calls WHERE attempt_id=?', (attempt['id'],))}
    all_tools = {**tools, **child_tools}; all_ends = {**tool_ends, **child_ends}
    bind_spawns(all_tools, all_ends, children)
    expected_tools = {}
    for identity, (_, item) in all_tools.items():
        call_id = attempt['id'] + ':native:' + identity
        expected_tools[call_id] = {'id': call_id, 'attempt_id': attempt['id'], 'hash': digest(item),
            'state': 'COMPLETED', 'result': canonical(all_ends[identity][1])}
    tool_events = rows('native_tool_started')
    if len(tool_events) != len(tools): raise ValueError('V3工具开始派生记录缺失或重复。')
    for (identity, (source, item)), (row, body) in zip(tools.items(), tool_events):
        count = sum(r['seq'] <= source['seq'] for r, _ in all_tools.values())
        end, _ = tool_ends[identity]
        call_id = attempt['id'] + ':native:' + identity
        next_source = next(r['seq'] for r, _ in native_rows if r['seq'] > source['seq'])
        fields(body, 'item call_id task_tool_count')
        if (canonical(body) != canonical({'item': item, 'call_id': call_id, 'task_tool_count': count}) or
                not source['seq'] < row['seq'] < next_source <= end['seq']):
            raise ValueError('V3工具开始摘要、原计数、归属或派生顺序不符。')
        observe('native_tool_started', row, body)
    if actual_tools != expected_tools:
        raise ValueError('V3工具完整账本与原开始摘要或结束结果不符。')
    expected_messages = [{'text': item['text'], 'phase': item['phase']} for _, item in messages]
    actual_messages = rows('model_message')
    if [v for _, v in actual_messages] != expected_messages or any(not source < row['seq'] < terminal
            for (source, _), (row, _) in zip(messages, actual_messages)):
        raise ValueError('V3 Codex消息派生事件不符。')
    for row, value in actual_messages: observe('model_message', row, value)
    actual_deltas = rows('model_delta')
    source_deltas = [(row, value['params']['delta']) for row, value in rows('native_event')
        if row['seq'] not in child_seqs and value['method'] == 'item/agentMessage/delta']
    if [v for _, v in actual_deltas] != [{'text': text} for _, text in source_deltas] or any(
            not source['seq'] < row['seq'] < terminal for (source, _), (row, _) in zip(source_deltas, actual_deltas)):
        raise ValueError('V3 Codex消息增量派生事件不符。')
    for row, value in actual_deltas: observe('model_delta', row, value)
    answer = ''
    for _, message in messages:
        if message['phase'] == 'final_answer' or not answer: answer = message['text']
    if answer != result['answer']: raise ValueError('V3 Codex完整原回答与退出记录不符。')
    protocol = exact('protocol_terminal', {'status': result['protocol'], 'thread_id': thread, 'turn_id': turn})
    closing = exact('native_terminal_transport_closing', {'unprocessed_local_frames': False,
        'not_yet_received_frames': 'UNKNOWN', 'no_post_terminal_action_grants': True})
    # native.receive records only tokenUsage, after matching its current thread.
    # The original carrier loses the wire thread/turn, so reconstruct an
    # attempt-scoped observation only, never a per-turn/billing assertion.
    # Identical snapshots may recur; each original seq remains a distinct row.
    for row, usage in rows('native_token_usage_observation'):
        fields(usage, 'total last modelContextWindow')
        for name in ('total', 'last'):
            counts = usage[name]
            fields(counts, 'totalTokens inputTokens cachedInputTokens cacheWriteInputTokens outputTokens reasoningOutputTokens')
            if any(type(v) is not int or v < 0 for v in counts.values()):
                raise ValueError('V3 Codex用量观察须是完整非负整数计数。')
        window = usage['modelContextWindow']
        if window is not None and (type(window) is not int or window <= 0):
            raise ValueError('V3 Codex上下文窗口观察无效。')
        if not bound['seq'] < row['seq'] < closing['seq']:
            raise ValueError('V3 Codex用量观察越过原session绑定或输运关闭。')
        observe('native_token_usage_observation', row, usage)
    if not terminal < protocol['seq'] < closing['seq'] < states[-1][0]['seq'] < exited['seq']:
        raise ValueError('V3 Codex原生终态、输运关闭与退出顺序不符。')
    return states[-1][0]
