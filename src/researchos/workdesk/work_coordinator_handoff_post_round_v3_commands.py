"""V3 complete receipt witness; Store alone commits the command row."""
from copy import deepcopy
import json
import time

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_post_round_v3_command_scope import pending_request

RECEIPT_KIND = 'work_coordinator_handoff_post_round_successor_receipt_v3'
RECEIPT_PROTOCOL = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_RECEIPT_V3'
RECEIPT_EVENT = 'coordinator_post_round_successor_receipt_saved_v3'


def body_for(owner, plan, run, driver, receipt):
    return versioned({'protocol': RECEIPT_PROTOCOL, 'item_id': plan['item_id'], 'plan': plan_ref(plan),
        'capability': owner.ref(owner._successor), 'run': deepcopy(run), 'driver': deepcopy(driver),
        'request': deepcopy(owner._successor['snapshot']['request']), 'receipt': deepcopy(receipt)})


def reconstruct_receipt(owner, db, plan, run, driver, dispatch):
    service = owner.s; request = owner._successor['snapshot']['request']
    rows = owner._related(db, RECEIPT_KIND, plan['item_id'])
    events = owner._events(db, RECEIPT_EVENT, plan['item_id'])
    owner._same(rows, [v for _,v in events], 'V3完整回执记录与事件全集不符。')
    command = db.execute('SELECT * FROM commands WHERE id=?', (run['command_id'],)).fetchone()
    live = command is None and pending_request(service, db, run['command_id'], request)
    if not rows:
        if live: return [], [], []
        raise ValueError('V3首次运行缺少原命令的完整回执见证。')
    if len(rows) != 1 or dispatch is None: raise ValueError('V3首次运行须有唯一完整回执及真实派发。')
    task = service.store.task(run['task_id'], db)
    binding = {'id': plan['item_id'], 'revision': dispatch['item_revision'], 'dispatch_id': dispatch['id']}
    receipt = {'task_id': run['task_id'], 'spec_hash': task['spec_hash'], 'launch_attempt': run['attempt_id'],
        'resume_mode': None, 'note': '', 'state': 'RESERVED', 'resource_selection': run['input']['unit']['member']['reference'],
        'unit_run_id': run['id'], 'cooperative': run['cooperative'], 'work_item': binding, 'work_items': [binding]}
    body = body_for(owner, plan, run, driver, receipt); row = rows[0]
    finite(row['created'])
    expected = {'id': RECEIPT_KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
        'item_id': plan['item_id'], 'command_id': run['command_id'], 'created': row['created']}
    event = owner.checkpoints._event(db, RECEIPT_EVENT, row)
    if (row != expected or service.collaboration.get(db, RECEIPT_KIND,row['id']) != row or
            driver['last_unit_run'] != run['id'] or not dispatch['created'] <= driver['updated'] <= row['created'] or
            event['seq'] <= owner._driver_event(db, driver) or
            (not live and (command is None or command['kind'] != 'work-item.unit-send' or
                command['payload_hash'] != digest(request) or json.loads(command['receipt']) != receipt or
                command['created'] < row['created']))):
        raise ValueError('V3原UUID/kind/hash、完整回执或实际绑定语义不符。')
    return [row], [dict(command)] if command is not None else [], [(RECEIPT_KIND,row,event)]


def finish(service, db, kind, request, receipt):
    if kind=='work-item.proposal-check':
        from .work_coordinator_handoff_post_round_v3_capture import finish as finish_proposal
        return finish_proposal(service,db,request,receipt)
    if kind != 'work-item.unit-send': return receipt
    plan = service.teamwork.current(db, request['id'])
    from .work_coordinator_handoff_post_round_v3_public import is_v3
    if not is_v3(plan): return receipt
    from .work_coordinator_handoff_post_round_v3_runtime import WorkCoordinatorHandoffPostRoundV3Runtime
    owner = WorkCoordinatorHandoffPostRoundV3Runtime(service); h = owner.read(db, plan)
    run = h['frames'][-1]['run']
    context=run['input'].get('handoff_coordination_context') or run['input']['handoff_unit_context']
    baseline = context['material_observation']
    def current_material():
        service.work_file_rework_applications.require_baseline(baseline)
        if service.work_file_rework_applications.observe(baseline['root'],baseline['paths']) != baseline:
            raise ValueError('V3首次协调提交末尾当前材料字节已变化；整条命令回滚。')
    current_material()
    run = h['frames'][-1]['run']
    if not pending_request(service, db, run['command_id'], request):
        raise ValueError('V3完整回执结束须属于首运行的准确唯一命令。')
    from . import work_coordinator_handoff_post_round_v3_successor_contracts as first
    if context['protocol']==first.PROTOCOL:
        body=body_for(owner,plan,run,h['driver'],receipt); record_kind=RECEIPT_KIND; record_event=RECEIPT_EVENT
    else:
        from . import work_coordinator_handoff_post_round_v3_continuation_receipts as later
        body=later.body_for(owner,plan,run,h['driver'],owner._continuation,receipt); record_kind=later.KIND; record_event=later.EVENT
    row = {'id': record_kind + ':' + body['version'], 'version': body['version'], 'snapshot': body,
        'item_id': plan['item_id'], 'command_id': service.command_identity, 'created': time.time()}
    service.collaboration.put(db, record_kind, row); service.store.event(None,None,record_event,row,db)
    again = owner.read(db, plan)
    current_material()
    if row not in again['successor_receipts']+again['continuation_receipts']: raise ValueError('V3完整回执末尾见证不符。')
    return receipt
