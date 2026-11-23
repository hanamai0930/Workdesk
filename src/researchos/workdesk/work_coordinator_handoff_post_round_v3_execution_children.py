"""Bounded child observations from native.receive; these never authorize spawn."""
import math
from pathlib import PurePosixPath, PureWindowsPath

from .store import canonical
from .work_coordinator_handoff_post_round_v3_execution_shapes import fields, turn_observation


TOOL_KINDS = ('commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall', 'collabAgentToolCall')


def text(value, nullable=False):
    if not isinstance(value, str) and not (nullable and value is None):
        raise ValueError('V3子会话文本观察无效。')


def thread_observation(value, known):
    fields(value, 'id sessionId forkedFromId parentThreadId preview ephemeral section sectionEnteredAt '
        'projectId historyMode modelProvider model reasoningEffort createdAt updatedAt recencyAt status '
        'path cwd cliVersion originator source threadSource agentNickname agentRole gitInfo name turns')
    for key in ('id', 'sessionId', 'preview', 'modelProvider', 'cwd', 'cliVersion'):
        text(value[key])
    for key in ('forkedFromId', 'projectId', 'model', 'reasoningEffort', 'path', 'originator',
            'threadSource', 'agentNickname', 'agentRole', 'name'):
        text(value[key], True)
    if (not value['id'] or not value['sessionId'] or value['id'] in known or
            type(value['ephemeral']) is not bool or value['historyMode'] not in ('legacy', 'paginated') or
            value['forkedFromId'] is not None or value['turns'] != []):
        raise ValueError('V3子会话首次完整Thread身份无效。')
    path = value['cwd']
    if ('\x00' in path or not (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()) or
            '..' in PureWindowsPath(path).parts or '..' in PurePosixPath(path).parts):
        raise ValueError('V3子会话cwd须是绝对路径观察。')
    for key in ('createdAt', 'updatedAt', 'recencyAt', 'sectionEnteredAt'):
        v = value[key]
        if v is None and key in ('recencyAt', 'sectionEnteredAt'): continue
        if type(v) not in (int, float) or not math.isfinite(v) or v < 0:
            raise ValueError('V3子会话时间观察无效。')
    if value['updatedAt'] < value['createdAt']:
        raise ValueError('V3子会话更新时间早于创建。')
    status = value['status']
    if not isinstance(status, dict) or status.get('type') not in ('notLoaded', 'idle', 'systemError', 'active'):
        raise ValueError('V3子会话状态观察无效。')
    fields(status, 'type activeFlags' if status['type'] == 'active' else 'type')
    if status['type'] == 'active' and (not isinstance(status['activeFlags'], list) or
            any(v not in ('waitingOnApproval', 'waitingOnUserInput') for v in status['activeFlags'])):
        raise ValueError('V3子会话activeFlags观察无效。')
    if value['section'] is not None:
        fields(value['section'], 'id name appearance')
        for key in ('id', 'name'): text(value['section'][key])
        appearance = value['section']['appearance']
        if appearance is not None:
            fields(appearance, 'icon color')
            for v in appearance.values(): text(v, True)
    if value['gitInfo'] is not None:
        fields(value['gitInfo'], 'sha branch originUrl')
        for v in value['gitInfo'].values(): text(v, True)
    fields(value['source'], 'subAgent'); fields(value['source']['subAgent'], 'thread_spawn')
    source = value['source']['subAgent']['thread_spawn']
    fields(source, 'parent_thread_id depth agent_path agent_nickname agent_role')
    parent = source['parent_thread_id']
    if (not isinstance(parent, str) or parent not in known or value['parentThreadId'] != parent or
            type(source['depth']) is not int or source['depth'] != known[parent] + 1):
        raise ValueError('V3子会话父身份或深度未绑定原树。')
    for key in ('agent_path', 'agent_nickname', 'agent_role'): text(source[key], True)
    return parent, source['depth']


def collab_observation(item, starting):
    # One concrete operation is open. Other collab operations need their own
    # receiver/state/lifecycle contract, even though their schema is similar.
    fields(item, 'type id tool status senderThreadId receiverThreadIds prompt model reasoningEffort agentsStates')
    if item['type'] != 'collabAgentToolCall' or item['tool'] != 'spawnAgent':
        raise ValueError('V3尚未核实此collab操作。')
    text(item['senderThreadId'])
    for key in ('prompt', 'model', 'reasoningEffort'): text(item[key], True)
    receivers = item['receiverThreadIds']; states = item['agentsStates']
    if (not isinstance(receivers, list) or any(not isinstance(v, str) or not v for v in receivers) or
            len(set(receivers)) != len(receivers) or not isinstance(states, dict)):
        raise ValueError('V3子会话接收者完整观察无效。')
    if starting:
        if item['status'] != 'inProgress' or receivers or states:
            raise ValueError('V3 spawn开始须无结束接收者观察。')
    elif item['status'] != 'completed' or len(receivers) != 1 or set(states) != set(receivers):
        raise ValueError('V3 spawn结束须是单个完整接收者观察。')
    for state in states.values():
        fields(state, 'status message'); text(state['message'], True)
        if state['status'] not in ('pendingInit', 'running', 'interrupted', 'completed', 'errored', 'shutdown', 'notFound'):
            raise ValueError('V3 agent状态观察无效。')


def stable_tool(source, item):
    if source['type'] in ('mcpToolCall', 'dynamicToolCall'):
        mutable = ('status', 'durationMs', 'result', 'error') if source['type'] == 'mcpToolCall' else (
            'status', 'durationMs', 'contentItems', 'success')
        if canonical({k: v for k, v in source.items() if k not in mutable}) != canonical(
                {k: v for k, v in item.items() if k not in mutable}):
            raise ValueError('V3外部工具原始身份、参数或元数据发生变化。')
        return
    keys = {
        'commandExecution': ('type', 'id', 'pluginId', 'scriptPath', 'command', 'cwd', 'source', 'commandActions'),
        'fileChange': ('type', 'id', 'changes'),
        'collabAgentToolCall': ('type', 'id', 'tool', 'senderThreadId', 'prompt', 'model', 'reasoningEffort'),
    }[source['type']]
    if any(canonical(source[k]) != canonical(item[k]) for k in keys) or (
            source['type'] == 'commandExecution' and source['processId'] is not None and source['processId'] != item['processId']):
        raise ValueError('V3工具开始与完成的原始字段发生变化。')


def child_events(native_rows, sessions, root, begin, closing, item_shape, observe):
    """One successful complete turn per observed child, including nested children.

    Child receive bypasses primary model/message/count effects. Its tools share
    the attempt table, so their rows and insertion order are returned separately.
    """
    known = {root: 0}; children = {}; excluded = set(); tools = {}; ends = {}; claimed = {}; tree_session = None
    session_rows = iter(sessions); session = next(session_rows, None)
    for row, body in native_rows:
        fields(body, 'method params'); method = body['method']; params = body['params']
        if not isinstance(params, dict): raise ValueError('V3子会话params无效。')
        if method == 'thread/started':
            fields(params, 'thread'); value = params['thread']; parent, depth = thread_observation(value, known)
            identity = value['id']
            if tree_session is not None and value['sessionId'] != tree_session:
                raise ValueError('V3子会话sessionId须一致绑定原观察树。')
            tree_session = value['sessionId']
            if (session is None or session[1] != {'session_id': identity, 'parent_session_id': parent} or
                    not begin < session[0]['seq'] < row['seq'] < closing):
                raise ValueError('V3子会话派生记录缺失、错序或错归属。')
            session_seq = session[0]['seq']; observe('native_child_session', *session)
            session = next(session_rows, None); known[identity] = depth
            children[identity] = {'parent': parent, 'seq': row['seq'], 'session_seq': session_seq, 'turn': None, 'done': False,
                'started': {}, 'completed': {}, 'clocks': {}, 'deltas': {}}
        else:
            identity = params.get('threadId')
            if identity == root: continue
            if identity not in children: raise ValueError('V3子会话事件缺少原始Thread绑定。')
            child = children[identity]
            if child['done'] or not child['seq'] < row['seq'] < closing:
                raise ValueError('V3子会话事件越过原生命周期。')
            if method in ('turn/started', 'turn/completed'):
                fields(params, 'threadId turn'); value = params['turn']
                if not isinstance(value, dict) or not isinstance(value.get('id'), str) or not value['id']:
                    raise ValueError('V3子会话Turn身份无效。')
                if method == 'turn/started':
                    if child['turn'] is not None: raise ValueError('V3子会话重复回合尚未核实。')
                    turn_observation(value, 'inProgress', value['id'], [])
                    child.update(turn=value['id'], initial=value)
                else:
                    if child['turn'] is None or set(child['started']) != set(child['completed']):
                        raise ValueError('V3子会话结束缺少完整开始或item。')
                    turn_observation(value, 'completed', child['turn'],
                        [child['completed'][k] for k in child['started']], child['initial'])
                    child['done'] = True
            elif method in ('item/started', 'item/completed'):
                starting = method == 'item/started'; clock = 'startedAtMs' if starting else 'completedAtMs'
                fields(params, 'threadId turnId item' + (' ' + clock if clock in params else ''))
                if child['turn'] is None or params['turnId'] != child['turn']:
                    raise ValueError('V3子会话item缺少原回合绑定。')
                item = params['item']; item_shape(item, starting); key = item['id']
                if item['type'] not in (*TOOL_KINDS, 'agentMessage', 'contextCompaction'):
                    raise ValueError('V3子会话此item尚未核实。')
                if item['type'] == 'collabAgentToolCall' and item['senderThreadId'] != identity:
                    raise ValueError('V3子会话spawn发送者不符。')
                if clock in params and (type(params[clock]) is not int or params[clock] < 0 or
                        not starting and params[clock] < child['clocks'].get(key, 0)):
                    raise ValueError('V3子会话item时间观察无效。')
                if starting:
                    if key in claimed: raise ValueError('V3子会话item共享账本身份冲突。')
                    claimed[key] = identity; child['started'][key] = item
                    if clock in params: child['clocks'][key] = params[clock]
                    if item['type'] in TOOL_KINDS: tools[key] = (row, item)
                else:
                    source = child['started'].get(key)
                    if source is None or key in child['completed'] or item['type'] != source['type']:
                        raise ValueError('V3子会话item结束缺少唯一原始开始。')
                    if item['type'] in TOOL_KINDS: stable_tool(source, item); ends[key] = (row, item)
                    if key in child['deltas'] and ''.join(child['deltas'][key]) != item['text']:
                        raise ValueError('V3子会话消息增量与完整正文不符。')
                    child['completed'][key] = item
            elif method in ('item/agentMessage/delta', 'item/commandExecution/outputDelta', 'item/mcpToolCall/progress'):
                progress = method == 'item/mcpToolCall/progress'; payload = 'message' if progress else 'delta'
                fields(params, 'threadId turnId itemId ' + payload)
                key = params['itemId']; expected = {'item/agentMessage/delta': 'agentMessage',
                    'item/commandExecution/outputDelta': 'commandExecution', 'item/mcpToolCall/progress': 'mcpToolCall'}[method]
                if (child['turn'] is None or params['turnId'] != child['turn'] or not isinstance(key, str) or
                        child['started'].get(key, {}).get('type') != expected or key in child['completed'] or
                        not isinstance(params[payload], str)):
                    raise ValueError('V3子会话增量缺少原进行中item绑定。')
                if expected == 'agentMessage': child['deltas'].setdefault(key, []).append(params[payload])
            else:
                raise ValueError('V3子会话此通知路径尚未核实。')
        excluded.add(row['seq']); observe('native_event', row, body)
    if session is not None or any(not child['done'] for child in children.values()):
        raise ValueError('V3子会话原完整生命周期未闭合。')
    return excluded, tools, ends, children, claimed


def bind_spawns(tools, ends, children):
    bound = set()
    for key, (start, item) in tools.items():
        if item['type'] != 'collabAgentToolCall': continue
        end, value = ends[key]; receiver = value['receiverThreadIds'][0]
        child = children.get(receiver)
        if (receiver in bound or child is None or child['parent'] != item['senderThreadId'] or
                not start['seq'] < child['session_seq'] < child['seq'] < end['seq']):
            raise ValueError('V3 spawn接收者须绑定原始唯一父子创建观察。')
        bound.add(receiver)
    if bound != set(children):
        raise ValueError('V3子会话缺少原spawn工具生命周期。')
