"""Real official child reservations, exact business binding and full receipts."""
from copy import deepcopy
import time

from .store import digest, uid
from .official_work import send
from .work_coordinator_handoff_text_rework_saved_records import ref
from .work_coordinator_handoff_text_rework_current import require_saved_live
from .work_coordinator_handoff_text_rework_review_values import linked_driver
from .work_coordinator_handoff_text_rework_child_scope import _ChildSend, _ChildCheck, _pending_send, _pending_check
from . import work_coordinator_handoff_text_rework_child_values as v
from . import work_coordinator_handoff_text_rework_contracts as c


def _owner(service, db, checking=False):
    owner = (_pending_check if checking else _pending_send).get()
    if type(owner) is not (_ChildCheck if checking else _ChildSend) or owner.service is not service or owner.db is not db:
        raise ValueError('后继writer须属于当前准确typed命令scope。')
    return owner.require()


def _write_receipt(service, db, owner, result, operation, run, response):
    body = v.receipt_body(result, operation, owner.request, run, response)
    row = {'id': c.KINDS[c.RECEIPT] + ':' + body['version'], 'version': body['version'],
        'snapshot': body, 'item_id': owner.request['id'], 'created': time.time(), 'command_id': owner.identity}
    c.validate_receipt(row)
    if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
        raise ValueError('后继完整回执不得覆盖既有载体。')
    service.collaboration.put(db, c.KINDS[c.RECEIPT], row)
    service.store.event(None, None, c.EVENTS[c.RECEIPT], row, db)
    owner.witness = deepcopy(row)
    return row


@c.closed
def reserve_child(service, db, request):
    owner = _owner(service, db)
    if request != owner.request:
        raise ValueError('后继保留原请求发生变化。')
    result = owner.before(); plan = result['child_plan']
    unit = next((u for u in plan['units'] if request['unit'] == {'key': u['key'], 'revision': digest(u)}), None)
    if unit is None:
        raise ValueError('后继保留须选择本方案准确原定义。')
    proposal = result['proposal'] if unit['role'] != 'coordinate' else None
    if request != v.send_request(result, unit, proposal):
        raise ValueError('后继保留须绑定当前完整前沿、原授权及准确未消费提案。')
    frozen = v.input_value(result, unit, proposal); prompt = v.prompt(frozen)
    if time.time() >= result['authorization']['dispatch_before']:
        raise ValueError('后继原最初派发截止已到。')
    selected = service.resources.selection(db, unit['member']['reference']['member_id'])
    if selected['reference'] != unit['member']['reference']:
        raise ValueError('后继准确成员或连接引用已变化。')
    manifest = {}
    for dep in frozen['file_artifacts']:
        for name, value in dep['manifest'].items():
            if name in manifest and manifest[name] != value:
                raise ValueError('后继冻结依赖包含同路径不同版本。')
            manifest[name] = value
    baseline = result['history']['material_observation']; service.work_file_rework_applications.preserve(baseline)
    item = service.work_items.item(db, request['id'])
    effective = {'project_id': item['project_id'], 'goal': prompt,
        **{k: selected['member'][k] for k in ('provider', 'model', 'effort', 'tier')},
        'resource_selection': selected['reference'], 'attachments': [{'file': name, 'route': 'native'} for name in manifest]}
    effective, selection = service.resources.before(db, 'independent.send', effective)
    again = owner.before()
    if v.input_value(again, unit, proposal) != frozen:
        raise ValueError('后继保留前独立current完整输入变化。')
    driver = result['driver']; auth = result['authorization']
    meta = {'item_id': item['id'], 'source_item_revision': item['revision'], 'driver_id': driver['id'],
        'generation': driver['generation'], 'driver_revision': driver['revision'], 'plan_id': plan['id'],
        'plan_version': plan['version'], 'unit_key': unit['key'], 'unit_revision': digest(unit),
        'input_version': digest(frozen), 'authorization': {k: auth[k] for k in ('id', 'revision')}}
    if proposal:
        meta['proposal'] = ref(proposal)
    receipt = service.resources.complete(db, send(service, db, effective, cooperative=meta), selection)
    attempt = service.store.attempt(receipt['launch_attempt'], db); task = service.store.task(attempt['task_id'], db)
    if task['spec']['materials'] != manifest:
        raise ValueError('后继实际冻结材料不等于独立输入依赖。')
    run = {'id': 'unit-run:' + attempt['id'], **meta, 'cooperative': meta, 'item_revision': item['revision'],
        'task_id': task['id'], 'attempt_id': attempt['id'], 'owner': attempt['owner'],
        'initial_session_id': attempt['session_id'], 'connection_fingerprint': digest(task['spec']['execution_connection']),
        'input': frozen, 'retry_of': None, 'ordinal_total': result['history']['usage']['attempts'] + 1,
        'created': time.time(), 'command_id': owner.identity, 'scheduling': 'EXPLICIT_SERIAL_STEP',
        'flow': None, 'task_spec_version': digest(task['spec'])}
    service.collaboration.put(db, 'work_unit_run', run)
    service.store.event(task['id'], attempt['id'], 'cooperative_unit_reserved', run, db)
    permit_child(service, db, item, run)
    return {**receipt, 'unit_run_id': run['id'], 'cooperative': meta}


@c.closed
def permit_child(service, db, item, run):
    owner = _owner(service, db)
    result = owner.replay(before_binding=True)
    if result.get('pending_run') != run or item != service.work_items.item(db, run['item_id']):
        raise ValueError('后继准入只属于本次唯一真实保留。')
    require_saved_live(service, db, result, reserving=owner)


@c.closed
def bind_child(service, db, receipt):
    owner = _owner(service, db)
    run = service.work_units.reservation(db, receipt['launch_attempt']); binding = receipt.get('work_item')
    if (run is None or run['command_id'] != owner.identity or receipt.get('unit_run_id') != run['id'] or
            binding != {'id': run['item_id'], 'revision': run['item_revision'] + 1, 'dispatch_id': 'dispatch:' + run['attempt_id']}):
        raise ValueError('后继须先完成真实唯一准确业务dispatch绑定。')
    previous = service.work_drivers.current(db, run['item_id'])
    if previous['revision'] != run['driver_revision'] or previous['item_revision'] != run['item_revision']:
        raise ValueError('后继执行绑定前实际driver已变化。')
    value = linked_driver(previous, run, binding['revision'], revision=previous['revision'], updated=previous['updated'])
    service.work_drivers.save(db, value, 'execution_linked')
    result = owner.replay(before_receipt=True)
    if result['last_send_response'] != receipt:
        raise ValueError('后继业务绑定后的完整响应不等于独立重建。')
    return receipt


@c.closed
def finish_send(service, db, request, receipt):
    owner = _owner(service, db)
    if request != owner.request:
        raise ValueError('后继SEND提交前原请求变化。')
    result = owner.replay(before_receipt=True)
    run = service.work_units.reservation(db, receipt['launch_attempt'])
    if result['last_send_response'] != receipt:
        raise ValueError('后继SEND完整响应不等于实际独立运行。')
    _write_receipt(service, db, owner, result, 'SEND', run, receipt)
    owner.bind_response(receipt)
    return receipt


@c.closed
def capture_child(service, db, request):
    owner = _owner(service, db, True)
    if request != owner.request:
        raise ValueError('后继CHECK原请求变化。')
    result = owner.before()
    frames = [f for f in result['history']['frames'] if f['run']['id'] == request['run_id']]
    if len(frames) != 1:
        raise ValueError('后继CHECK缺少准确完整协调来源。')
    frame = frames[0]; run = frame['run']; now = time.time()
    snapshot = v.check_snapshot(result, run, frame, request, now)
    proposal = {'id': 'work-proposal:' + uid(), 'version': digest(snapshot), 'snapshot': snapshot,
        'item_id': request['id'], 'created': now, 'command_id': owner.identity}
    service.collaboration.put(db, 'work_proposal', proposal)
    service.collaboration.put(db, 'work_proposal_current', {'id': 'proposal-current:' + request['id'], 'proposal_id': proposal['id']})
    service.store.event(run['task_id'], run['attempt_id'], 'coordinator_proposal_checked', proposal, db)
    response = {'proposal': proposal, 'no_dispatch': True}
    _write_receipt(service, db, owner, result, 'PROPOSAL_CHECK', run, response)
    owner.bind_response(response)
    return response
