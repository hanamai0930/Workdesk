"""Exact in-transaction admission and durable full receipt witnesses."""
from copy import deepcopy
import json
import time

from .store import digest
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import versioned

RECEIPT_KIND = 'work_coordinator_handoff_post_round_successor_receipt_v2'
RECEIPT_PROTOCOL = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_RECEIPT_V2'
RECEIPT_EVENT = 'coordinator_post_round_successor_receipt_saved_v2'
from .work_coordinator_handoff_post_round_v2_command_scope import command, pending, pending_request


def finish(service, db, kind, request, receipt):
    if kind != 'work-item.unit-send': return receipt
    plan = service.teamwork.current(db, request['id'])
    if not plan or plan.get('coordinator_file_handoff_post_round_lineage', {}).get('protocol') != 'COORDINATOR_FILE_HANDOFF_POST_ROUND_ADJUSTMENT_LINEAGE_V2': return receipt
    from .work_coordinator_handoff_post_round_v2_runtime import WorkCoordinatorHandoffPostRoundV2Runtime
    runtime = WorkCoordinatorHandoffPostRoundV2Runtime(service)
    h = runtime.read(db, plan); cap = runtime._successor
    run = next(f['run'] for f in h['frames'] if f['run']['id'] == receipt['unit_run_id'])
    if (run['command_id'] != service.command_identity or run['attempt_id'] != receipt['launch_attempt'] or
            h['driver']['last_unit_run'] != run['id'] or
            receipt['cooperative'] != run['cooperative']):
        raise ValueError('后继完整回执缺少准确首次保留和责任绑定。')
    body = versioned({'protocol': RECEIPT_PROTOCOL, 'item_id': plan['item_id'], 'plan': plan_ref(plan),
        'capability': runtime.ref(cap), 'run': deepcopy(run), 'driver': h['driver'],
        'request': deepcopy(request), 'receipt': deepcopy(receipt)})
    row = {'id': RECEIPT_KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
        'item_id': plan['item_id'], 'command_id': service.command_identity, 'created': time.time()}
    service.collaboration.put(db, RECEIPT_KIND, row)
    service.store.event(None, None, RECEIPT_EVENT, row, db)
    runtime.read(db, plan)
    return receipt


def require_receipt(runtime, db, plan, frames):
    service = runtime.s; cap = runtime._successor
    rows = runtime._related(db, RECEIPT_KIND, plan['item_id'])
    events = runtime._events(db, RECEIPT_EVENT, plan['item_id'])
    runtime._same(rows, [v for _, v in events], '后继完整回执记录与实际事件全集不符。')
    new = [f for f in frames if f['run']['plan_id'] == plan['id']]
    if not new:
        if not rows and pending(service, db, cap): return
        raise ValueError('后继能力缺少同命令首保留或出现孤立回执。')
    expected_rows = []
    for frame in new:
        run = frame['run']; found = [r for r in rows if r['command_id'] == run['command_id']]
        prior = [runtime.sources._frame_before(db, f['run'], run) for f in frames
            if runtime.sources._reservation_event(db, f['run']) < runtime.sources._reservation_event(db, run)]
        request = {'id': plan['item_id'], 'revision': run['item_revision'],
            'current_driver': {'id': run['driver_id'], 'generation': run['generation'], 'revision': run['driver_revision']},
            'plan': plan_ref(plan), 'authorization': run['authorization'],
            'frontier': digest([{'run_id': f['run']['id'], 'evidence': f['evidence']['version']} for f in prior]),
            'unit': {'key': run['unit_key'], 'revision': run['unit_revision']}, 'retry_of': None, 'proposal': run.get('proposal')}
        command_row = db.execute('SELECT * FROM commands WHERE id=?', (run['command_id'],)).fetchone()
        live = command_row is None and pending_request(service, db, run['command_id'], request)
        if not found:
            if live: continue
            raise ValueError('后继每个真实运行须有完整同命令回执。')
        if len(found) != 1: raise ValueError('后继运行完整回执不唯一。')
        row = found[0]; body = row['snapshot']; expected_rows.append(row)
        if service.collaboration.get(db, RECEIPT_KIND, row['id']) != row:
            raise ValueError('后继完整回执数据库键与真实记录不符。')
        driver = next((v for _, v, _ in runtime._versions if v.get('last_unit_run') == run['id'] and
            v['reason'] == 'OWNED_EXECUTION_LINKED'), None)
        expected = versioned({'protocol': RECEIPT_PROTOCOL, 'item_id': plan['item_id'], 'plan': plan_ref(plan),
            'capability': runtime.ref(cap), 'run': run, 'driver': driver,
            'request': request, 'receipt': body['receipt']})
        event = runtime.checkpoints._event(db, RECEIPT_EVENT, row)
        if (set(row) != {'id','version','snapshot','item_id','command_id','created'} or body != expected or
                row['id'] != RECEIPT_KIND + ':' + body['version'] or row['version'] != digest({k:v for k,v in body.items() if k != 'version'}) or
                row['item_id'] != plan['item_id'] or row['command_id'] != run['command_id'] or driver is None or
                event['seq'] <= runtime._driver_event(db, driver) or row['created'] < driver['updated'] or
                body['receipt'].get('unit_run_id') != run['id'] or body['receipt'].get('launch_attempt') != run['attempt_id'] or
                body['receipt'].get('cooperative') != run['cooperative'] or
                (not live and (command_row is None or command_row['kind'] != 'work-item.unit-send' or
                    command_row['payload_hash'] != digest(request) or
                    json.loads(command_row['receipt']) != body['receipt'] or command_row['created'] < row['created']))):
            raise ValueError('后继已提交命令种类、摘要、完整回执或真实绑定见证不符。')
    runtime._same(rows, expected_rows, '后继回执包含未重建运行或孤立记录。')
