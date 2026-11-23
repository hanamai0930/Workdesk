"""Frozen official routes and per-attempt observations for fixed Work."""
from importlib.metadata import distributions
import json


PACKAGE_METADATA = {
    'qwen': ('qwen/node_modules/@qwen-code/sdk/package.json', '@qwen-code/sdk'),
    'deepseek': ('deepseek-cli/node_modules/@deepseek-ai/dsh/package.json', '@deepseek-ai/dsh'),
}
SDK_LIFECYCLE = {
    'qwen': {'MAIN_RESULT': 'Query result', 'STREAM_ENDED': 'Query iterator', 'CLOSE_RETURNED': 'Query.close'},
    'deepseek': {'INIT_ACKNOWLEDGED': 'Harness.start', 'MAIN_RESULT': 'Session.run',
                 'WAIT_REQUESTED': 'session/wait', 'WAIT_ACKNOWLEDGED': 'session/wait'},
}


def local_package(locations, relative, package):
    try:
        metadata = json.loads((locations.runtime / relative).read_text(encoding='utf-8'))
        if isinstance(metadata, dict) and metadata.get('name') == package and isinstance(metadata.get('version'), str):
            return metadata['version']
    except (OSError, ValueError):
        pass
    return None


def runtime_route(service, provider, interface=None):
    runtime = service.independent.describe(provider, interface)
    route = {key: runtime[key] for key in ('provider', 'harness', 'execution', 'origin', 'agent_loop')}
    route['interface'] = {'codex': 'app-server', 'qwen': 'Query', 'deepseek': 'session/prompt'}[provider]
    if interface=='daemon':route['interface']='Daemon'
    route['local_packages'] = {}
    if provider == 'codex':
        try:
            binary = service.locations.codex
        except OSError:
            binary = None
        route['local_binary_id'] = binary.parent.name if binary else None
        route['version_evidence'] = 'BINARY_LOCATION_ONLY' if binary else 'UNAVAILABLE'
        return route
    relative, package = PACKAGE_METADATA[provider]
    version = local_package(service.locations, relative, package)
    if version:
        route['local_packages'][package] = version
    if provider == 'deepseek':
        route['profile'] = 'sdk'
        app = '@deepseek-ai/dsh-sdk-app'
        version = local_package(service.locations, 'deepseek-cli/node_modules/' + app + '/package.json', app)
        if version:
            route['local_packages'][app] = version
        site = service.locations.runtime / 'deepseek-venv/Lib/site-packages'
        versions = {dist.version for dist in distributions(name='deepseek-harness-sdk', path=[str(site)]) if dist.version}
        if len(versions) == 1:
            route['local_packages']['deepseek-harness-sdk'] = versions.pop()
    route['version_evidence'] = 'LOCAL_PACKAGE_METADATA' if route['local_packages'] else 'UNAVAILABLE'
    return route


def record_sdk_lifecycle(store, task, attempt, data):
    provider = task['spec']['provider']
    phase = data.get('phase')
    if (not isinstance(phase, str) or phase not in SDK_LIFECYCLE.get(provider, {}) or
            data.get('session_id') != attempt['session_id']):
        return
    value = {'phase': phase, 'session_id': attempt['session_id']}
    if provider=='qwen' and task['spec'].get('qwen_runtime')=='daemon':value['interface']='Daemon'
    if phase == 'MAIN_RESULT' and isinstance(data.get('outcome'), str):
        value['outcome'] = data['outcome']
    store.event(task['id'], attempt['id'], 'native_sdk_lifecycle', value)


def sdk_lifecycle(db, task, attempt):
    provider = task['spec']['provider']
    if provider not in SDK_LIFECYCLE:
        return []
    session = attempt['session_id']
    daemon=provider=='qwen' and task['spec'].get('qwen_runtime')=='daemon'
    daemon_sources={'MAIN_RESULT':'Daemon exact prompt terminal','STREAM_ENDED':'Daemon event stream',
                    'CLOSE_RETURNED':'Daemon session close and process exit'}
    receipts = []
    rows = db.execute("""
        SELECT seq,kind,payload FROM events WHERE task_id=? AND attempt_id=?
          AND kind IN ('native_sdk_lifecycle','native_sdk_configuration','model_selection_receipt',
                       'native_resume_receipt','native_effort_receipt','interrupt_receipt','shutdown_receipt')
        ORDER BY seq
    """, (task['id'], attempt['id']))
    for row in rows:
        kind = row['kind']
        data = json.loads(row['payload'])

        def add(phase, source, **details):
            receipts.append({'phase': phase, 'source': source, 'event_seq': row['seq'], **details})

        if kind == 'native_sdk_lifecycle' and data.get('session_id') == session:
            phase = data.get('phase')
            if isinstance(phase, str) and phase in SDK_LIFECYCLE[provider]:
                add(phase, daemon_sources.get(phase, SDK_LIFECYCLE[provider][phase]) if daemon else SDK_LIFECYCLE[provider][phase], outcome=data.get('outcome'))
        elif provider == 'deepseek' and kind == 'native_resume_receipt':
            if data.get('session_id') == session and data.get('method') == 'session/resume' and data.get('restored') is True:
                add('RESUME_ACKNOWLEDGED', 'session/resume')
        elif provider == 'qwen' and kind == 'native_sdk_configuration':
            if data.get('session_id') == session and data.get('model') == task['spec']['model']:
                add('INIT_ACKNOWLEDGED', 'Daemon exact session and applied model' if daemon else 'Query system.init')
        elif provider == 'qwen' and kind == 'model_selection_receipt':
            if (data.get('session_id_observed_before_prompt') == session and
                    data.get('model') == task['spec']['model'] and data.get('interface_acknowledged') is True):
                add('MODEL_ACKNOWLEDGED', 'Daemon applied startup model' if daemon else 'Query.setModel')
                if data.get('resume_requested') is True:
                    add('RESUME_TARGET_OBSERVED', 'Daemon resumeSession' if daemon else 'Query.getSessionId')
        elif provider == 'qwen' and kind == 'native_effort_receipt':
            status = data.get('status')
            if (data.get('session_id') == session and data.get('requested') == task['spec']['effort'] and
                    isinstance(status, dict) and type(status.get('applied')) is bool):
                add('EFFORT_REPORTED', 'Daemon reasoning configuration' if daemon else 'Query effort', requested=data['requested'], applied=status['applied'])
        elif provider == 'qwen' and kind == 'interrupt_receipt' and (data.get('receipt') or {}).get('received') is True:
            add('STOP_ACKNOWLEDGED', 'Daemon prompt cancel' if daemon else 'Query.interrupt')
        elif provider == 'deepseek' and kind == 'shutdown_receipt' and isinstance(data.get('receipt'), dict):
            add('STOP_ACKNOWLEDGED', 'shutdown')
    return receipts


def mcp_tool_server(name):
    if isinstance(name, str) and name.startswith('mcp__'):
        server, _, tool = name.removeprefix('mcp__').partition('__')
        if tool:
            return server
    return None


def tool_outcome(provider, row):
    if row['state'] != 'COMPLETED':
        return {}
    if provider == 'codex':
        if (row['result_type'] != 'mcpToolCall' or row['result_id'] != row['native_call_id']
                or not row['result_id'] or row['result_server'] != row['server'] or row['result_tool'] != row['codex_tool']):
            return {}
        states = {'completed': 'REPORTED_COMPLETED', 'failed': 'REPORTED_ERROR'}
        result = {'outcome': states[row['result_status']]} if row['result_status'] in states else {}
        if isinstance(row['plugin_id'], str) and row['plugin_id']:
            result['plugin_id'] = row['plugin_id']
        return result
    if not row['native_call_id']:
        return {}
    if provider == 'qwen':
        if row['result_type'] != 'tool_result' or row['qwen_result_id'] != row['native_call_id']:
            return {}
        error = row['qwen_error']
    else:
        if row['deepseek_result_id'] != row['native_call_id']:
            return {}
        error = row['deepseek_error']
    if error in ('true', 'false'):
        return {'outcome': 'REPORTED_ERROR' if error == 'true' else 'REPORTED_COMPLETED'}
    return {}


def tool_activity(db, task, attempt, snapshot):
    provider = task['spec']['provider']
    mcp = {entry['name']: {'kind': 'mcp', 'label': entry['label']} for entry in snapshot['mcp']}
    skills = {skill['name']: {'kind': 'skill_by_name', 'label': skill['name']}
              for bundle in snapshot['bundles'] for skill in bundle['skills']} if provider in {'deepseek', 'qwen'} else {}
    if not (mcp or skills):
        return []
    seen = {}
    for row in db.execute("""
        SELECT e.seq, t.id, t.state, json_extract(e.payload,'$.item.type') AS item_type,
               json_extract(e.payload,'$.item.id') AS native_call_id,
               json_extract(e.payload,'$.item.server') AS server,
               json_extract(e.payload,'$.item.tool') AS codex_tool,
               json_extract(e.payload,'$.item.name') AS qwen_tool,
               json_extract(e.payload,'$.item.input.name') AS qwen_deferred_target,
               json_type(e.payload,'$.item.input.arguments') AS qwen_deferred_arguments_type,
               json_extract(e.payload,'$.item.tool_name') AS deepseek_tool,
               json_extract(e.payload,'$.item.input.skill') AS qwen_skill,
               json_extract(e.payload,'$.item.input.name') AS deepseek_skill,
               json_extract(t.result,'$.type') AS result_type,
               json_extract(t.result,'$.id') AS result_id,
               json_extract(t.result,'$.server') AS result_server,
               json_extract(t.result,'$.tool') AS result_tool,
               json_extract(t.result,'$.status') AS result_status,
               json_extract(t.result,'$.pluginId') AS plugin_id,
               json_extract(t.result,'$.tool_use_id') AS qwen_result_id,
               json_type(t.result,'$.is_error') AS qwen_error,
               json_extract(t.result,'$.message.toolCallId') AS deepseek_result_id,
               json_type(t.result,'$.message.isError') AS deepseek_error
        FROM events e JOIN tool_calls t ON t.id=json_extract(e.payload,'$.call_id')
        WHERE e.task_id=? AND e.attempt_id=? AND e.kind='native_tool_started' AND t.attempt_id=e.attempt_id
        ORDER BY e.seq
    """, (task['id'], attempt['id'])):
        if provider == 'codex':
            source = mcp.get(row['server']) if row['item_type'] == 'mcpToolCall' else None
            name = row['codex_tool']
        else:
            name = row['deepseek_tool'] if provider == 'deepseek' else row['qwen_tool']
            direct_name = name
            if provider == 'qwen' and direct_name == 'tool_call':
                # Query reports the deferred bridge as the invoked tool. The
                # original call id still owns the underlying MCP result.
                if row['item_type'] != 'tool_use' or row['qwen_deferred_arguments_type'] != 'object':
                    continue
                name = row['qwen_deferred_target']
            if not isinstance(name, str):
                continue
            source = mcp.get(mcp_tool_server(name))
            if direct_name == 'skill':
                source = skills.get(row[provider + '_skill'])
        if source and isinstance(name, str) and name:
            seen[row['id']] = {**source, 'tool': name, 'event_seq': row['seq'], 'state': row['state'],
                               **tool_outcome(provider, row)}
    return list(seen.values())


def codex_mcp_receipts(db, task, attempt):
    states = {'starting': 'MCP_STARTING', 'ready': 'SERVER_READY',
              'failed': 'MCP_START_FAILED', 'cancelled': 'MCP_START_CANCELLED'}
    observed = {}
    for row in db.execute("""SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=?
          AND kind='native_mcp_startup' AND json_extract(payload,'$.thread_id')=? ORDER BY seq""",
                          (task['id'], attempt['id'], attempt['session_id'])):
        data = json.loads(row['payload'])
        state = states.get(data.get('status'))
        if state == 'MCP_START_FAILED' and data.get('reauthentication_required') is True:
            state = 'MCP_REAUTHENTICATION_REQUIRED'
        if state and isinstance(data.get('name'), str):
            observed[data['name']] = {'state': state, 'event_seq': row['seq']}
    return observed


def capability_components(snapshot, provider, attempt, details, codex_mcp=None):
    skills = [skill['name'] for bundle in snapshot['bundles'] for skill in bundle['skills']]
    mcp = {entry['name']: entry['label'] for entry in snapshot['mcp']}
    skill_states, mcp_states = {}, {}
    if attempt['session_id'] and details.get('session_id') == attempt['session_id']:
        source = details.get('source')
        if (provider == 'codex' and details.get('method') == 'turn/start' and attempt['turn_id']
                and details.get('turn_id') == attempt['turn_id']):
            if details.get('skills') == 'EXPLICIT_NATIVE_SKILL_INPUT':
                skill_states = dict.fromkeys(skills, 'INPUT_ACCEPTED')
            if details.get('mcp') == 'NATIVE_THREAD_CONFIG':
                mcp_states = dict.fromkeys(mcp, 'CONFIG_SUBMITTED')
        elif provider == 'qwen' and source == 'NATIVE_SYSTEM_INIT':
            missing = details.get('missing') or []
            names = details.get('skills') or []
            reported = 'NAME_IN_COMMAND_CATALOG' if details.get('skill_catalog') == 'SLASH_COMMANDS' else 'NAME_IN_CATALOG'
            skill_states = {name: reported if name in names else 'REPORTED_MISSING'
                            for name in skills if name in names or name in missing}
            connections = {server['name']: server.get('status') for server in details.get('servers') or []
                           if isinstance(server, dict) and isinstance(server.get('name'), str)}
            mcp_states = {name: 'SERVER_CONNECTED' if connections.get(name) == 'connected' else 'NOT_CONNECTED'
                          for name in mcp if name in connections or name in missing}
        elif provider == 'deepseek' and source == 'NATIVE_SKILL_AND_TOOL_REGISTRIES':
            skill_states = {item['name']: 'NAME_IN_CATALOG' if item['present'] else 'REPORTED_MISSING'
                            for item in details.get('skills') or []
                            if isinstance(item, dict) and isinstance(item.get('name'), str)
                            and type(item.get('present')) is bool}
            registered = {mcp_tool_server(name) for name in details.get('tools') or []}
            mcp_states = dict.fromkeys(mcp.keys() & registered, 'TOOLS_IN_REGISTRY')
    return [
        *({'kind': 'skill', 'label': name, 'state': skill_states.get(name, 'NOT_REPORTED')} for name in skills),
        *({'kind': 'mcp', 'label': label, 'state': mcp_states.get(name, 'NOT_REPORTED'),
           **(codex_mcp or {}).get(name, {})} for name, label in mcp.items()),
    ]


def qwen_delegations(db, task, attempt):
    # The Query envelope supplies the parent tool ID independently of its name.
    parents = {}
    for row in db.execute("""
        SELECT e.seq, json_extract(e.payload,'$.item.id') AS parent_id, t.state
        FROM events e JOIN tool_calls t ON t.id=json_extract(e.payload,'$.call_id')
        WHERE e.task_id=? AND e.attempt_id=? AND e.kind='native_tool_started'
          AND t.attempt_id=e.attempt_id AND json_type(e.payload,'$.item.id')='text'
    """, (task['id'], attempt['id'])):
        parents[row['parent_id']] = row
    if not parents:
        return []
    observed = {}
    for row in db.execute("""
        SELECT seq, json_extract(payload,'$.parent_tool_use_id') AS parent_id,
               json_extract(payload,'$.type') AS message_type, json_extract(payload,'$.is_error') AS is_error
        FROM events WHERE task_id=? AND attempt_id=? AND kind='model_event'
          AND json_type(payload,'$.parent_tool_use_id')='text'
          AND json_type(payload,'$.session_id')='text'
        ORDER BY seq
    """, (task['id'], attempt['id'])):
        identity = row['parent_id']
        parent = parents.get(identity)
        if not parent or parent['seq'] >= row['seq']:
            continue
        state = observed.get(identity, {}).get('state', 'OBSERVED')
        if row['message_type'] == 'result':
            state = 'REPORTED_ERROR' if row['is_error'] else 'RESULT_REPORTED'
        elif state in {'REPORTED_ERROR', 'RESULT_REPORTED'}:
            continue
        elif state == 'OBSERVED' and parent['state'] == 'COMPLETED':
            state = 'TOOL_RETURNED'
        observed[identity] = {'id': identity, 'identity_kind': 'PARENT_TOOL_USE', 'state': state, 'event_seq': row['seq']}
    return list(observed.values())


def session_delegations(db, task, attempt):
    provider = task['spec']['provider']
    if provider == 'codex':
        condition = """kind='native_child_session' OR
                       (kind='native_event' AND json_extract(payload,'$.method') IN ('turn/started','turn/completed'))"""
    else:
        condition = """kind='model_event' AND json_extract(payload,'$.method') IN ('subagent.started','subagent.finished')"""
    rows = db.execute(f"""SELECT seq,kind,payload FROM events
                        WHERE task_id=? AND attempt_id=? AND ({condition}) ORDER BY seq""", (task['id'], attempt['id']))
    root = attempt['session_id']
    children = {}
    turns = {}
    for row in rows:
        event = json.loads(row['payload'])
        if provider == 'codex' and row['kind'] == 'native_event':
            params = event['params']
            child = params.get('threadId')
            turn = params.get('turn') or {}
            turn_id = turn.get('id') or params.get('turnId')
            if not isinstance(child, str) or child not in children or not isinstance(turn_id, str) or not turn_id:
                continue
            if event['method'] == 'turn/started':
                turns[child] = turn_id
                children[child].update(state='TURN_OBSERVED', event_seq=row['seq'])
            elif turns.get(child) == turn_id:
                turns.pop(child)
                children[child].update(state='TURN_ENDED', event_seq=row['seq'], native_status=turn.get('status'))
            continue
        if provider == 'codex':
            child, parent = event.get('session_id'), event.get('parent_session_id')
        else:
            payload = event.get('payload') or {}
            child, parent = payload.get('childSessionId'), payload.get('parentSessionId')
            if event['method'] == 'subagent.finished':
                if isinstance(child, str) and child in children and children[child]['parent_id'] == parent:
                    children[child].update(state='FINISH_REPORTED', event_seq=row['seq'])
                continue
        if (isinstance(child, str) and child and isinstance(parent, str) and child not in {root, parent} and
                (parent == root or parent in children) and child not in children):
            children[child] = {'id': child, 'parent_id': parent, 'identity_kind': 'NATIVE_SESSION',
                               'state': 'OBSERVED', 'event_seq': row['seq']}
    return list(children.values())


def command_catalog(db, task, attempt, provider):
    session = attempt['session_id']
    if not session:
        return None
    if provider == 'qwen':
        kind, source, key = 'native_sdk_configuration', 'QWEN_QUERY_INIT', 'slash_commands'
        identity = "json_extract(payload,'$.model')=?"
        identity_value = task['spec']['model']
    elif provider == 'deepseek':
        kind, source, key = 'native_configuration_receipt', 'NATIVE_COMMAND_REGISTRY', 'commands'
        identity = "json_extract(payload,'$.source')=?"
        identity_value = source
    else:
        return None
    row = db.execute(f"""
        SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind=?
          AND json_extract(payload,'$.session_id')=? AND {identity}
          AND json_type(payload,?)='array' ORDER BY seq DESC LIMIT 1
    """, (task['id'], attempt['id'], kind, session, identity_value, '$.' + key)).fetchone()
    if row is None:
        return None
    names = json.loads(row['payload'])[key]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        return None
    return {'source': source, 'names': list(dict.fromkeys(names)), 'event_seq': row['seq']}


def evidence(db, task, attempt):
    route = (task['spec'].get('cooperative') or {}).get('runtime_route')
    if not route or route['provider'] != task['spec']['provider']:
        return None
    snapshot = task['spec'].get('scene_snapshot')
    observed = None
    if route['provider'] == 'qwen':
        row = db.execute("""SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=?
                         AND kind='native_sdk_configuration' ORDER BY seq DESC LIMIT 1""",
                         (task['id'], attempt['id'])).fetchone()
        if row:
            payload = json.loads(row['payload'])
            version = payload.get('qwen_code_version')
            if payload.get('session_id') == attempt['session_id'] and isinstance(version, str) and version:
                observed = {'version': version, 'event_seq': row['seq']}
    elif route['provider'] == 'codex':
        row = db.execute("""SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=?
                         AND kind='native_app_server_initialized' ORDER BY seq DESC LIMIT 1""",
                         (task['id'], attempt['id'])).fetchone()
        if row:
            payload = json.loads(row['payload'])
            if isinstance(payload.get('user_agent'), str) and payload['user_agent']:
                observed = {'user_agent': payload['user_agent'], 'binary_id': payload.get('binary_id'),
                            'event_seq': row['seq']}
    capability = None
    if snapshot:
        row = db.execute("SELECT data FROM collaboration WHERE kind='scene_load' AND id=?",
                         ('scene-load:' + attempt['id'],)).fetchone()
        load = json.loads(row['data']) if row else None
        if load and (load['task_id'] != task['id'] or load['attempt_id'] != attempt['id'] or
                     load['provider'] != task['spec']['provider'] or
                     load['preset']['snapshot_hash'] != snapshot['snapshot_hash']):
            load = None
        details = (load or {}).get('details') or {}
        capability = {
            'preset': {key: snapshot[key] for key in ('name', 'revision', 'snapshot_hash')},
            'skills': [skill['name'] for bundle in snapshot['bundles'] for skill in bundle['skills']],
            'mcp': [entry['label'] for entry in snapshot['mcp']],
            'load_state': load['state'] if load else 'UNRECORDED',
            'load_source': details.get('source') or details.get('method'),
            'missing': details.get('missing') or [],
            'legacy_missing': (details.get('missing') or []) if not details.get('session_id') else [],
            'components': capability_components(
                snapshot, route['provider'], attempt, details,
                codex_mcp_receipts(db, task, attempt) if route['provider'] == 'codex' else None),
            'tool_activity': tool_activity(db, task, attempt, snapshot),
        }
    delegated = (qwen_delegations(db, task, attempt) if route['provider'] == 'qwen'
                 else session_delegations(db, task, attempt))
    return {'route': route, 'native_version': observed, 'capability': capability, 'delegation': delegated,
            'lifecycle': sdk_lifecycle(db, task, attempt),
            'command_catalog': command_catalog(db, task, attempt, route['provider'])}
