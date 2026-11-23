"""Private actual review reservation, business binding and response witness.

Public service integration waits for the full saved-child lifecycle. Every
intermediate writer requires the unique exact live REVIEW_SEND scope.
"""
from copy import deepcopy
import time

from .store import digest
from .work_readiness_observations import record, EVENT as READINESS_EVENT
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_historical_assets import HistoricalSources, FrozenAssets, _json
from .work_coordinator_handoff_historical_namespace import CanonicalLedger
from .work_coordinator_handoff_text_rework_parent import TextReworkParent
from .work_coordinator_handoff_text_rework_command_scope import _ReviewCommand
from . import work_coordinator_handoff_text_rework_contracts as c


@c.closed
def open_review(service, db, request, command_owner):
    request = c.review_request(request)
    if (type(command_owner) is not _ReviewCommand or command_owner.service is not service or
            command_owner.db is not db or command_owner.request != request):
        raise ValueError('返工审阅开启须属于当前唯一准确新命令。')
    command_owner.require()
    plan = service.teamwork.current(db, request['id'])
    if plan is None:
        raise ValueError('返工审阅缺少当前实际方案。')
    parent = TextReworkParent.current(service, db, plan)
    if parent.parent_type != 'POST_V3':
        raise ValueError('文字返工首批叶子入口目前只覆盖准确已运行POST_V3 B_COMPLETED；其它父叶子尚待独立准入验证。')
    command_owner.bind_parent(parent)
    # Freeze the independently proved current material before the first SQL
    # event, so history_at_edge can verify real bytes even directly after B.
    service.work_file_rework_applications.preserve(parent.history['material_observation'])
    if any(row.get('item_id') == plan['item_id'] for row in
            service.collaboration.all(db, c.KINDS[c.CAPABILITY])):
        raise ValueError('首批返工能力只能显式开启一次，未知或失败回执不重发。')
    observed_at = time.time()
    record(service, db, parent.item, plan, parent.driver, request, c.SEND, parent.catalog)
    raw = db.execute('SELECT * FROM work_readiness_observations WHERE id=?',
        ('work-readiness:' + service.command_identity,)).fetchone()
    if raw is None:
        raise ValueError('返工首写未产生真实资源观察。')
    ready = _json(raw['data'])
    pointer = {'id': ready['id'], 'version': ready['version'], 'no_dispatch': True}
    events = [row for row in db.execute('SELECT * FROM events WHERE kind=?', (READINESS_EVENT,))
        if _json(row['payload']) == pointer and row['task_id'] is None and row['attempt_id'] is None]
    if len(events) != 1 or events[0]['seq'] != parent.tail + 1:
        raise ValueError('返工首readiness须紧接本命令开始时的真实全库事件尾。')
    readiness = {'id': ready['id'], 'version': ready['version'], 'event':
        {'seq': events[0]['seq'], 'kind': READINESS_EVENT, 'payload_digest': digest(pointer)}}
    observation = parent.observation(request['unit'], readiness, observed_at)
    if request != c.request_for(observation):
        raise ValueError('返工原请求不等于实际完整父状态和准确协调定义。')
    body = versioned({'protocol': c.CAPABILITY, 'scope': c.SCOPE, 'root': parent.root,
        'parent_type': parent.parent_type, 'parent_plan': request['plan'], 'parent_tail': parent.tail,
        'request': request, 'request_hash': digest(request), 'observation': observation,
        'allowed_actions': c.ACTIONS, 'reply_schema': {'rework': c.REWORK_SCHEMA, 'pause': c.PAUSE_SCHEMA},
        **c.PERMISSIONS})
    capability = {'id': c.KINDS[c.CAPABILITY] + ':' + body['version'], 'version': body['version'],
        'snapshot': body, 'item_id': plan['item_id'], 'created': time.time(), 'command_id': service.command_identity}
    c.validate_capability(capability)
    if db.execute('SELECT 1 FROM collaboration WHERE id=?', (capability['id'],)).fetchone():
        raise ValueError('返工能力准确内容身份已经存在。')
    service.collaboration.put(db, c.KINDS[c.CAPABILITY], capability)
    service.store.event(None, None, c.EVENTS[c.CAPABILITY], capability, db)
    sources = HistoricalSources(db, FrozenAssets(service.locations.data))
    ledger = CanonicalLedger(sources)
    command_owner.bind_capability(sources, capability)
    rebuilt = TextReworkParent.history_at_edge(db, service.locations.data,
        {k: capability[k] for k in ('id', 'version')}, ledger, command_owner=command_owner)
    return {'parent_current': parent, 'parent_history': rebuilt, 'capability': deepcopy(capability)}


def _owner(service, db):
    from .work_coordinator_handoff_text_rework_command_scope import _pending
    owner = _pending.get()
    if type(owner) is not _ReviewCommand or owner.service is not service or owner.db is not db:
        raise ValueError('审阅writer缺少本Service当前唯一真实命令owner。')
    return owner.require()


@c.closed
def reserve_review(service, db, request):
    """Reserve one real task/attempt; does not launch a worker or bind business."""
    from .official_work import send
    from .work_coordinator_handoff_text_rework_values import review_input, review_prompt
    from .work_coordinator_handoff_text_rework_review_values import metadata
    from .work_coordinator_handoff_text_rework_review_live import require_live
    owner = _owner(service, db)
    opened = open_review(service, db, request, owner)
    cap = opened['capability']; obs = cap['snapshot']['observation']
    opened['parent_history']['sources'].unchanged(db)
    item = require_live(service, db, cap, obs['driver'])
    frozen = review_input(cap); prompt = review_prompt(frozen)
    if len(prompt) > 160000:
        raise ValueError('审阅完整提示超过160000字；未截断或保留。')
    selected = service.resources.selection(db, frozen['unit']['member']['reference']['member_id'])
    if selected['reference'] != frozen['unit']['member']['reference']:
        raise ValueError('审阅准确成员或连接引用变化。')
    expected_files = {}
    for dependency in frozen['file_artifacts']:
        for name, record in dependency['manifest'].items():
            if name in expected_files and expected_files[name] != record:
                raise ValueError('审阅依赖包含同路径不同版本。')
            expected_files[name] = record
    effective = {'project_id': item['project_id'], 'goal': prompt,
        **{k: selected['member'][k] for k in ('provider', 'model', 'effort', 'tier')},
        'resource_selection': selected['reference'],
        'attachments': [{'file': name, 'route': 'native'} for name in expected_files]}
    effective, selection = service.resources.before(db, 'independent.send', effective)
    # Resource selection may have persisted an existing connection reference.
    # Build a new real-source historical observation after any such write.
    sources = HistoricalSources(db, FrozenAssets(service.locations.data))
    ledger = CanonicalLedger(sources)
    TextReworkParent.history_at_edge(db, service.locations.data, {k: cap[k] for k in ('id', 'version')},
        ledger, command_owner=owner)
    require_live(service, db, cap, obs['driver'])
    meta = metadata(cap)
    receipt = service.resources.complete(db, send(service, db, effective, cooperative=meta), selection)
    attempt = service.store.attempt(receipt['launch_attempt'], db)
    task = service.store.task(attempt['task_id'], db)
    if task['spec']['materials'] != expected_files:
        raise ValueError('审阅实际冻结材料与独立准确依赖不符。')
    run = {'id': 'unit-run:' + attempt['id'], **meta, 'cooperative': meta,
        'item_revision': item['revision'], 'task_id': task['id'], 'attempt_id': attempt['id'],
        'owner': attempt['owner'], 'initial_session_id': attempt['session_id'],
        'connection_fingerprint': digest(task['spec']['execution_connection']), 'input': frozen,
        'retry_of': None, 'ordinal_total': obs['usage']['attempts'] + 1, 'created': time.time(),
        'command_id': owner.identity, 'scheduling': 'EXPLICIT_SERIAL_STEP', 'flow': None,
        'task_spec_version': digest(task['spec'])}
    service.collaboration.put(db, 'work_unit_run', run)
    service.store.event(task['id'], attempt['id'], 'cooperative_unit_reserved', run, db)
    permit_review(service, db, item, run)
    return {**receipt, 'unit_run_id': run['id'], 'cooperative': meta}


@c.closed
def permit_review(service, db, item, run):
    from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
    from .work_coordinator_handoff_text_rework_review_live import require_live
    owner = _owner(service, db)
    if owner.capability is None:
        raise ValueError('审阅许可缺少本命令真实能力。')
    replay = ReviewReplay(db, service.locations.data,
        {k: owner.capability[k] for k in ('id', 'version')}, command_owner=owner)
    checked = replay.read(before_binding=True)
    if checked['run'] != run or item != service.work_items.item(db, run['item_id']):
        raise ValueError('审阅准入须属于实际唯一保留和当前准确业务。')
    require_live(service, db, replay.capability, checked['driver'], excluding={run['attempt_id']})


@c.closed
def bind_review(service, db, receipt):
    from .work_coordinator_handoff_text_rework_review_values import linked_driver
    from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
    owner = _owner(service, db)
    run = service.work_units.reservation(db, receipt['launch_attempt'])
    ref = receipt.get('work_item')
    if (run is None or run['command_id'] != owner.identity or run['id'] != receipt.get('unit_run_id') or
            not ref or ref != {'id': run['item_id'], 'revision': run['item_revision'] + 1,
                'dispatch_id': 'dispatch:' + run['attempt_id']}):
        raise ValueError('审阅须先准确完成一次业务dispatch绑定。')
    previous = owner.capability['snapshot']['observation']['driver']
    if service.work_drivers.current(db, run['item_id']) != previous:
        raise ValueError('审阅执行绑定前原driver已变化。')
    row = linked_driver(previous, run, ref['revision'], revision=previous['revision'], updated=previous['updated'])
    service.work_drivers.save(db, row, 'execution_linked')
    result = ReviewReplay(db, service.locations.data,
        {k: owner.capability[k] for k in ('id', 'version')}, command_owner=owner).read(before_receipt=True)
    if result['response'] != receipt:
        raise ValueError('审阅绑定后完整返回对象与实际独立重建不符。')
    return receipt


@c.closed
def finish_review(service, db, request, receipt):
    from .work_coordinator_handoff_text_rework_review_values import receipt_body
    from .work_coordinator_handoff_text_rework_review_replay import ReviewReplay
    owner = _owner(service, db)
    if request != owner.request:
        raise ValueError('审阅末尾原请求已变化。')
    cap = owner.capability; ref = {k: cap[k] for k in ('id', 'version')}
    rebuilt = ReviewReplay(db, service.locations.data, ref, command_owner=owner).read(before_receipt=True)
    if rebuilt['response'] != receipt:
        raise ValueError('审阅完整回执必须等于业务绑定后实际返回对象。')
    body = receipt_body(cap, rebuilt['run'], rebuilt['driver'], receipt)
    row = {'id': c.KINDS[c.RECEIPT] + ':' + body['version'], 'version': body['version'], 'snapshot': body,
        'item_id': cap['item_id'], 'created': time.time(), 'command_id': owner.identity}
    c.validate_receipt(row)
    if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
        raise ValueError('审阅回执见证不能覆盖既有内容。')
    service.collaboration.put(db, c.KINDS[c.RECEIPT], row)
    service.store.event(None, None, c.EVENTS[c.RECEIPT], row, db)
    checked = ReviewReplay(db, service.locations.data, ref, command_owner=owner).read()
    if checked['response'] != receipt:
        raise ValueError('审阅回执写后独立核对失败。')
    owner.bind_response(receipt)
    return receipt
