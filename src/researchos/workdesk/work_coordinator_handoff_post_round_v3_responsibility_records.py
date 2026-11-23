"""Exact same-generation V3 responsibility values; no write/runtime authority."""
from copy import deepcopy
import uuid

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import (
    PERMISSIONS, fields, reference, text, sha, task_revision)
from .work_coordinator_handoff_adjustment_records import (
    hashed, finite, permissions, driver_ref, auth_ref, usage)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_post_round_v2_saved_records import successor_driver
from . import work_coordinator_handoff_post_round_v3_saved_records as saved


COMMAND = 'internal.post-round.v3.responsibility'
TRANSITION = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_RESPONSIBILITY_TRANSITION_V3'
KIND = 'work_coordinator_handoff_post_round_responsibility_transition_v3'
EVENT = 'coordinator_post_round_v3_responsibility_changed'
SCOPE = 'SAME_GENERATION_RESPONSIBILITY_ONLY; SUCCESSOR_RUNTIME_CLOSED'
REQUEST_FIELDS = {'id', 'revision', 'plan', 'current_driver', 'authorization',
                  'frontier', 'saved_registration', 'action'}
STATES = {'resume': ('PAUSED', 'RESERVED'), 'pause': ('RESERVED', 'PAUSED'),
          'release': (('PAUSED', 'RESERVED'), 'RELEASED'),
          'restart_pause': ('RESERVED', 'PAUSED')}
REASONS = {'resume': 'POST_ROUND_V3_EXPLICIT_RESUME',
           'pause': 'POST_ROUND_V3_EXPLICIT_PAUSE',
           'release': 'POST_ROUND_V3_EXPLICIT_RELEASE',
           'restart_pause': 'POST_ROUND_V3_CONTROLLER_RESTART'}
DRIVER_EVENTS = {a: 'work_driver_post_round_v3_' + (
    'restart_paused' if a == 'restart_pause' else a) for a in STATES}
BODY_FIELDS = {'protocol', 'version', 'scope', 'action', 'item_id', 'plan',
    'saved_registration', 'original_driver', 'previous_transition', 'driver_before',
    'driver_after', 'authorization', 'usage', 'original_dispatch_before', 'request',
    'command_id', 'restart_id', 'instance', 'reason', 'automatically_resumed', *PERMISSIONS}


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('V3责任身份须为规范UUID。')


def transition_ref(value):
    reference(value)
    if value['id'] != KIND + ':' + value['version']:
        raise ValueError('V3责任必须使用独立transition引用。')


@closed
def request_shape(value):
    fields(value, REQUEST_FIELDS)
    text(value['id']); task_revision(value['revision']); reference(value['plan'])
    driver_ref(value['current_driver']); auth_ref(value['authorization']); sha(value['frontier'])
    saved.record_ref(value['saved_registration'], saved.REGISTRATION)
    if value['action'] not in {'resume', 'pause', 'release'}:
        raise ValueError('V3显式责任动作无效。')
    return deepcopy(value)


def frontier(history):
    return digest([{'run_id': f['run_id'], 'evidence': f['evidence']['version']}
                   for f in history['frontier']])


@closed
def validate_row(row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    b = row['snapshot']; fields(b, BODY_FIELDS); hashed(b); permissions(b)
    finite(row['created']); text(b['item_id']); text(b['instance'])
    reference(b['plan']); driver_ref(b['original_driver'])
    saved.record_ref(b['saved_registration'], saved.REGISTRATION)
    if b['previous_transition'] is not None: transition_ref(b['previous_transition'])
    if (b['protocol'] != TRANSITION or b['scope'] != SCOPE or
            b['automatically_resumed'] is not False or b['action'] not in STATES or
            row['version'] != b['version'] or row['id'] != KIND + ':' + b['version'] or
            row['item_id'] != b['item_id'] or row['command_id'] != b['command_id']):
        raise ValueError('V3责任完整身份、范围或动作不符。')
    before = b['driver_before']; after = b['driver_after']; action = b['action']
    successor_driver(before); successor_driver(after); saved.transferred_authorization(b['authorization'])
    usage(b['usage']); finite(b['original_dispatch_before'])
    for value in (before, after):
        for name in ('created', 'updated'): finite(value[name])
        text(value['instance']); task_revision(value['item_revision'])
        if (value['item_id'] != b['item_id'] or
                {'id': value['plan_id'], 'version': value['plan_version']} != b['plan']):
            raise ValueError('V3责任修订方案或任务归属不符。')
    allowed, target = STATES[action]
    if isinstance(allowed, str): allowed = (allowed,)
    _uuid(after['revision'])
    expected = {**deepcopy(before), 'revision': after['revision'], 'updated': row['created'],
                'state': target, 'reason': REASONS[action]}
    if action in {'resume', 'restart_pause'}: expected['instance'] = b['instance']
    if action == 'release': expected['released'] = row['created']
    if (before['state'] not in allowed or b['reason'] != REASONS[action] or
            before['revision'] == after['revision'] or after != expected or
            not before['created'] <= before['updated'] <= row['created'] or
            'released' in before):
        raise ValueError('V3责任状态、不可变完整值或修订顺序不符。')
    auth = b['authorization']
    if (auth['item_id'] != b['item_id'] or auth['driver_generation'] != before['generation'] or
            {'id': auth['plan_id'], 'version': auth['plan_version']} != b['plan'] or
            auth['dispatch_before'] != b['original_dispatch_before'] or
            b['usage']['attempts'] > auth['max_attempts_total']):
        raise ValueError('V3责任原授权、generation、累计或最初截止不符。')
    if action == 'restart_pause':
        _uuid(b['restart_id'])
        if b['command_id'] is not None or b['request'] is not None or b['instance'] == before['instance']:
            raise ValueError('V3重启暂停须为新实例的独立内部事实。')
    else:
        _uuid(b['command_id']); request_shape(b['request'])
        expected_request = {'id': b['item_id'], 'revision': before['item_revision'],
            'plan': b['plan'], 'current_driver': {k: before[k] for k in ('id', 'generation', 'revision')},
            'authorization': {k: auth[k] for k in ('id', 'revision')},
            'frontier': b['request']['frontier'], 'saved_registration': b['saved_registration'], 'action': action}
        if b['request'] != expected_request or b['restart_id'] is not None:
            raise ValueError('V3责任须保持准确八字段原请求。')
    if action == 'resume' and (b['usage']['unknown_requests'] or
            b['usage']['attempts'] >= auth['max_attempts_total'] or
            row['created'] >= auth['dispatch_before']):
        raise ValueError('V3责任恢复缺少原累计余量、已知回执或原截止。')
    return deepcopy(row)


@closed
def adjacent(row, original, previous=None):
    """Bind a typed revision to exact original saved values and prior revision.

    The caller reconstructs actual ancestry/materials separately. This helper
    deliberately returns no permission or full-current proof.
    """
    validate_row(row); saved.successor_driver(original['driver'])
    saved.validate_row(original['registration'], saved.REGISTRATION)
    saved.transferred_authorization(original['authorization']); usage(original['usage'])
    b = row['snapshot']; before = original['driver']
    if previous is not None:
        validate_row(previous); before = previous['snapshot']['driver_after']
    if (b['driver_before'] != before or b['plan'] != plan_ref(original['child_plan']) or
            b['saved_registration'] != ref(original['registration']) or
            b['original_driver'] != {k: original['driver'][k] for k in ('id', 'generation', 'revision')} or
            b['previous_transition'] != (ref(previous) if previous is not None else None) or
            b['authorization'] != original['authorization'] or
            b['item_id'] != original['child_plan']['item_id']):
        raise ValueError('V3责任子链未准确绑定原保存和前一责任。')
    comparable = lambda v: {k: x for k, x in v.items() if k != 'elapsed_observed_seconds'}
    if (comparable(b['usage']) != comparable(original['usage']) or
            (b['request'] is not None and b['request']['frontier'] != frontier(original))):
        raise ValueError('V3责任子链累计使用或原前沿已变化。')
    return deepcopy(row)


@closed
def receipt(row, original_registration):
    validate_row(row); saved.validate_row(original_registration, saved.REGISTRATION)
    if row['snapshot']['saved_registration'] != ref(original_registration):
        raise ValueError('V3责任完整回执原登记不符。')
    if row['command_id'] is None: raise ValueError('V3重启暂停没有Human命令回执。')
    return {'transition': deepcopy(row), 'driver': deepcopy(row['snapshot']['driver_after']),
        'authorization': deepcopy(row['snapshot']['authorization']),
        'saved_registration': deepcopy(original_registration),
        'no_dispatch': True, 'automatically_resumed': False}
