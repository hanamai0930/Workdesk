"""Independent post-ROUND saved-edge shapes. Frozen preparation stays data."""
from copy import deepcopy
import json
import time
import uuid

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_records import (
    hashed, finite, original_driver, original_authorization, auth_ref)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD
from .work_coordinator_handoff_post_round_v2_candidates import (
    PREFIX, OPERATION, CHECKPOINT, CANDIDATE, LINEAGE, ref,
    validate_row as preparation_row, KINDS as PREP_KINDS, EVENTS as PREP_EVENTS)

APPLICATION = PREFIX + 'APPLICATION_V2'
REGISTRATION = PREFIX + 'REGISTRATION_V2'
SCOPE = 'SAVED_POST_ROUND_ADJUSTMENT_V2; PAUSED_TRANSFER; SUBSEQUENT_RUNTIME_NOT_OPEN'
PAUSE_REASON = 'POST_ROUND_ADJUSTMENT_SAVED_RECHECK_REQUIRED'
SAVE_EVENT = 'coordinator_post_round_adjustment_saved_v2'
DRIVER_EVENT = 'work_driver_coordinator_post_round_adjustment_saved_v2'
KIND_FOR = {**PREP_KINDS, APPLICATION: 'work_coordinator_handoff_post_round_application_v2',
    REGISTRATION: 'work_coordinator_handoff_post_round_registration_v2'}
EVENTS = {**PREP_EVENTS, APPLICATION: 'coordinator_post_round_adjustment_applied_v2',
    REGISTRATION: 'coordinator_post_round_adjustment_registered_v2'}
AUTH_EXTRA = {'previous_authorization', 'transferred_at', 'transfer_registration', 'usage_at_transfer'}
DRIVER_EXTRA = {'previous_plan_reference', 'transition_registration'}
REQUEST_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'authorization', 'frontier', 'proposal'}


@closed
def validate_row(row, protocol):
    if protocol in PREP_KINDS:
        return preparation_row(row, protocol)
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']; hashed(body); finite(row['created']); uuid.UUID(row['command_id'])
    common = {'protocol', 'version', 'operation', 'scope', 'item_id', 'root', 'parent_plan',
        'proposal', 'checkpoint', 'candidate', *PERMISSIONS}
    extra = ({'child_plan', 'driver_before', 'driver_after', 'authorization_before',
        'authorization_after', 'usage', 'save_event', 'request', 'pause_reason'} if protocol == APPLICATION
        else {'application', 'from_plan', 'to_plan', 'command_id'})
    fields(body, common | extra)
    for k in ('root', 'parent_plan', 'proposal', 'checkpoint', 'candidate'): reference(body[k])
    if (protocol not in (APPLICATION, REGISTRATION) or body['protocol'] != protocol or
            body['operation'] != OPERATION or body['scope'] != SCOPE or
            row['id'] != KIND_FOR[protocol] + ':' + body['version'] or
            row['version'] != body['version'] or row['item_id'] != body['item_id'] or
            any(body[k] is not expected for k, expected in PERMISSIONS.items())):
        raise ValueError('新保存记录完整身份、范围或权限不符。')
    if protocol == APPLICATION:
        fields(body['request'], REQUEST_FIELDS)
        if body['pause_reason'] != PAUSE_REASON: raise ValueError('新保存须从准确暂停原因开始。')
    return deepcopy(row)


@closed
def successor_plan(value, candidate):
    preparation_row(candidate, CANDIDATE); hashed(value)
    if value != candidate['snapshot']['child_plan']:
        raise ValueError('新保存child不是实际重建的完整候选。')
    link = value[LINEAGE_FIELD]; fields(link, {'protocol', 'root', 'parent_plan', 'checkpoint', 'proposal'})
    if link['protocol'] != LINEAGE or value['id'] in (link['root']['id'], link['parent_plan']['id']):
        raise ValueError('新保存child须有独立准确lineage。')
    return deepcopy(value)


@closed
def successor_driver(value):
    if not DRIVER_EXTRA <= set(value): raise ValueError('新保存责任缺少来源。')
    original_driver({k: v for k, v in value.items() if k not in DRIVER_EXTRA})
    reference(value['previous_plan_reference']); reference(value['transition_registration'])
    if value['state'] not in {'PAUSED', 'RESERVED', 'RELEASED'}:
        raise ValueError('新保存责任状态无效。')
    return deepcopy(value)


@closed
def transferred_authorization(value):
    if not AUTH_EXTRA <= set(value): raise ValueError('新保存授权缺少来源。')
    original_authorization({k: v for k, v in value.items() if k not in AUTH_EXTRA})
    auth_ref(value['previous_authorization']); reference(value['transfer_registration'])
    finite(value['transferred_at'])
    return deepcopy(value)


@closed
def adjacent(checkpoint, candidate, child, before, after, old_auth, auth):
    preparation_row(checkpoint, CHECKPOINT); successor_plan(child, candidate)
    successor_driver(after); transferred_authorization(auth)
    body = candidate['snapshot']; h = checkpoint['snapshot']['history']
    if (body['checkpoint'] != ref(checkpoint) or before != h['driver'] or old_auth != h['authorization'] or
            body['parent_plan'] != plan_ref(checkpoint['snapshot']['plan'])):
        raise ValueError('新保存相邻父来源不符。')
    # Preserve every immutable original field. Only the named transfer fields
    # change; original authorization identity, generation and deadline survive.
    from .work_coordinator_handoff_adjustment_contracts import AUTH_EXTRA as OA, DRIVER_EXTRA as OD
    base_auth = {k: deepcopy(v) for k, v in old_auth.items() if k not in AUTH_EXTRA | OA}
    expected_auth = {**base_auth, 'revision': auth['revision'], 'plan_id': child['id'],
        'plan_version': child['version'], 'previous_authorization': {k: old_auth[k] for k in ('id', 'revision')},
        'transferred_at': auth['transferred_at'], 'transfer_registration': ref(candidate),
        'usage_at_transfer': body['transition']['usage_at_transition'], 'command_id': candidate['command_id']}
    base_driver = {k: deepcopy(v) for k, v in before.items() if k not in DRIVER_EXTRA | OD}
    expected_driver = {**base_driver, 'revision': after['revision'], 'updated': after['updated'],
        'plan_id': child['id'], 'plan_version': child['version'], 'state': 'PAUSED', 'reason': PAUSE_REASON,
        'previous_plan_reference': body['parent_plan'], 'transition_registration': ref(candidate)}
    if (auth != expected_auth or after != expected_driver or auth['revision'] == old_auth['revision'] or
            after['revision'] == before['revision'] or
            not candidate['created'] <= auth['transferred_at'] <= after['updated'] < auth['dispatch_before']):
        raise ValueError('新保存授权责任未准确沿袭最初身份、累计、截止或暂停状态。')


def save_payload(service, candidate, child, driver, auth):
    b = candidate['snapshot']
    return {'item_id': child['item_id'], 'root': b['root'], 'parent_plan': b['parent_plan'],
        'child_plan': plan_ref(child), 'candidate': ref(candidate),
        'driver': service.work_drivers.reference(driver), 'authorization': service.work_units.auth_ref(auth),
        'command_id': candidate['command_id'], 'no_dispatch': True}


def bodies(service, checkpoint, candidate, child, before, after, old_auth, auth, event, request):
    b = candidate['snapshot']
    common = {k: deepcopy(b[k]) for k in ('item_id', 'root', 'parent_plan', 'proposal')}
    common.update(operation=OPERATION, scope=SCOPE, checkpoint=ref(checkpoint), candidate=ref(candidate), **PERMISSIONS)
    app = versioned({**common, 'protocol': APPLICATION, 'child_plan': plan_ref(child),
        'driver_before': service.work_drivers.reference(before), 'driver_after': service.work_drivers.reference(after),
        'authorization_before': service.work_units.auth_ref(old_auth), 'authorization_after': service.work_units.auth_ref(auth),
        'usage': b['transition']['usage_at_transition'], 'save_event': event,
        'request': deepcopy(request), 'pause_reason': PAUSE_REASON})
    reg = versioned({**common, 'protocol': REGISTRATION,
        'application': {'id': KIND_FOR[APPLICATION] + ':' + app['version'], 'version': app['version']},
        'from_plan': b['parent_plan'], 'to_plan': plan_ref(child), 'command_id': candidate['command_id']})
    return app, reg


class SavedRecords:
    def __init__(self, service): self.s = service

    def get(self, db, protocol, identity):
        reference(identity)
        row = validate_row(self.s.collaboration.get(db, KIND_FOR[protocol], identity['id']), protocol)
        if ref(row) != identity: raise ValueError('新保存记录引用版本不符。')
        return row

    def put(self, db, protocol, body):
        row = {'id': KIND_FOR[protocol] + ':' + body['version'], 'version': body['version'],
            'snapshot': body, 'item_id': body['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        validate_row(row, protocol)
        if db.execute('SELECT 1 FROM collaboration WHERE kind=? AND id=?', (KIND_FOR[protocol], row['id'])).fetchone():
            raise ValueError('新保存身份已存在，应重放原命令。')
        self.s.collaboration.put(db, KIND_FOR[protocol], row)
        self.s.store.event(None, None, EVENTS[protocol], row, db)
        return row
