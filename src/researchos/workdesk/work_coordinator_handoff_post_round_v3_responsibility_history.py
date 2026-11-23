"""Read-only committed V3 responsibility subchain, not full-current admission.

The internal caller must independently reconstruct the original saved edge and
actual complete current ancestry. This module never accepts a protocol flag as
that proof and provides no write handler, carrier permission or runtime input.
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
def _reconstruct(service, db, original, _application=None):
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
                from .work_coordinator_handoff_post_round_v3_responsibility_apply import pending
                if row != transitions[-1] or not pending(service, db, _application, row, original['registration']):
                    raise ValueError('V3责任缺少准确已提交命令或唯一当前拥有者。')
                # An owning witness is not a committed command. Do not add a
                # synthetic command/receipt to the reconstructed command ledger.
                boundary_time = te['created']
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
    revisions = [{**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']} for v in versions]
    relevant_revisions = [json.loads(v['data']) for v in raw_rows if v['kind'] == 'work_driver_revision'
                          and json.loads(v['data']).get('plan_id') == plan['id']]
    same = lambda a, b: sorted(map(digest, a)) == sorted(map(digest, b))
    if not same(relevant_revisions, revisions): raise ValueError('V3责任子链完整revision集合不符。')
    expected_rows = {(r.KIND, v['id']): v for v in transitions}
    expected_rows.update({('work_driver_revision', v['id']): v for v in revisions[1:]})
    if transitions: expected_rows[('work_driver', tail['id'])] = tail
    for raw in raw_rows:
        value = json.loads(raw['data'])
        if _mentions(value, identities, item) and expected_rows.get((raw['kind'], raw['id'])) != value:
            raise ValueError('V3责任存在嵌套、改名、别名或未重建载体。')
        if raw['kind'] == 'work_driver_revision' and value in revisions and raw['id'] != value['id']:
            raise ValueError('V3责任revision数据库主键不符。')
    expected_event_map = {(e['kind'], e['seq']): e['payload'] for e in expected_events}
    child_driver_events = []
    for raw in raw_events:
        value = json.loads(raw['payload'])
        if _mentions(value, identities, item) or (raw['kind'] == r.EVENT and value.get('item_id') == item):
            if (expected_event_map.get((raw['kind'], raw['seq'])) != value or
                    raw['task_id'] is not None or raw['attempt_id'] is not None):
                raise ValueError('V3责任存在孤立、嵌套、迟到或错归属事件。')
        if raw['kind'].startswith('work_driver_') and value.get('plan_id') == plan['id']:
            child_driver_events.append(value)
        if ((raw['kind'] == 'cooperative_unit_reserved') and
                (value.get('plan_id') == plan['id'] or value.get('cooperative', {}).get('plan_id') == plan['id'])):
            raise ValueError('V3责任子链尚未开放子方案reservation。')
    if not same(child_driver_events, [{**v, 'no_dispatch': True} for v in versions]):
        raise ValueError('V3责任子链driver事件全集不符。')
    expected_commands = {v['id']: v for v in commands}
    for raw in db.execute('SELECT * FROM commands'):
        value = json.loads(raw['receipt'])
        if _mentions(value, identities, item) and expected_commands.get(raw['id']) != dict(raw):
            raise ValueError('V3责任存在未重建或别名命令回执。')
    drivers = [json.loads(v['data']) for v in raw_rows if v['kind'] == 'work_driver'
               and json.loads(v['data']).get('item_id') == item]
    if drivers != [tail] or service.work_drivers.current(db, item) != tail:
        raise ValueError('V3责任当前指针未准确指向子链尾。')
    auth = original['authorization']
    expected_auth_revision = {**auth, 'id': 'unit-auth-version:' + auth['revision']}
    child_auth = [(v['kind'], v['id'], json.loads(v['data'])) for v in raw_rows
        if v['kind'] in {'work_unit_authorization', 'work_unit_authorization_revision'}
        and json.loads(v['data']).get('plan_id') == plan['id']]
    if sorted(child_auth, key=lambda v: v[:2]) != sorted([
            ('work_unit_authorization', auth['id'], auth),
            ('work_unit_authorization_revision', expected_auth_revision['id'], expected_auth_revision)], key=lambda v: v[:2]):
        raise ValueError('V3责任原完整authorization集合不符。')
    if any(v.get('plan_id') == plan['id'] for v in service.collaboration.all(db, 'work_unit_run')):
        raise ValueError('V3责任子链尚未开放子方案运行。')
    return {'transitions': deepcopy(transitions), 'versions': deepcopy(versions), 'driver': deepcopy(tail),
        'events': expected_events, 'commands': commands, 'subchain_verified': True,
        'full_current_tree_verified': False, 'successor_runtime_open': False, **r.PERMISSIONS}


@closed
def read_subchain(service, db, original, _application=None):
    """Two fresh subchain reconstructions. Never a full-current owner API."""
    with database_observation(db):
        changes = db.total_changes
        first = _reconstruct(service, db, original, _application)
        again = _reconstruct(service, db, original, _application)
        if first != again or changes != db.total_changes:
            raise ValueError('V3责任子链双遍或数据库末尾变化。')
        return deepcopy(again)
