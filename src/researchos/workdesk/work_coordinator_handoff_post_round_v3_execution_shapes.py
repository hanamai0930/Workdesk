"""Finite observations for the successful first-turn execution replay.

These shapes describe returned observations. They neither attest OS isolation
nor grant requests, tool execution, file reads or successor runtime permission.
"""
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath


def fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('V3 Codex完整观察字段未核实。')


def command_observation(item, starting):
    """Complete local command schema; returned text never grants execution.

    Legacy path strings retain their wire spelling. Output/duration/exit code
    are observations, not a claim about success of the enclosing native turn.
    """
    fields(item, 'type id pluginId scriptPath command cwd processId source status '
        'commandActions aggregatedOutput exitCode durationMs')
    for key in ('id', 'command', 'cwd'):
        if not isinstance(item[key], str): raise ValueError('V3命令文本字段无效。')
    for key in ('pluginId', 'scriptPath', 'processId', 'aggregatedOutput'):
        if item[key] is not None and not isinstance(item[key], str):
            raise ValueError('V3命令可空文本观察无效。')
    if item['source'] not in ('agent', 'userShell', 'unifiedExecStartup', 'unifiedExecInteraction'):
        raise ValueError('V3命令来源观察尚未核实。')
    if item['status'] not in (('inProgress',) if starting else ('completed', 'failed', 'declined')):
        raise ValueError('V3命令生命周期状态无效。')
    for key in ('exitCode', 'durationMs'):
        if item[key] is not None and (type(item[key]) is not int or key == 'durationMs' and item[key] < 0):
            raise ValueError('V3命令退出码或耗时须是准确整数观察。')
        if starting and item[key] is not None:
            raise ValueError('V3首次命令开始不能包含结束观察。')
    if not isinstance(item['commandActions'], list): raise ValueError('V3命令动作须是数组。')
    for action in item['commandActions']:
        if not isinstance(action, dict): raise ValueError('V3命令动作须是完整对象。')
        kind = action.get('type')
        if not isinstance(kind, str): raise ValueError('V3命令动作类型须是字符串。')
        names = {'read': 'type command name path', 'listFiles': 'type command path',
            'search': 'type command query path', 'unknown': 'type command'}.get(kind)
        if names is None: raise ValueError('V3命令动作类型尚未核实。')
        fields(action, names)
        for key in names.split():
            if not isinstance(action[key], str) and not (
                    key in ('path', 'query') and kind in ('listFiles', 'search') and action[key] is None):
                raise ValueError('V3命令动作完整文本字段无效。')


def file_change_observation(item, starting):
    """Complete patch observation; paths/diffs neither grant nor apply writes."""
    fields(item, 'type id changes status')
    if item['type'] != 'fileChange' or not isinstance(item['id'], str) or not item['id']:
        raise ValueError('V3文件变更身份无效。')
    if item['status'] not in (('inProgress',) if starting else ('completed', 'failed', 'declined')):
        raise ValueError('V3文件变更生命周期状态无效。')
    if not isinstance(item['changes'], list):
        raise ValueError('V3文件变更须是完整数组。')
    for change in item['changes']:
        fields(change, 'path kind diff')
        if not isinstance(change['path'], str) or not isinstance(change['diff'], str):
            raise ValueError('V3文件路径与差异须是原文本观察。')
        kind = change['kind']
        if not isinstance(kind, dict) or kind.get('type') not in ('add', 'delete', 'update'):
            raise ValueError('V3文件变更种类尚未核实。')
        fields(kind, 'type move_path' if kind['type'] == 'update' else 'type')
        if kind['type'] == 'update' and kind['move_path'] is not None and not isinstance(kind['move_path'], str):
            raise ValueError('V3文件移动路径须是文本或null。')


def json_observation(value):
    """Validate only a designated JsonValue payload, never an event or permit."""
    stack = [(value, False)]; ancestors = set()
    while stack:
        part, exiting = stack.pop()
        if exiting:
            ancestors.remove(id(part)); continue
        if part is None or type(part) in (bool, str, int): continue
        if type(part) is float:
            if not math.isfinite(part): raise ValueError('V3工具JSON数值须是有限数。')
            continue
        if type(part) not in (dict, list) or id(part) in ancestors:
            raise ValueError('V3工具结果须是完整无环JSON。')
        if type(part) is dict and any(type(key) is not str for key in part):
            raise ValueError('V3工具JSON对象键须是字符串。')
        ancestors.add(id(part)); stack.append((part, True))
        stack.extend((v, False) for v in (part.values() if type(part) is dict else part))


def external_tool_observation(item, starting):
    """Full MCP/dynamic observations; embedded content is passive JSON data."""
    mcp = item.get('type') == 'mcpToolCall'
    if not mcp and item.get('type') != 'dynamicToolCall':
        raise ValueError('V3外部工具类型尚未核实。')
    names = ('type id server tool status arguments appContext mcpAppUi pluginId readOnlyHint result error durationMs'
        if mcp else 'type id namespace tool arguments status contentItems success durationMs')
    if mcp and 'mcpAppResourceUri' in item: names += ' mcpAppResourceUri'
    fields(item, names)
    for key in ('id', 'tool') + (('server',) if mcp else ()):
        if not isinstance(item[key], str): raise ValueError('V3外部工具文本字段无效。')
    for key in (('pluginId', 'mcpAppResourceUri') if mcp else ('namespace',)):
        if key in item and item[key] is not None and not isinstance(item[key], str):
            raise ValueError('V3外部工具可空文本无效。')
    # The legacy optional URI, when present, is a string rather than nullable.
    if 'mcpAppResourceUri' in item and not isinstance(item['mcpAppResourceUri'], str):
        raise ValueError('V3 MCP旧资源URI须是字符串。')
    if item['status'] not in (('inProgress',) if starting else ('completed', 'failed')):
        raise ValueError('V3外部工具生命周期无效。')
    duration = item['durationMs']
    if duration is not None and (type(duration) is not int or duration < 0):
        raise ValueError('V3外部工具耗时须是非负整数或null。')
    if starting and (duration is not None or any(item[k] is not None for k in
            (('result', 'error') if mcp else ('contentItems', 'success')))):
        raise ValueError('V3外部工具开始不能包含结束结果。')
    json_observation(item['arguments'])
    if mcp:
        if item['readOnlyHint'] is not None and type(item['readOnlyHint']) is not bool:
            raise ValueError('V3 MCP只读提示须是布尔值或null。')
        context = item['appContext']
        if context is not None:
            fields(context, 'connectorId linkId resourceUri appName actionName')
            if not isinstance(context['connectorId'], str) or any(
                    context[k] is not None and not isinstance(context[k], str) for k in
                    ('linkId', 'resourceUri', 'appName', 'actionName')):
                raise ValueError('V3 MCP应用上下文无效。')
        ui = item['mcpAppUi']
        if ui is not None:
            fields(ui, 'resourceUri preferredModelDisplayMode')
            if not isinstance(ui['resourceUri'], str) or ui['preferredModelDisplayMode'] not in ('inline', 'fullscreen'):
                raise ValueError('V3 MCP应用UI观察无效。')
        result = item['result']
        if result is not None:
            fields(result, 'content structuredContent _meta')
            if not isinstance(result['content'], list): raise ValueError('V3 MCP内容须是数组。')
            for value in result.values(): json_observation(value)
        if item['error'] is not None:
            fields(item['error'], 'message')
            if not isinstance(item['error']['message'], str): raise ValueError('V3 MCP错误正文无效。')
    else:
        if item['success'] is not None and type(item['success']) is not bool:
            raise ValueError('V3 dynamic工具结果须是布尔值或null。')
        content = item['contentItems']
        if content is not None:
            if not isinstance(content, list): raise ValueError('V3 dynamic内容须是数组。')
            for part in content:
                if not isinstance(part, dict): raise ValueError('V3 dynamic内容须是完整对象。')
                kind = part.get('type')
                if kind not in ('inputText', 'inputImage', 'inputAudio'):
                    raise ValueError('V3 dynamic内容类型尚未核实。')
                key = {'inputText': 'text', 'inputImage': 'imageUrl', 'inputAudio': 'audioUrl'}[kind]
                fields(part, 'type ' + key)
                if not isinstance(part[key], str): raise ValueError('V3 dynamic内容字段须是字符串。')


def original_input(task, attempt, prompt):
    """Rebuild official_work.prepare's private input from its frozen sources.

    Do not consult today's files or accept a caller-provided send body. Scenes,
    extraction and rendered images remain outside this first-turn branch.
    """
    spec = task['spec']
    if (attempt.get('baseline') or {}).get('runtime_mode') != 'official':
        raise ValueError('V3原输入缺少官方首次执行基线。')
    attachments = spec.get('attachments', [])
    if any(i['route'] != 'native' for i in attachments):
        raise ValueError('V3原输入尚未核实转换附件。')
    attached = {i['file'] for i in attachments}
    root = Path(attempt['workspace'])
    prepared = [{**i, 'path': str(root / i['file'])} for i in attachments]
    reconstructed = {'workspace': attempt['workspace'], 'baseline': attempt['baseline'],
        '_attachments': prepared,
        '_selected_files': [str(root / name) for name in spec['materials'] if name not in attached]}
    from .attachments import codex_input
    return codex_input(reconstructed, prompt)


def user_message(item, expected):
    fields(item, 'id type clientId content')
    # This turn/start has no clientId, text spans, image detail override, audio,
    # remote image, mention or skill input. A well-shaped extra is still not
    # evidence that the original native call actually sent it.
    if item['clientId'] is not None or not isinstance(item['content'], list):
        raise ValueError('V3用户消息不是原首次发送输入。')
    content = []
    for part in item['content']:
        if not isinstance(part, dict): raise ValueError('V3用户输入须是完整对象。')
        if part.get('type') == 'text':
            fields(part, 'type text text_elements')
            if not isinstance(part['text'], str) or part['text_elements'] != []:
                raise ValueError('V3原发送未包含文本元素。')
            content.append({'type': 'text', 'text': part['text']})
        elif part.get('type') == 'localImage':
            fields(part, 'type path')
            if not isinstance(part['path'], str): raise ValueError('V3原图片路径无效。')
            content.append(part)
        else:
            raise ValueError('V3尚未核实此原始用户输入类型。')
    if content != expected:
        raise ValueError('V3用户消息与原实际准备发送输入不符。')


def sandbox_observation(value, access):
    if not isinstance(value, dict): raise ValueError('V3 sandbox观察须是对象。')
    kind = value.get('type')
    expected = {'full': 'dangerFullAccess', 'workspace': 'workspaceWrite'}.get(access)
    if access not in ('inherit', 'full', 'workspace') or expected and kind != expected:
        raise ValueError('V3 sandbox观察与原访问选择不符。')
    if kind == 'dangerFullAccess':
        fields(value, 'type')
    elif kind in ('workspaceWrite', 'readOnly'):
        # Retain the previously checked historical minimal carrier as well as
        # the complete generated schema. Partial enrichment remains closed.
        if set(value) == {'type'}: return
        fields(value, 'type networkAccess' +
            (' writableRoots excludeTmpdirEnvVar excludeSlashTmp' if kind == 'workspaceWrite' else ''))
        if type(value['networkAccess']) is not bool: raise ValueError('V3网络观察须是布尔值。')
        if kind == 'workspaceWrite':
            if any(type(value[k]) is not bool for k in ('excludeTmpdirEnvVar', 'excludeSlashTmp')):
                raise ValueError('V3临时目录观察须是布尔值。')
            roots = value['writableRoots']
            if not isinstance(roots, list) or any(not isinstance(p, str) or not p or '\x00' in p or
                    not (PureWindowsPath(p).is_absolute() or PurePosixPath(p).is_absolute()) or
                    '..' in PureWindowsPath(p).parts or '..' in PurePosixPath(p).parts for p in roots):
                raise ValueError('V3可写根须是完整绝对路径观察。')
    else:
        raise ValueError('V3尚未核实此sandbox路径。')


def turn_observation(value, status, turn, items, initial=None):
    minimal = {'id', 'status'}
    if isinstance(value, dict) and set(value) == minimal:
        fields(value, 'id status')
    else:
        fields(value, 'id status rootTurnId items itemsView error startedAt completedAt durationMs')
        if value['rootTurnId'] not in (None, turn) or value['error'] is not None:
            raise ValueError('V3首次成功Turn的root或error不符。')
        view = value['itemsView']
        # Summary bodies need their own projection contract. Never equate a
        # summary with a complete lifecycle or silently accept arbitrary items.
        if view == 'notLoaded':
            if value['items'] != []: raise ValueError('V3未加载Turn不能携带items。')
        elif view == 'full':
            if json.dumps(value['items'], sort_keys=True, allow_nan=False) != json.dumps(items, sort_keys=True, allow_nan=False):
                raise ValueError('V3完整Turn与原item生命周期不符。')
        else:
            raise ValueError('V3尚未核实Turn摘要投影。')
        for name in ('startedAt', 'completedAt', 'durationMs'):
            v = value[name]
            if v is not None and (type(v) is not int or v < 0):
                raise ValueError('V3 Turn时间须是非负整数或null。')
        if status == 'inProgress' and (value['completedAt'] is not None or value['durationMs'] is not None):
            raise ValueError('V3未完成Turn不能携带完成时间。')
        if value['startedAt'] is not None and value['completedAt'] is not None and value['completedAt'] < value['startedAt']:
            raise ValueError('V3 Turn完成早于开始。')
        if initial and set(initial) != minimal:
            if value['rootTurnId'] != initial['rootTurnId'] or value['startedAt'] != initial['startedAt']:
                raise ValueError('V3 Turn原始根或开始时间在生命周期中变化。')
        # Seconds and durationMs are separate observations; rounded epoch
        # seconds cannot establish an exact millisecond-duration identity.
    if value['id'] != turn or value['status'] != status:
        raise ValueError('V3原Turn身份或状态不符。')
