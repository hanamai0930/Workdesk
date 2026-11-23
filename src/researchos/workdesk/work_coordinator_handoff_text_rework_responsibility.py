"""Explicit same-generation pause/resume/release and exact full-response replay."""
from contextvars import ContextVar
from copy import deepcopy
import time

from .store import digest, uid
from .work_coordinator_handoff_records import fields, versioned
from .work_coordinator_handoff_historical_assets import _uuid
from .work_coordinator_handoff_text_rework_parent import require_service_db
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_saved_values import driver_ref, auth_ref
from . import work_coordinator_handoff_text_rework_contracts as c

REQUEST_FIELDS = {'id', 'revision', 'current_driver', 'plan', 'registration', 'action'}
EVENT = 'work_driver_coordinator_text_rework_changed_v1'
_pending = ContextVar('exact_coordinator_text_rework_driver_change', default=None)
SAVEPOINT = 'coordinator_text_rework_driver_change'


def request_for(result, previous, action):
    return {'id': result['child_plan']['item_id'], 'revision': previous['item_revision'],
        'current_driver': driver_ref(previous), 'plan': ref(result['child_plan']),
        'registration': ref(result['registration']), 'action': action}


def transformed(previous, action, *, revision, updated):
    _uuid(revision); c.finite(updated)
    target = {'pause': ('RESERVED', 'PAUSED'), 'resume': ('PAUSED', 'RESERVED')}
    if action == 'release':
        if previous['state'] not in {'RESERVED', 'PAUSED'}:
            raise ValueError('返工release只接受本实例仍持有的责任。')
        state = 'RELEASED'
    elif action in target and previous['state'] == target[action][0]:
        state = target[action][1]
    else:
        raise ValueError('返工责任状态不允许该明确动作。')
    if revision == previous['revision'] or updated < previous['updated']:
        raise ValueError('返工责任新修订或时间不符。')
    return {**deepcopy(previous), 'state': state, 'reason': 'COORDINATOR_TEXT_REWORK_EXPLICIT_' + action.upper(),
        'revision': revision, 'updated': updated}


def response_for(result, after):
    return {'driver': deepcopy(after), 'authorization': auth_ref(result['authorization']),
        'registration': ref(result['registration']), 'no_dispatch': True, 'automatically_resumed': False}


def body_for(result, request, after):
    return versioned({'protocol': c.RECEIPT, 'operation': 'DRIVER_CHANGE', 'request': deepcopy(request),
        'request_hash': digest(request), 'capability': ref(result['capability']),
        'registration': ref(result['registration']), 'run': None, 'driver': driver_ref(after),
        'receipt': response_for(result, after), **c.PERMISSIONS})


def replay_changes(history, result, pending=None):
    sources, ledger, db = history.sources, history.ledger, history.db
    previous = result['driver']; receipts = []
    saved_event = sources._event(db, c.EVENTS[c.RECEIPT], result['receipt'])
    boundary = saved_event
    found = []
    for row in sources.all(db, c.KINDS[c.RECEIPT]):
        if row.get('item_id') != previous['item_id'] or row.get('snapshot', {}).get('operation') != 'DRIVER_CHANGE':
            continue
        c.validate_receipt(row)
        found.append((sources._event(db, c.EVENTS[c.RECEIPT], row), row))
    result['responsibility_receipts'] = []
    for receipted, row in sorted(found, key=lambda pair: pair[0]['seq']):
        boundary = replay_change(history, result, row, boundary, pending)
    previous = result['driver']
    receipts = result['responsibility_receipts']
    result['driver'] = previous; result['responsibility_receipts'] = receipts
    return result


def replay_change(history, result, row, boundary, pending=None):
    sources, ledger, db = history.sources, history.ledger, history.db
    previous = result['driver']
    c.validate_receipt(row)
    receipted = sources._event(db, c.EVENTS[c.RECEIPT], row)
    body = row['snapshot']; request = body['request']; fields(request, REQUEST_FIELDS)
    revision = body['driver']['revision']
    carrier = sources.get(db, 'work_driver_revision', 'driver-version:' + revision)
    actual = {k: val for k, val in carrier.items() if k != 'driver_id'}; actual['id'] = carrier['driver_id']
    expected = transformed(previous, request['action'], revision=revision, updated=actual['updated'])
    event = sources._event(db, EVENT, {**actual, 'no_dispatch': True})
    if (request != request_for(result, previous, request['action']) or actual != expected or
            body != body_for(result, request, actual) or
            not boundary['seq'] < event['seq'] < receipted['seq'] or
            not boundary['created'] <= actual['updated'] <= event['created'] <= row['created'] <= receipted['created'] or
            actual['updated'] >= result['authorization']['dispatch_before']):
        raise ValueError('返工责任原请求、同generation变换、登记、完整回执或顺序不符。')
    owner = 'text-responsibility:' + row['id']
    ledger.row('work_driver_revision', carrier, owner)
    ledger.event_at(event['seq'], EVENT, {**actual, 'no_dispatch': True}, owner)
    ledger.row(c.KINDS[c.RECEIPT], row, owner)
    ledger.event_at(receipted['seq'], receipted['kind'], row, owner)
    command = db.execute('SELECT * FROM commands WHERE id=?', (row['command_id'],)).fetchone()
    if command is None:
        if type(pending) is not _DriverChange:
            raise ValueError('返工责任缺少原完整命令或当前唯一真实命令owner。')
        pending.require_witness(history, row, event, receipted)
    else:
        ledger.command(row['command_id'], c.DRIVER_CHANGE, request, body['receipt'], owner, not_before=receipted['created'])
    result['driver'] = actual
    result.setdefault('responsibility_receipts', []).append(row)
    return receipted


class _DriverChange:
    def __init__(self, service, db, request):
        require_service_db(service, db); fields(request, REQUEST_FIELDS)
        if (service.command_kind != c.DRIVER_CHANGE or service.command_payload_hash != digest(request) or _pending.get() is not None):
            raise ValueError('返工责任变更须属于准确六字段唯一真实命令。')
        self.service, self.db, self.request = service, db, deepcopy(request)
        self.identity, self.instance, self.active = _uuid(service.command_identity), service.instance, True
        self.tail = db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        self.receipt = None; self._fingerprint = digest((self.identity, self.instance, self.request, self.tail))

    def require(self):
        require_service_db(self.service, self.db)
        if (not self.active or _pending.get() is not self or self.service.command_identity != self.identity or
                self.service.command_kind != c.DRIVER_CHANGE or self.service.command_payload_hash != digest(self.request) or
                self.service.instance != self.instance or self._fingerprint != digest((self.identity, self.instance, self.request, self.tail)) or
                self.db.execute('SELECT 1 FROM commands WHERE id=?', (self.identity,)).fetchone()):
            raise ValueError('返工责任pending不属于本次真实命令。')

    def require_witness(self, history, row, event, receipted):
        self.require()
        if (history.sources.db.connection is not self.db or row != self.receipt or row['command_id'] != self.identity or
                row['snapshot']['request'] != self.request or event['seq'] != self.tail + 1 or receipted['seq'] != self.tail + 2):
            raise ValueError('返工责任pending只消费本命令真实完整事件和原响应。')


@c.closed
def change(service, db, request):
    from .work_coordinator_handoff_text_rework_current import TextCurrent, require_saved_live
    from .work_coordinator_handoff_text_rework_child_history import TextChildHistory
    owner = _DriverChange(service, db, request)
    db.execute('SAVEPOINT ' + SAVEPOINT); token = _pending.set(owner)
    try:
        result = TextCurrent(service).read(db, service.teamwork.current(db, request['id']))
        previous = result['driver']
        if request != request_for(result, previous, request['action']):
            raise ValueError('返工责任请求不等于当前登记及准确版本。')
        after = transformed(previous, request['action'], revision=uid(), updated=time.time())
        service.collaboration.put(db, 'work_driver', after)
        service.collaboration.put(db, 'work_driver_revision', {**after, 'id': 'driver-version:' + after['revision'], 'driver_id': after['id']})
        service.store.event(None, None, EVENT, {**after, 'no_dispatch': True}, db)
        body = body_for(result, request, after)
        row = {'id': c.KINDS[c.RECEIPT] + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': request['id'], 'created': time.time(), 'command_id': owner.identity}
        c.validate_receipt(row)
        if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
            raise ValueError('返工责任完整回执不得覆盖旧载体。')
        service.collaboration.put(db, c.KINDS[c.RECEIPT], row)
        service.store.event(None, None, c.EVENTS[c.RECEIPT], row, db); owner.receipt = deepcopy(row)
        checked = TextChildHistory(db, service.locations.data, ref(result['candidate']), responsibility_owner=owner).read()
        require_saved_live(service, db, checked); owner.require()
        if checked['driver'] != after or checked['responsibility_receipts'][-1] != row:
            raise ValueError('返工责任变更提交前完整尾不符。')
        response = response_for(result, after)
        if row['snapshot']['receipt'] != response:
            raise ValueError('返工责任最终响应已变化。')
        db.execute('RELEASE ' + SAVEPOINT)
        return response
    except BaseException:
        db.execute('ROLLBACK TO ' + SAVEPOINT); db.execute('RELEASE ' + SAVEPOINT)
        raise
    finally:
        owner.active = False; _pending.reset(token)
