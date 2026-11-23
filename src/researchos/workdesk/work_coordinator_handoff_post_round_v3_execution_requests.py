"""Serial primary inputs/ordinary approvals and exact native.resolve proof.

Only asynchronous successful reply/resolution or unanswered server clearance is
covered. Child requests, policy/session grants, other control actions, expiry and
ambiguous sends retain closed contracts. A recorded request never grants an action.
"""
import json
from uuid import UUID

from .native import response_for
from .store import canonical, digest
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_post_round_v3_execution_shapes import fields
from .work_coordinator_handoff_post_round_v3_execution_permissions import permission_params, permission_answers


def approval_params(method, params, thread, turn, tools, ends, wait, resolution):
    """Bind a complete ordinary approval to its actual in-progress tool.

    Preserve the generated schema's optional omissions and explicit nulls in
    the original DB/event carrier. Non-null command descriptions must match
    the actual full tool. Policy/session/environment extensions remain closed.
    """
    command = method == 'item/commandExecution/requestApproval'
    if not command and method != 'item/fileChange/requestApproval':
        raise ValueError('V3尚未核实此审批方法。')
    required = set('threadId turnId itemId startedAtMs workdeskItem'.split())
    optional = {'reason'}
    if command:
        required.update(('kind', 'environmentId'))
        optional.update(('approvalId', 'networkApprovalContext', 'command', 'cwd', 'commandActions',
            'proposedExecpolicyAmendment', 'proposedNetworkPolicyAmendments'))
    else:
        optional.add('grantRoot')
    if not isinstance(params, dict) or not required <= set(params) or set(params) - required - optional:
        raise ValueError('V3普通审批原字段须符合已核实的可选合同。')
    identity = params['itemId']
    if (params['threadId'] != thread or params['turnId'] != turn or
            not isinstance(identity, str) or identity not in tools or identity not in ends or
            type(params['startedAtMs']) is not int or params['startedAtMs'] < 0 or
            params.get('reason') is not None and not isinstance(params['reason'], str)):
        raise ValueError('V3审批须绑定原主回合工具和完整开始时间。')
    start, item = tools[identity]; end, _ = ends[identity]
    if (item['type'] != ('commandExecution' if command else 'fileChange') or
            canonical(params['workdeskItem']) != canonical(item) or
            not start['seq'] < wait['seq'] < resolution['seq'] < end['seq']):
        raise ValueError('V3审批原workdeskItem或工具生命周期不符。')
    if command:
        if (params['kind'] != 'command' or any(params.get(k) is not None for k in
                ('approvalId', 'environmentId', 'networkApprovalContext',
                 'proposedExecpolicyAmendment', 'proposedNetworkPolicyAmendments')) or
                any(params.get(k) is not None and canonical(params[k]) != canonical(item[k])
                    for k in ('command', 'cwd', 'commandActions'))):
            raise ValueError('V3普通命令审批未绑定准确命令或包含未核实扩展。')
    elif params.get('grantRoot') is not None:
        raise ValueError('V3文件审批尚未核实会话根授权。')


def input_params(params, thread, turn):
    fields(params, 'threadId turnId itemId questions isBlocking autoResolutionMs')
    if (params['threadId'] != thread or params['turnId'] != turn or
            not isinstance(params['itemId'], str) or not params['itemId'] or type(params['isBlocking']) is not bool):
        raise ValueError('V3补充输入请求未绑定准确主回合。')
    duration = params['autoResolutionMs']
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ValueError('V3补充输入自动结束时间观察无效。')
    questions = params['questions']; identities = set()
    if not isinstance(questions, list) or not questions: raise ValueError('V3补充输入须有完整问题。')
    for q in questions:
        fields(q, 'id header question isOther isSecret options')
        if (any(not isinstance(q[k], str) for k in ('id', 'header', 'question')) or not q['id'] or
                q['id'] in identities or any(type(q[k]) is not bool for k in ('isOther', 'isSecret'))):
            raise ValueError('V3补充输入问题身份或标志无效。')
        identities.add(q['id'])
        if q['options'] is not None:
            if not isinstance(q['options'], list): raise ValueError('V3补充输入选项须是数组。')
            for option in q['options']:
                fields(option, 'label description')
                if any(not isinstance(v, str) for v in option.values()): raise ValueError('V3补充输入选项文本无效。')


def request_events(db, task, attempt, thread, turn, rows, observe, states, turn_started, terminal, tools, ends, dispatch):
    """Rebuild persisted requests, exact state effects, replies and resolutions."""
    actual = db.execute('SELECT * FROM native_requests WHERE task_id=? OR attempt_id=? ORDER BY created,id',
        (task['id'], attempt['id'])).fetchall()
    waiting = rows('native_request_waiting'); responses = rows('native_human_response_recorded')
    sends = rows('native_response_sent'); resolved = rows('native_server_request_resolved')
    if len(actual) != len(waiting) or len(resolved) != len(waiting):
        raise ValueError('V3补充输入原请求与完整清除记录不一致。')
    middle = list(states[2:-1]); repeated = False
    if middle and middle[0][1] == {'previous': 'RUNNING', 'state': 'RUNNING'}:
        row, body = middle.pop(0)
        if turn_started is None or not turn_started < row['seq'] < terminal:
            raise ValueError('V3重复RUNNING缺少实际turn/started。')
        observe('attempt_state', row, body); repeated = True
    if len(middle) != len(waiting) * 2:
        raise ValueError('V3原状态链包含未重建的交互转换。')
    response_by_id = {}; send_by_id = {}; control_by_id = {}
    for row, body in rows('work_item_control_requested'):
        fields(body, 'work_item_id item_revision action task_id attempt_id owner dispatch_id request_id')
        identity = body['request_id']
        if not isinstance(identity, str) or identity in control_by_id:
            raise ValueError('V3业务任务答复控制身份重复或无效。')
        control_by_id[identity] = (row, body)
    for row, body in responses:
        fields(body, 'request_id response delivery source')
        identity = body['request_id']
        if not isinstance(identity, str) or identity in response_by_id:
            raise ValueError('V3原Human回复身份重复或无效。')
        response_by_id[identity] = (row, body)
    for row, body in sends:
        fields(body, 'request_id delivery'); identity = body['request_id']
        if not isinstance(identity, str) or identity in send_by_id:
            raise ValueError('V3原回复投递身份重复或无效。')
        send_by_id[identity] = (row, body)
    responded = set(); controlled = set(); previous_end = states[1][0]['seq']
    if repeated: previous_end = states[2][0]['seq']
    for index, ((wait, body), original, (resolution, wire)) in enumerate(zip(waiting, actual, resolved)):
        fields(body, 'request_id method params silence_is_approval')
        identity = body['request_id']
        try:
            if not isinstance(identity, str) or str(UUID(identity)) != identity: raise ValueError('V3请求UUID无效。')
        except (TypeError, AttributeError) as exc: raise ValueError('V3请求UUID无效。') from exc
        params = body['params']; method = body['method']; is_input = method == 'item/tool/requestUserInput'
        is_permission = method == 'item/permissions/requestApproval'
        if is_input: input_params(params, thread, turn)
        elif is_permission: permission_params(params, thread, turn, attempt)
        else: approval_params(method, params, thread, turn, tools, ends, wait, resolution)
        if (body['silence_is_approval'] is not False or
                original['id'] != identity or original['task_id'] != task['id'] or
                original['attempt_id'] != attempt['id'] or original['method'] != body['method'] or
                original['params'] != canonical(params)):
            raise ValueError('V3补充输入原DB与等待事件未完整绑定。')
        rpc_id = json.loads(original['rpc_id'])
        if type(rpc_id) not in (str, int) or original['rpc_id'] != canonical(rpc_id):
            raise ValueError('V3补充输入RPC身份无效。')
        fields(wire, 'threadId requestId')
        if wire['threadId'] != thread or canonical(wire['requestId']) != canonical(rpc_id):
            raise ValueError('V3服务端清除未绑定原始RPC请求。')
        start_state, end_state = middle[index * 2:index * 2 + 2]
        waiting_state = 'WAITING_INPUT' if is_input else 'WAITING_APPROVAL'
        if (start_state[1] != {'previous': 'RUNNING', 'state': waiting_state} or
                end_state[1] != {'previous': waiting_state, 'state': 'RUNNING'} or
                not previous_end < start_state[0]['seq'] < wait['seq'] < end_state[0]['seq'] < resolution['seq'] < terminal):
            raise ValueError('V3补充输入原状态转换或串行顺序不符。')
        finite(original['created']); finite(original['resolved'])
        if not states[1][0]['at'] <= original['created'] <= start_state[0]['at'] <= wait['at']:
            raise ValueError('V3补充输入原创建时间不符。')
        if not wait['at'] <= original['resolved'] <= resolution['at']:
            raise ValueError('V3补充输入原结束时间不符。')
        if identity in response_by_id:
            response, value = response_by_id[identity]
            if identity not in send_by_id: raise ValueError('V3原Human回复缺少实际投递观察。')
            sent, send = send_by_id[identity]
            answer = value['response']
            if is_permission:
                original_answers = permission_answers(params, answer)
            else:
                fields(answer, 'answers' if is_input else 'decision')
                if not is_input and answer['decision'] not in ('accept', 'decline', 'cancel'):
                    raise ValueError('V3审批尚未核实会话或策略扩展决定。')
                original_answers = [answer]
            if any(canonical(response_for(method, params, original)) != canonical(answer)
                    for original in original_answers):
                raise ValueError('V3原Human答案与规范化传输答复不符。')
            if (value['source'] not in ('LOCAL_CONTROL', 'HUMAN_WORK_ITEM') or value['delivery'] != 'NOT_YET_SENT' or
                    send != {'request_id': identity, 'delivery': 'WRITE_RETURNED; server_resolution_separate'} or
                    original['state'] != 'RESOLVED' or original['response'] != canonical(answer) or
                    not wait['seq'] < response['seq'] < sent['seq'] < end_state[0]['seq'] < resolution['seq']):
                raise ValueError('V3原Human回复、投递与服务端确认不符。')
            # Each source has its own original command and complete receipt.
            # A source label cannot substitute for the actual route's effects.
            payload = {'task_id': task['id'], 'attempt_id': attempt['id'], 'owner': attempt['owner'],
                'request_id': identity, 'response': answer}
            receipt = {'request_id': identity, 'state': 'READY', 'delivery': 'NOT_YET_SENT'}
            command_kind = 'native.resolve'; control = None
            if value['source'] == 'HUMAN_WORK_ITEM':
                context = {'work_item_id': dispatch['item_id'], 'item_revision': dispatch['item_revision'],
                    'action': 'native-response', **{k: payload[k] for k in ('task_id', 'attempt_id', 'owner')},
                    'dispatch_id': dispatch['id'], 'request_id': identity}
                if identity not in control_by_id or control_by_id[identity][1] != context:
                    raise ValueError('V3业务任务答复缺少准确派发/业务版本/运行控制记录。')
                control, control_body = control_by_id[identity]
                if not response['seq'] < control['seq'] < sent['seq']:
                    raise ValueError('V3业务任务控制记录须在原回复提交后且实际投递前。')
                payload = {'id': context['work_item_id'], 'revision': context['item_revision'],
                    'action': context['action'], 'dispatch_id': context['dispatch_id'], **payload}
                receipt['work_item_control'] = context; command_kind = 'work-item.control'
                controlled.add(identity)
            elif identity in control_by_id:
                raise ValueError('V3本地答复不能冒认业务任务控制记录。')
            commands = []
            for original_answer in original_answers:
                original_payload = {**payload, 'response': original_answer}
                commands.extend(db.execute('SELECT * FROM commands WHERE kind=? AND payload_hash=?',
                    (command_kind, digest(original_payload))).fetchall())
            if len(commands) != 1 or commands[0]['receipt'] != canonical(receipt):
                raise ValueError('V3原Human答复缺少准确命令摘要与完整回执。')
            command = commands[0]; finite(command['created'])
            try:
                if str(UUID(command['id'])) != command['id']: raise ValueError('V3原Human命令UUID无效。')
            except (TypeError, AttributeError) as exc: raise ValueError('V3原Human命令UUID无效。') from exc
            if not response['at'] <= command['created'] <= sent['at'] <= original['resolved']:
                raise ValueError('V3原Human命令提交与投递时间不符。')
            if control is not None:
                if not response['at'] <= control['at'] <= command['created']:
                    raise ValueError('V3原业务任务控制与命令提交时间不符。')
                observe('work_item_control_requested', control, control_body)
            responded.add(identity); observe('native_human_response_recorded', response, value)
            observe('native_response_sent', sent, send)
        elif original['state'] != 'CLEARED_UNANSWERED' or original['response'] is not None or identity in send_by_id:
            raise ValueError('V3未答复请求须完整记录服务端无回复清除。')
        for row, value in (start_state, end_state): observe('attempt_state', row, value)
        observe('native_request_waiting', wait, body); observe('native_server_request_resolved', resolution, wire)
        previous_end = resolution['seq']
    if set(response_by_id) != responded or set(send_by_id) != responded or set(control_by_id) != controlled:
        raise ValueError('V3存在未绑定原请求的Human回复或投递。')
