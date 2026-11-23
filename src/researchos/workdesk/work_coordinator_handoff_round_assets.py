"""Backup closure for ROUND data and frozen inputs, without runtime authority."""
import json

from .store import digest
from .work_coordinator_handoff_records import fields, reference, versioned, PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_round_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, KIND_FOR, KINDS, validate_row)
from .work_coordinator_handoff_round_contracts import prepared, successor_plan
from .work_coordinator_handoff_round_runtime import (
    CAPABILITY, KIND, PROTOCOL, UNIT, SCOPE, validate_path, validate_context)


def get(db, kind, ref):
    reference(ref)
    row = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (ref['id'],)).fetchone()
    if row is None or row[0] != kind: raise ValueError('ROUND资产缺少准确实际引用。')
    value = json.loads(row[1])
    if {k: value[k] for k in ('id', 'version')} != ref: raise ValueError('ROUND资产引用版本不符。')
    if kind == 'teamwork_plan':
        from .work_coordinator_handoff_adjustment_records import hashed
        hashed(value)
    if kind in KINDS: validate_row(kind, value)
    return value


@closed
def require_path_assets(db, path):
    validate_path(path); edge = path['edge']
    root = get(db, 'teamwork_plan', path['root']); plan = get(db, 'teamwork_plan', path['plan']); successor_plan(plan)
    parent = get(db, 'teamwork_plan', edge['parent_plan'])
    cp = get(db, KIND_FOR[CHECKPOINT], edge['checkpoint']); candidate = get(db, KIND_FOR[CANDIDATE], edge['candidate'])
    app = get(db, KIND_FOR[APPLICATION], edge['application']); reg = get(db, KIND_FOR[REGISTRATION], edge['registration'])
    obs = cp['snapshot']['observation']
    if (prepared(cp, candidate) != plan or cp['snapshot']['plan'] != parent or
            obs['root'] != path['root'] or obs['parent_kind'] != path['parent_kind'] or obs['saved_path'] != path['parent_path'] or
            root['coordinator_file_handoff_root'] != plan['coordinator_file_handoff_root'] or
            path['runtime_capability'] != obs['history']['capability'] or
            path['adjustment_capability'] != obs['history']['adjustment_capability'] or
            path['source'] != obs['source'] or path['prior_check'] != obs['prior_check'] or
            any(r['snapshot']['candidate'] != edge['candidate'] for r in (app, reg)) or
            app['snapshot']['checkpoint'] != edge['checkpoint'] or app['snapshot']['child_plan'] != path['plan'] or
            app['snapshot']['save_event'] != edge['save_event'] or reg['snapshot']['application'] != edge['application'] or
            reg['snapshot']['from_plan'] != edge['parent_plan'] or reg['snapshot']['to_plan'] != path['plan'] or
            app['snapshot']['authorization_after'] != {k: path['authorization'][k] for k in ('id', 'revision')} or
            app['snapshot']['driver_after'] != {k: path['driver_history'][0]['driver'][k] for k in ('id', 'generation', 'revision')}):
        raise ValueError('ROUND资产须闭合实际检查点、候选、相邻方案、应用与登记。')
    for event in [edge['save_event'], edge['registration_event'], *[v['event'] for v in path['driver_history']]]:
        row = db.execute('SELECT kind,payload FROM events WHERE seq=?', (event['seq'],)).fetchone()
        if row is None or row[0] != event['kind'] or digest(json.loads(row[1])) != event['payload_digest']:
            raise ValueError('ROUND资产事件引用不是准确实际记录。')
    for entry in path['driver_history']:
        v = entry['driver']; raw = db.execute('SELECT kind,data FROM collaboration WHERE id=?', ('driver-version:' + v['revision'],)).fetchone()
        stored = json.loads(raw[1]) if raw else None
        if (not raw or raw[0] != 'work_driver_revision' or
                stored != {**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']}):
            raise ValueError('ROUND资产缺少完整不可变责任版本。')
    auth = path['authorization']; raw = db.execute('SELECT kind,data FROM collaboration WHERE id=?', ('unit-auth-version:' + auth['revision'],)).fetchone()
    if not raw or raw[0] != 'work_unit_authorization_revision' or json.loads(raw[1]) != {**auth, 'id': 'unit-auth-version:' + auth['revision']}:
        raise ValueError('ROUND资产缺少完整原转移授权。')
    for kind, ref in (('work_coordinator_handoff_capability', path['runtime_capability']),
            ('work_coordinator_handoff_adjustment_capability', path['adjustment_capability'])):
        if ref is not None: get(db, kind, ref)
    if path['round_capability'] is not None:
        cap = get(db, KIND, path['round_capability']); validate_capability_assets(db, cap)
        if cap['snapshot']['registration'] != edge['registration']: raise ValueError('ROUND输入能力属于其它登记。')
    return path


@closed
def validate_capability_assets(db, row):
    fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
    body = row['snapshot']
    fields(body, {'protocol', 'root', 'plan', 'runtime_capability', 'adjustment_capability',
        'registration', 'registration_event', 'source', 'prior_check', 'activation_frontier',
        'driver', 'authorization', 'coordination_protocol', 'unit_protocol', 'allowed_actions', 'scope', 'version', *PERMISSIONS})
    from .work_coordinator_handoff_adjustment_records import finite
    finite(row['created'])
    if (body['protocol'] != CAPABILITY or body['coordination_protocol'] != PROTOCOL or body['unit_protocol'] != UNIT or
            body['allowed_actions'] != ['dispatch', 'pause'] or body['scope'] != SCOPE or
            any(body[k] is not v for k, v in PERMISSIONS.items()) or
            versioned({k: v for k, v in body.items() if k != 'version'}) != body or
            row['version'] != body['version'] or row['id'] != KIND + ':' + body['version'] or
            not isinstance(row['command_id'], str) or not row['command_id']):
        raise ValueError('ROUND能力资产闭合协议或摘要不符。')
    reg = get(db, KIND_FOR[REGISTRATION], body['registration'])
    candidate = get(db, KIND_FOR[CANDIDATE], reg['snapshot']['candidate'])
    cp = get(db, KIND_FOR[CHECKPOINT], candidate['snapshot']['checkpoint']); obs = cp['snapshot']['observation']
    if (body['root'] != obs['root'] or body['plan'] != reg['snapshot']['to_plan'] or
            body['runtime_capability'] != obs['history']['capability'] or body['adjustment_capability'] != obs['history']['adjustment_capability'] or
            body['source'] != obs['source'] or body['prior_check'] != obs['prior_check'] or
            body['activation_frontier'] != obs['history']['frontier'] or row['item_id'] != reg['item_id']):
        raise ValueError('ROUND能力资产须绑定原完整前沿与准确登记来源。')
    return row


@closed
def required_records(db, file_baseline):
    rows = []
    for kind in KINDS:
        rows.extend(validate_row(kind, json.loads(r[0])) for r in db.execute('SELECT data FROM collaboration WHERE kind=?', (kind,)))
    for row in rows:
        b = row['snapshot']; protocol = b['protocol']
        if protocol == CHECKPOINT: file_baseline(b['observation']['history']['material_observation'])
        elif protocol == CANDIDATE:
            cp = get(db, KIND_FOR[CHECKPOINT], b['checkpoint']); prepared(cp, row)
        elif protocol == APPLICATION:
            candidate = get(db, KIND_FOR[CANDIDATE], b['candidate'])
            cp = get(db, KIND_FOR[CHECKPOINT], b['checkpoint'])
            if prepared(cp, candidate) != get(db, 'teamwork_plan', b['child_plan']):
                raise ValueError('ROUND应用资产子方案不符。')
        else:
            app = get(db, KIND_FOR[APPLICATION], b['application'])
            if app['snapshot']['candidate'] != b['candidate'] or app['snapshot']['child_plan'] != b['to_plan'] or app['snapshot']['parent_plan'] != b['from_plan']:
                raise ValueError('ROUND登记资产相邻方案或应用不符。')
    for row in db.execute('SELECT data FROM collaboration WHERE kind=?', (KIND,)): validate_capability_assets(db, json.loads(row[0]))
    # A V2 parent checkpoint owns material before a successor plan exists.
    from .work_coordinator_handoff_round_parent_checkpoint import required_assets
    required_assets(db, file_baseline)


def require_context_assets(db, context):
    validate_context(context); require_path_assets(db, context['saved_path'])
