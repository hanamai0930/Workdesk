"""Internal ordered responsibility prefix for the new complete V3 owner.

The caller rebuilds the original saved edge from actual V8 facts. This function
proves only the original transitions and their committed complete receipts.
The enclosing successor owner closes ALL child namespaces, revisions, runtime
facts and current pointers; this prefix never supplies admission by itself.
"""
from copy import deepcopy
import json

from .store import digest
from .work_read_observation import database_observation
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import ref
from . import work_coordinator_handoff_post_round_v3_responsibility_records as r


def _mentions(value, identities, item):
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if node.get('protocol') == r.TRANSITION and node.get('item_id') == item: return True
            pending.extend(node.values())
        elif isinstance(node, list): pending.extend(node)
        elif isinstance(node, str) and node in identities:
            return True
    return False


@closed
def reconstruct_prefix(service, db, original):
    """Internal subchain proof only; original must come from an actual owner."""
    if not db.in_transaction: raise ValueError('V3责任子链须属于明确事务。')
    item = original['child_plan']['item_id']; plan = original['child_plan']
    raw_rows = list(db.execute('SELECT * FROM collaboration'))
    transitions = []
    for raw in raw_rows:
        if raw['kind'] != r.KIND: continue
        value = json.loads(raw['data'])
        if value.get('item_id') != item and value.get('snapshot', {}).get('item_id') != item: continue
        r.validate_row(value)
        if raw['id'] != value['id']: raise ValueError('V3责任transition数据库主键不符。')
        transitions.append(value)
    raw_events = list(db.execute('SELECT * FROM events ORDER BY seq'))
    def event(kind, payload):
        found = [e for e in raw_events if e['kind'] == kind and json.loads(e['payload']) == payload]
        if len(found) != 1 or found[0]['task_id'] is not None or found[0]['attempt_id'] is not None:
            raise ValueError('V3责任子链须有唯一无任务/尝试归属的准确事件。')
        e = found[0]
        r.finite(e['at'])
        return {'seq': e['seq'], 'kind': kind, 'created': e['at'], 'payload': deepcopy(payload)}
    from . import work_coordinator_handoff_post_round_v3_saved_records as saved
    start = event(saved.EVENTS[saved.REGISTRATION], original['registration'])
    save_command = db.execute('SELECT * FROM commands WHERE id=?', (original['registration']['command_id'],)).fetchone()
    if save_command is None: raise ValueError('V3责任子链缺少原保存命令。')
    # Full original save kind/hash/receipt belongs to the actual saved owner.
    r.finite(save_command['created']); boundary_time = save_command['created']
    if boundary_time < start['created']: raise ValueError('V3责任原保存命令提交顺序不符。')
    events = {v['id']: event(r.EVENT, v) for v in transitions}
    transitions.sort(key=lambda v: events[v['id']]['seq'])
    previous = None; tail = deepcopy(original['driver']); seq = start['seq']
    versions = [tail]; expected_events = []; commands = []; restart_ids = set()
    identities = set()
    for row in transitions:
        r.adjacent(row, original, previous); b = row['snapshot']
        if row['created'] < boundary_time: raise ValueError('V3责任修订早于前一完整提交。')
        de = event(r.DRIVER_EVENTS[b['action']], {**b['driver_after'], 'no_dispatch': True})
        te = events[row['id']]
        if not seq < de['seq'] < te['seq'] or not row['created'] <= de['created'] <= te['created']:
            raise ValueError('V3责任修订、完整事件顺序或时间不符。')
        if row['command_id'] is None:
            if b['restart_id'] in restart_ids: raise ValueError('V3重启身份不得复用。')
            restart_ids.add(b['restart_id']); boundary_time = te['created']
        else:
            command = db.execute('SELECT * FROM commands WHERE id=?', (row['command_id'],)).fetchone()
            if command is None:
                raise ValueError('V3后继原责任前缀必须已经完整提交。')
            elif (command['kind'] != r.COMMAND or
                    command['payload_hash'] != digest(b['request']) or
                    json.loads(command['receipt']) != r.receipt(row, original['registration'])):
                raise ValueError('V3责任缺少准确已提交命令和完整回执。')
            else:
                r.finite(command['created'])
                if command['created'] < te['created']: raise ValueError('V3责任命令早于完整transition事件。')
                boundary_time = command['created']; commands.append(dict(command))
        versions.append(b['driver_after']); tail = b['driver_after']; previous = row; seq = te['seq']
        expected_events += [de, te]
        identities.update((row['id'], row['version'], tail['revision']))
    return {'transitions': deepcopy(transitions), 'versions': deepcopy(versions),
        'driver': deepcopy(tail), 'events': expected_events, 'commands': commands,
        'boundary_seq': seq, 'boundary_time': boundary_time}
