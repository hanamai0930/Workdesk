"""Typed V3 saved values; validation alone proves no durable save or namespace."""
from copy import deepcopy
import uuid

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference, PERMISSIONS, versioned, sha, task_revision
from .work_coordinator_handoff_adjustment_records import (
    hashed, permissions, finite, driver_ref, auth_ref, usage)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref, LINEAGE_FIELD
from .work_coordinator_handoff_post_round_v2_saved_records import (
    AUTH_EXTRA, DRIVER_EXTRA, REQUEST_FIELDS,
    successor_driver as driver_shape, transferred_authorization as auth_shape)
from .work_coordinator_handoff_round_records import event_ref
from .work_coordinator_handoff_post_round_v3_candidates import (
    PREFIX, OPERATION, PARENT_KIND, CHECKPOINT, CANDIDATE, LINEAGE,
    KINDS, EVENTS as PREP_EVENTS, validate_row as preparation_row)

APPLICATION = PREFIX + 'APPLICATION_V3'
REGISTRATION = PREFIX + 'REGISTRATION_V3'
SCOPE = 'SAVED_POST_ROUND_ADJUSTMENT_V3; PAUSED_TRANSFER; SUBSEQUENT_RUNTIME_NOT_OPEN'
PAUSE_REASON = 'POST_ROUND_ADJUSTMENT_V3_SAVED_RECHECK_REQUIRED'
SAVE_EVENT = 'coordinator_post_round_adjustment_saved_v3'
DRIVER_EVENT = 'work_driver_coordinator_post_round_adjustment_saved_v3'
KIND_FOR = {**KINDS, APPLICATION: 'work_coordinator_handoff_post_round_application_v3',
    REGISTRATION: 'work_coordinator_handoff_post_round_registration_v3'}
EVENTS = {**PREP_EVENTS, APPLICATION: 'coordinator_post_round_adjustment_applied_v3',
    REGISTRATION: 'coordinator_post_round_adjustment_registered_v3'}


def record_ref(value, protocol):
    reference(value)
    if value['id'] != KIND_FOR[protocol] + ':' + value['version']:
        raise ValueError('V3保存记录须使用独立准确类型引用。')


def request_shape(request):
    fields(request, REQUEST_FIELDS); task_revision(request['revision']); sha(request['frontier'])
    from .work_coordinator_handoff_records import text
    text(request['id']); driver_ref(request['current_driver']); auth_ref(request['authorization'])
    reference(request['plan']); reference(request['proposal'])


@closed
def validate_row(row, protocol):
    if protocol in KINDS: return preparation_row(row, protocol)
    if protocol not in (APPLICATION, REGISTRATION): raise ValueError('未知V3保存类型。')
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    b = row['snapshot']; hashed(b); permissions(b); finite(row['created']); uuid.UUID(row['command_id'])
    common = {'protocol', 'version', 'operation', 'scope', 'parent_kind', 'item_id', 'root',
        'parent_plan', 'proposal', 'checkpoint', 'candidate', *PERMISSIONS}
    extra = ({'child_plan', 'driver_before', 'driver_after', 'authorization_before',
        'authorization_after', 'usage', 'save_event', 'request', 'pause_reason'} if protocol == APPLICATION
        else {'application', 'from_plan', 'to_plan', 'command_id'})
    fields(b, common | extra)
    for k in ('root', 'parent_plan', 'proposal'): reference(b[k])
    record_ref(b['checkpoint'], CHECKPOINT); record_ref(b['candidate'], CANDIDATE)
    if (b['protocol'] != protocol or b['scope'] != SCOPE or b['operation'] != OPERATION or
            b['parent_kind'] != PARENT_KIND or row['id'] != KIND_FOR[protocol] + ':' + b['version'] or
            row['version'] != b['version'] or row['item_id'] != b['item_id']):
        raise ValueError('V3保存完整身份、父类型或范围不符。')
    child = b['child_plan'] if protocol == APPLICATION else b['to_plan']; reference(child)
    if child['id'] in (b['root']['id'], b['parent_plan']['id']): raise ValueError('V3保存不得循环。')
    if protocol == APPLICATION:
        request_shape(b['request']); usage(b['usage']); event_ref(b['save_event'], SAVE_EVENT)
        for k in ('driver_before', 'driver_after'): driver_ref(b[k])
        for k in ('authorization_before', 'authorization_after'): auth_ref(b[k])
        if (b['pause_reason'] != PAUSE_REASON or b['request']['id'] != b['item_id'] or
                b['request']['plan'] != b['parent_plan'] or b['request']['proposal'] != b['proposal'] or
                b['request']['current_driver'] != b['driver_before'] or
                b['request']['authorization'] != b['authorization_before']):
            raise ValueError('V3应用须保持完整七字段原请求与暂停原因。')
    else:
        record_ref(b['application'], APPLICATION); reference(b['from_plan'])
        if b['from_plan'] != b['parent_plan'] or b['command_id'] != row['command_id']:
            raise ValueError('V3登记须绑定准确原父及同一命令。')
    return deepcopy(row)


@closed
def successor_plan(value, candidate):
    preparation_row(candidate, CANDIDATE); hashed(value)
    b = candidate['snapshot']
    if (value != b['child_plan'] or value.get('transition') != b['transition'] or
            value.get('previous_plan_reference') != b['parent_plan'] or value['item_id'] != b['item_id'] or
            value[LINEAGE_FIELD] != {'protocol': LINEAGE, 'root': b['root'], 'parent_plan': b['parent_plan'],
                'checkpoint': b['checkpoint'], 'proposal': b['proposal']} or
            value['id'] in (b['root']['id'], b['parent_plan']['id'])):
        raise ValueError('V3保存child须准确等于完整候选及独立lineage。')
    return deepcopy(value)


@closed
def successor_driver(value):
    driver_shape(value)
    if value['state'] != 'PAUSED' or value['reason'] != PAUSE_REASON:
        raise ValueError('V3保存责任仅允许准确暂停起点。')
    return deepcopy(value)


@closed
def transferred_authorization(value):
    auth_shape(value); usage(value['usage_at_transfer'])
    if (type(value['max_attempts_total']) is not int or value['max_attempts_total'] < 1 or
            value['usage_at_transfer']['attempts'] > value['max_attempts_total'] or
            not value['created'] <= value['transferred_at'] < value['dispatch_before']):
        raise ValueError('V3保存原授权累计、上限或最初截止不符。')
    return deepcopy(value)


@closed
def adjacent(checkpoint, candidate, child, before, after, old_auth, auth):
    preparation_row(checkpoint, CHECKPOINT); successor_plan(child, candidate)
    successor_driver(after); transferred_authorization(auth)
    b = candidate['snapshot']; cp = checkpoint['snapshot']; h = cp['history']
    if (b['checkpoint'] != ref(checkpoint) or before != h['driver'] or old_auth != h['authorization'] or
            any(b[k] != cp[k] for k in ('item_id', 'root', 'parent_plan', 'proposal', 'capability', 'request')) or
            b['parent_plan'] != plan_ref(cp['plan']) or candidate['command_id'] != checkpoint['command_id'] or
            cp['original_dispatch_before'] != old_auth['dispatch_before'] or
            b['transition']['usage_at_transition'] != cp['preview']['usage']):
        raise ValueError('V3保存相邻来源、原请求、命令或最初截止不符。')
    from .work_coordinator_handoff_adjustment_contracts import AUTH_EXTRA as OA, DRIVER_EXTRA as OD
    base_auth = {k: deepcopy(v) for k, v in old_auth.items() if k not in AUTH_EXTRA | OA}
    expected_auth = {**base_auth, 'revision': auth['revision'], 'plan_id': child['id'],
        'plan_version': child['version'], 'previous_authorization': {k: old_auth[k] for k in ('id', 'revision')},
        'transferred_at': auth['transferred_at'], 'transfer_registration': ref(candidate),
        'usage_at_transfer': b['transition']['usage_at_transition'], 'command_id': candidate['command_id']}
    base_driver = {k: deepcopy(v) for k, v in before.items() if k not in DRIVER_EXTRA | OD}
    expected_driver = {**base_driver, 'revision': after['revision'], 'updated': after['updated'],
        'plan_id': child['id'], 'plan_version': child['version'], 'state': 'PAUSED', 'reason': PAUSE_REASON,
        'previous_plan_reference': b['parent_plan'], 'transition_registration': ref(candidate)}
    expected_request = {'id': child['item_id'], 'revision': h['task']['revision'],
        'current_driver': {k: before[k] for k in ('id', 'generation', 'revision')}, 'plan': b['parent_plan'],
        'authorization': {k: old_auth[k] for k in ('id', 'revision')},
        'frontier': digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']} for f in h['frontier']]),
        'proposal': b['proposal']}
    if (auth != expected_auth or after != expected_driver or b['request'] != expected_request or
            auth['revision'] == old_auth['revision'] or after['revision'] == before['revision'] or
            not checkpoint['created'] <= b['child_seed']['created'] <= candidate['created'] <= auth['transferred_at'] <= after['updated'] < old_auth['dispatch_before']):
        raise ValueError('V3迁移未逐值保留原请求、身份、累计、截止及暂停状态。')


def save_payload(candidate, child, driver, auth):
    b = candidate['snapshot']
    return {'item_id': child['item_id'], 'root': b['root'], 'parent_plan': b['parent_plan'],
        'child_plan': plan_ref(child), 'candidate': ref(candidate),
        'driver': {k: driver[k] for k in ('id', 'generation', 'revision')},
        'authorization': {k: auth[k] for k in ('id', 'revision')},
        'command_id': candidate['command_id'], 'no_dispatch': True}


@closed
def bodies(checkpoint, candidate, child, before, after, old_auth, auth, event, request):
    adjacent(checkpoint, candidate, child, before, after, old_auth, auth)
    request_shape(request); event_ref(event, SAVE_EVENT)
    if request != checkpoint['snapshot']['request'] or event['payload_digest'] != digest(save_payload(candidate, child, after, auth)):
        raise ValueError('V3保存原请求或实际保存事件载荷摘要不符。')
    b = candidate['snapshot']; common = {k: deepcopy(b[k]) for k in ('item_id', 'root', 'parent_plan', 'proposal')}
    common.update(operation=OPERATION, scope=SCOPE, parent_kind=PARENT_KIND,
        checkpoint=ref(checkpoint), candidate=ref(candidate), **PERMISSIONS)
    app = versioned({**common, 'protocol': APPLICATION, 'child_plan': plan_ref(child),
        'driver_before': {k: before[k] for k in ('id', 'generation', 'revision')},
        'driver_after': {k: after[k] for k in ('id', 'generation', 'revision')},
        'authorization_before': {k: old_auth[k] for k in ('id', 'revision')},
        'authorization_after': {k: auth[k] for k in ('id', 'revision')},
        'usage': b['transition']['usage_at_transition'], 'save_event': event,
        'request': deepcopy(request), 'pause_reason': PAUSE_REASON})
    reg = versioned({**common, 'protocol': REGISTRATION,
        'application': {'id': KIND_FOR[APPLICATION] + ':' + app['version'], 'version': app['version']},
        'from_plan': b['parent_plan'], 'to_plan': plan_ref(child), 'command_id': candidate['command_id']})
    return app, reg
