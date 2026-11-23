"""New text-rework values; shapes and hashes never grant source admission.

These codecs are deliberately separate from V3 ACTIONS and work_proposals.parse.
Only the new actual command/history owners may admit these values.
"""
from copy import deepcopy
from functools import wraps
import json

from .contracts import bounded_text
from .store import canonical, digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    fields, reference, sha, text, task_revision, observation, PERMISSIONS)
from .work_coordinator_handoff_adjustment_records import (
    hashed, finite, permissions, complete_frontier, usage, units)
from .work_coordinator_handoff_historical_assets import _uuid
from .work_coordinator_handoff_roots import CONFIG_FIELDS


PREFIX = 'COORDINATOR_FILE_HANDOFF_TEXT_REWORK_'
CAPABILITY = PREFIX + 'CAPABILITY_V1'
OBSERVATION = PREFIX + 'REVIEW_OBSERVATION_V1'
REVIEW_INPUT = PREFIX + 'REVIEW_INPUT_V1'
PROPOSAL = PREFIX + 'PROPOSAL_V1'
PREVIEW = PREFIX + 'PREVIEW_V1'
CANDIDATE = PREFIX + 'CANDIDATE_V1'
APPLICATION = PREFIX + 'APPLICATION_V1'
REGISTRATION = PREFIX + 'REGISTRATION_V1'
RECEIPT = PREFIX + 'RECEIPT_V1'
LINEAGE = PREFIX + 'LINEAGE_V1'
PATH = PREFIX + 'PATH_V1'
CURRENT = PREFIX + 'CURRENT_V1'
COORDINATION_INPUT = PREFIX + 'COORDINATION_INPUT_V1'
UNIT_INPUT = PREFIX + 'UNIT_INPUT_V1'
LINEAGE_FIELD = 'coordinator_file_handoff_text_rework_lineage'
SCOPE = 'B_COMPLETED_ORDINARY_TEXT; ORIGINAL_DEFINITIONS_RESOURCES_AUTHORIZATION; EXPLICIT_SERIAL_ROUNDS'
SEND = 'work-item.coordinator-text-rework-send'
APPLY = 'work-item.rework-apply'
DRIVER_CHANGE = 'work-item.coordinator-text-rework-driver-change'
ACTIONS = ['rework_units', 'pause']
POST_ACTIONS = ['dispatch', 'pause']
SEND_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization',
    'frontier', 'unit', 'retry_of', 'proposal', 'capability'}
APPLY_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'proposal'}
PAUSE_REASON = 'COORDINATOR_TEXT_REWORK_EXPLICIT_RECHECK_REQUIRED'
KINDS = {protocol: 'work_coordinator_handoff_text_rework_' + suffix + '_v1'
    for protocol, suffix in ((CAPABILITY, 'capability'), (PROPOSAL, 'proposal'),
        (CANDIDATE, 'candidate'), (APPLICATION, 'application'),
        (REGISTRATION, 'registration'), (RECEIPT, 'receipt'))}
EVENTS = {protocol: 'coordinator_text_rework_' + suffix + '_v1'
    for protocol, suffix in ((CAPABILITY, 'opened'), (PROPOSAL, 'proposal_checked'),
        (CANDIDATE, 'candidate_prepared'), (APPLICATION, 'applied'),
        (REGISTRATION, 'registered'), (RECEIPT, 'receipt_saved'))}
REWORK_SCHEMA = {'schema_version': 2, 'action': 'rework_units',
    'changes': [{'unit_key': 'declared ordinary key', 'source_run_id': 'exact run',
        'source_version': 'exact raw evidence sha256'}],
    'next_coordinate': {'key': 'declared coordinate key', 'revision': 'exact definition sha256'},
    'reason': 'rework reason'}
PAUSE_SCHEMA = {'schema_version': 2, 'action': 'pause', 'reason': 'pause reason'}
OBS_FIELDS = {'protocol', 'version', 'task', 'parent_plan_snapshot', 'driver',
    'authorization', 'saved_path', 'phase', 'frontier', 'current_frames', 'delivery',
    'completion', 'material_observation', 'configuration', 'work_requirements',
    'review_coordinate', 'coordinate_definitions', 'ordinary_sources', 'protected_keys',
    'usage', 'readiness_observation', 'observed_at'}
CAP_FIELDS = {'protocol', 'version', 'scope', 'root', 'parent_type', 'parent_plan',
    'parent_tail', 'request', 'request_hash', 'observation', 'allowed_actions',
    'reply_schema', *PERMISSIONS}
INPUT_FIELDS = {'protocol', 'version', 'capability', 'root', 'parent_plan', 'task',
    'driver', 'authorization', 'phase', 'prior_frontier', 'material_observation',
    'selected_unit', 'configuration', 'work_requirements', 'units', 'ordinary_sources',
    'coordinate_definitions', 'protected_keys', 'allowed_actions', 'rework_schema',
    'pause_schema', *PERMISSIONS}
PREVIEW_FIELDS = {'protocol', 'version', 'root', 'parent_plan', 'capability',
    'source_run', 'source_exit_event', 'decision', 'direct_changes', 'affected_keys',
    'protected_keys', 'candidate_units', 'next_coordinate', 'historical_coordinators',
    'carry_sources', 'source_frontier', 'material_observation', 'authorization',
    'usage', 'entries', 'blockers', 'effect', *PERMISSIONS}
RECEIPT_FIELDS = {'protocol', 'version', 'operation', 'request', 'request_hash',
    'capability', 'registration', 'run', 'driver', 'receipt', *PERMISSIONS}
PARENT_TYPES = {'ROOT_V1', 'ROOT_V2', 'ROOT_ADJUSTMENT', 'ADJUSTMENT',
    'FIRST_ROUND', 'MIXED_ROUND', 'POST_ROUND', 'POST_V1', 'POST_V2', 'POST_V3'}


def closed(function):
    """Normalize malformed new values, preserving keyword-only owner binding.

    The original codec wrapper accepts positional arguments only. New typed
    parent/suffix calls use explicit keyword-only ownership; keep this local.
    """
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (KeyError, TypeError, AttributeError, LookupError, OverflowError, RecursionError) as exc:
            raise ValueError('文字返工完整字段或嵌套格式不符。') from exc
    return checked



def memory_digest(value):
    """Fingerprint complete in-memory facts, including typed tuple indexes.

    This is only an owner mutation guard. Wire records retain their original
    canonical JSON codec and are independently rebuilt from actual SQL.
    """
    from hashlib import sha256
    state = sha256()
    def key_order(key):
        if isinstance(key, tuple):
            return ['tuple', [key_order(part) for part in key]]
        if type(key) in (str, int, float, bool, type(None)):
            return [type(key).__name__, key]
        raise ValueError('文字返工内存索引键格式不符。')
    def visit(node):
        if type(node) is dict:
            state.update(b'dict[')
            for key in sorted(node, key=lambda k: canonical(key_order(k))):
                visit(key); visit(node[key])
            state.update(b']')
        elif type(node) in (list, tuple):
            state.update(type(node).__name__.encode() + b'[')
            for part in node:
                visit(part)
            state.update(b']')
        elif type(node) in (str, int, float, bool, type(None)):
            raw = canonical(node).encode('utf-8')
            state.update(type(node).__name__.encode() + b':' + str(len(raw)).encode() + b':' + raw)
        else:
            raise ValueError('文字返工内存事实包含未知类型。')
    visit(value)
    return state.hexdigest()


def bounded(value):
    if len(canonical(value)) > 160000:
        raise ValueError('文字返工完整输入超过160000字，未截断。')


@closed
def coordinate_ref(value):
    fields(value, {'key', 'revision'})
    text(value['key']); sha(value['revision'])
    if len(value['key']) > 64:
        raise ValueError('文字返工协调key超过64字。')
    return deepcopy(value)


@closed
def event_ref(value):
    fields(value, {'seq', 'kind', 'payload_digest'})
    if type(value['seq']) is not int or value['seq'] < 1:
        raise ValueError('文字返工事件须为准确已发生序号。')
    text(value['kind']); sha(value['payload_digest'])
    return deepcopy(value)


@closed
def parse_reply(answer):
    if not isinstance(answer, str) or len(answer) > 32000:
        raise ValueError('文字返工提案JSON最多32000字。')

    def pairs(items):
        value = {}
        for key, part in items:
            if key in value:
                raise ValueError('文字返工提案包含重复字段。')
            value[key] = part
        return value

    def constant(_):
        raise ValueError('文字返工提案不接受非有限值。')

    try:
        value = json.loads(answer, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise ValueError('文字返工提案须为单一严格JSON对象。') from None
    if not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 2:
        raise ValueError('文字返工提案须使用新冻结schema_version整数2。')
    names = {'schema_version', 'action', 'reason'}
    if value.get('action') == 'rework_units':
        names |= {'changes', 'next_coordinate'}
    elif value.get('action') != 'pause':
        raise ValueError('返工提案回合只允许rework_units或pause。')
    fields(value, names)
    value['reason'] = bounded_text(value['reason'], '返工提案依据', 2000)
    if value['action'] == 'rework_units':
        from .work_reworks import changes
        changes(value['changes'])
        for change in value['changes']:
            sha(change['source_version'])
        coordinate_ref(value['next_coordinate'])
    return deepcopy(value)


@closed
def review_request(value):
    fields(value, SEND_FIELDS)
    text(value['id']); task_revision(value['revision']); reference(value['plan'])
    fields(value['current_driver'], {'id', 'generation', 'revision'})
    text(value['current_driver']['id'])
    _uuid(value['current_driver']['generation']); _uuid(value['current_driver']['revision'])
    fields(value['authorization'], {'id', 'revision'})
    text(value['authorization']['id']); _uuid(value['authorization']['revision'])
    sha(value['frontier']); coordinate_ref(value['unit'])
    if any(value[name] is not None for name in ('retry_of', 'proposal', 'capability')):
        raise ValueError('首次文字返工提案回合不能重试、消费旧提案或引用旧能力。')
    return deepcopy(value)


def request_for(obs):
    return {'id': obs['task']['id'], 'revision': obs['task']['revision'],
        'current_driver': {k: obs['driver'][k] for k in ('id', 'generation', 'revision')},
        'plan': plan_ref(obs['parent_plan_snapshot']),
        'authorization': {k: obs['authorization'][k] for k in ('id', 'revision')},
        'frontier': digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']}
            for f in obs['frontier']]), 'unit': deepcopy(obs['review_coordinate']),
        'retry_of': None, 'proposal': None, 'capability': None}


@closed
def validate_observation(value):
    fields(value, OBS_FIELDS); hashed(value)
    # The observation is a full historical witness, not the provider input.
    # review_input/review_prompt enforce the complete model input limit.
    if value['protocol'] != OBSERVATION or value['phase'] != 'B_COMPLETED':
        raise ValueError('文字返工须为准确B_COMPLETED父观察。')
    fields(value['task'], {'id', 'revision'})
    task_revision(value['task']['revision']); finite(value['observed_at'])
    plan = value['parent_plan_snapshot']; hashed(plan); units(plan['units'])
    if plan.get(LINEAGE_FIELD):
        raise ValueError('首批文字返工不开放第二条返工边。')
    driver, auth = value['driver'], value['authorization']
    coordinate_ref(value['review_coordinate'])
    selected = next(u for u in plan['units'] if u['key'] == value['review_coordinate']['key'])
    readiness = value['readiness_observation']
    fields(readiness, {'id', 'version', 'event'}); reference({k: readiness[k] for k in ('id', 'version')})
    event_ref(readiness['event'])
    if (value['task'] != {'id': plan['item_id'], 'revision': driver['item_revision']} or
            driver['state'] != 'RESERVED' or driver['generation'] != auth['driver_generation'] or
            any(driver[k] != auth[k] for k in ('item_id', 'plan_id', 'plan_version')) or
            driver['plan_id'] != plan['id'] or driver['plan_version'] != plan['version'] or
            selected['role'] != 'coordinate' or selected['member'] != plan['coordinator'] or
            value['review_coordinate']['revision'] != digest(selected) or
            value['configuration'] != {k: plan[k] for k in CONFIG_FIELDS} or
            value['work_requirements'] != plan['work_requirements'] or
            value['coordinate_definitions'] != [u for u in plan['units'] if u['role'] == 'coordinate'] or
            not driver['updated'] <= value['observed_at'] < auth['dispatch_before']):
        raise ValueError('返工父观察须保持原定义、资源、责任、授权和最初截止。')
    complete_frontier(value['frontier']); observation(value['material_observation']); usage(value['usage'])
    if (value['usage']['unknown_requests'] or value['usage']['attempts'] != len(value['frontier']) or
            len(value['frontier']) >= auth['max_attempts_total'] or
            not readiness['id'].startswith('work-readiness:')):
        raise ValueError('返工父累计或准确资源观察引用无效。')
    _uuid(readiness['id'].removeprefix('work-readiness:'))
    protected = value['protected_keys']
    if not isinstance(protected, list) or protected != sorted(set(protected)) or not set(protected) <= {u['key'] for u in plan['units']}:
        raise ValueError('返工保护集合须为准确排序原key。')
    if not isinstance(value['ordinary_sources'], list) or not isinstance(value['current_frames'], list):
        raise ValueError('返工须保留完整准确来源列表。')
    source_keys = set()
    definitions = {u['key']: u for u in plan['units']}
    for source in value['ordinary_sources']:
        fields(source, {'key', 'definition_revision', 'run', 'original_plan',
            'original_definition', 'original_input', 'evidence', 'files'})
        reference(source['original_plan']); sha(source['definition_revision'])
        unit = definitions.get(source['key']); run = source['run']; evidence = source['evidence']
        hashed(evidence)
        if (source['key'] in source_keys or unit is None or unit['role'] == 'coordinate' or
                unit['outputs'] or unit['checks'] or unit['write_paths'] or unit['dependency_gate'] != 'result' or
                source['definition_revision'] != digest(unit) or source['original_definition'] != unit or
                source['original_input'] != run['input'] or run['input']['unit'] != unit or
                run['unit_key'] != unit['key'] or run['unit_revision'] != digest(unit) or
                source['original_plan'] != {'id': run['plan_id'], 'version': run['plan_version']} or
                evidence['run_id'] != run['id']):
            raise ValueError('返工普通来源须保留唯一原定义、完整冻结输入及原始退出证据。')
        source_keys.add(source['key'])
    if not isinstance(value['delivery'], list) or not isinstance(value['completion'], list) or not value['delivery'] or not value['completion']:
        raise ValueError('返工缺少原完整A交付/B完成记录。')
    return deepcopy(value)


@closed
def validate_capability(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    finite(row['created']); _uuid(row['command_id'])
    body = row['snapshot']; fields(body, CAP_FIELDS)
    hashed(body); permissions(body)
    obs = validate_observation(body['observation']); request = review_request(body['request'])
    reference(body['root']); reference(body['parent_plan'])
    if (body['protocol'] != CAPABILITY or body['scope'] != SCOPE or
            row['id'] != KINDS[CAPABILITY] + ':' + body['version'] or row['version'] != body['version'] or
            row['item_id'] != obs['task']['id'] or body['parent_type'] not in PARENT_TYPES or
            body['parent_plan'] != plan_ref(obs['parent_plan_snapshot']) or request != request_for(obs) or
            body['request_hash'] != digest(request) or body['allowed_actions'] != ACTIONS or
            body['reply_schema'] != {'rework': REWORK_SCHEMA, 'pause': PAUSE_SCHEMA} or
            type(body['parent_tail']) is not int or body['parent_tail'] < 0 or
            obs['readiness_observation']['event']['seq'] != body['parent_tail'] + 1 or
            not obs['observed_at'] <= row['created'] < obs['authorization']['dispatch_before'] or
            obs['readiness_observation']['id'] != 'work-readiness:' + row['command_id']):
        raise ValueError('返工能力须绑定新准确命令、原父边界和完整观察。')
    return deepcopy(row)


@closed
def validate_review_context(value, capability):
    """Exact generated input equality; the caller still proves the capability."""
    fields(value, INPUT_FIELDS); hashed(value); permissions(value); bounded(value)
    from .work_coordinator_handoff_text_rework_values import review_context
    if value != review_context(capability):
        raise ValueError('返工审阅context不等于该准确能力的完整原输入。')
    return deepcopy(value)


@closed
def validate_preview(value):
    fields(value, PREVIEW_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PREVIEW or value['effect'] != 'PREVIEW_ONLY; NO_PLAN_CHANGE_OR_DISPATCH':
        raise ValueError('文字返工预览协议或用途不符。')
    for key in ('root', 'parent_plan', 'capability', 'source_run'):
        reference(value[key])
    if value['capability']['id'] != KINDS[CAPABILITY] + ':' + value['capability']['version']:
        raise ValueError('文字返工预览须引用准确typed审阅能力。')
    event_ref(value['source_exit_event']); coordinate_ref(value['next_coordinate'])
    decision = parse_reply(canonical(value['decision']))
    if decision['action'] != 'rework_units' or decision['next_coordinate'] != value['next_coordinate']:
        raise ValueError('返工预览须保持原schema2决定及准确后继协调。')
    units(value['candidate_units']); complete_frontier(value['source_frontier'])
    observation(value['material_observation']); usage(value['usage'])
    keys = [u['key'] for u in value['candidate_units']]
    for name in ('direct_changes', 'affected_keys', 'protected_keys', 'historical_coordinators'):
        items = value[name]
        if (not isinstance(items, list) or len(items) != len(set(items)) or
                any(not isinstance(key, str) or key not in keys for key in items)):
            raise ValueError('返工预览集合须为唯一准确原key。')
    if (value['direct_changes'] != sorted(change['unit_key'] for change in decision['changes']) or
            not set(value['direct_changes']) <= set(value['affected_keys']) or
            value['protected_keys'] != sorted(value['protected_keys']) or
            value['affected_keys'] != [key for key in keys if key in value['affected_keys']] or
            value['historical_coordinators'] != [u['key'] for u in value['candidate_units']
                if u['role'] == 'coordinate' and u['key'] != value['next_coordinate']['key']]):
        raise ValueError('返工预览集合顺序、影响并集或历史协调不符。')
    next_unit = next((u for u in value['candidate_units'] if u['key'] == value['next_coordinate']['key']), None)
    # Invalid next references are legitimate blocked previews; they must not be
    # silently turned into an applicable candidate by a shape validator.
    if not value['blockers'] and (next_unit is None or next_unit['role'] != 'coordinate' or
            digest(next_unit) != value['next_coordinate']['revision']):
        raise ValueError('可应用返工预览须指定原准确协调单元。')
    if (not isinstance(value['entries'], list) or len(value['entries']) != len(keys) or
            not isinstance(value['blockers'], list) or any(not isinstance(v, str) or not v for v in value['blockers']) or
            not isinstance(value['carry_sources'], dict) or not set(value['carry_sources']) <= set(keys)):
        raise ValueError('返工预览逐项诊断、原来源或阻塞格式不符。')
    for unit, entry in zip(value['candidate_units'], value['entries']):
        fields(entry, {'key', 'definition_revision', 'state', 'reasons', 'source',
            'original_input', 'original_evidence', 'files'})
        if (entry['key'] != unit['key'] or entry['definition_revision'] != digest(unit) or
                entry['state'] not in {'HISTORICAL_COORDINATOR', 'AFFECTED', 'HISTORICAL_RETIRED', 'ELIGIBLE_RESULT', 'NOT_RUN'} or
                not isinstance(entry['reasons'], list) or any(not isinstance(v, str) for v in entry['reasons'])):
            raise ValueError('返工预览逐项须保持原定义及准确诊断。')
    return deepcopy(value)


@closed
def validate_receipt(row):
    """Closed full-response witness; semantic command replay is separate."""
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    finite(row['created']); _uuid(row['command_id'])
    body = row['snapshot']; fields(body, RECEIPT_FIELDS); hashed(body); permissions(body)
    if (body['protocol'] != RECEIPT or row['id'] != KINDS[RECEIPT] + ':' + body['version'] or
            row['version'] != body['version'] or not isinstance(body['receipt'], dict) or
            not isinstance(body['request'], dict) or body['request_hash'] != digest(body['request']) or
            body['operation'] not in {'REVIEW_SEND', 'SAVE', 'DRIVER_CHANGE', 'SEND', 'PROPOSAL_CHECK'}):
        raise ValueError('文字返工完整回执类型、摘要或operation不符。')
    reference(body['capability'])
    if body['capability']['id'] != KINDS[CAPABILITY] + ':' + body['capability']['version']:
        raise ValueError('文字返工回执能力类型不符。')
    from .work_coordinator_handoff_adjustment_records import driver_ref
    driver_ref(body['driver'])
    if body['operation'] == 'REVIEW_SEND':
        review_request(body['request'])
        if body['registration'] is not None or body['run'] is None:
            raise ValueError('返工审阅回执须有真实run且尚无保存registration。')
    else:
        reference(body['registration'])
        if body['registration']['id'] != KINDS[REGISTRATION] + ':' + body['registration']['version']:
            raise ValueError('返工后继回执须引用准确新registration。')
        if (body['operation'] in {'SAVE', 'DRIVER_CHANGE'}) != (body['run'] is None):
            raise ValueError('返工回执run位置不符合准确operation。')
    if body['run'] is not None:
        reference(body['run'])
    if body['request'].get('id') != row['item_id']:
        raise ValueError('返工完整回执业务归属不符。')
    return deepcopy(row)
