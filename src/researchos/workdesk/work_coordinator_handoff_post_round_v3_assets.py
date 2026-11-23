"""Read-only typed V3 backup closure. This is not a current-tree proof."""
import json
import sqlite3
from types import SimpleNamespace

from .store import digest
from .work_coordinator_handoff_records import reference
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_post_round_v3_saved_component import WorkCoordinatorHandoffPostRoundV3SavedComponent
from . import work_coordinator_handoff_post_round_v3_saved_records as r


class _Rows:
    """Keyed cursors without changing the caller's connection row factory."""
    def __init__(self, db): self.db = db

    def execute(self, query, args=()):
        cursor = self.db.cursor(); cursor.row_factory = sqlite3.Row
        return cursor.execute(query, args)


class _Catalog:
    def get(self, db, kind, identity):
        row = db.execute('SELECT kind,id,data FROM collaboration WHERE id=?', (identity,)).fetchone()
        if row is None or row['kind'] != kind:
            raise ValueError('V3资产缺少准确类型及数据库主键。')
        value = json.loads(row['data'])
        if value.get('id') != row['id']: raise ValueError('V3资产数据库键与内容身份不符。')
        return value

    def all(self, db, kind):
        return [self.get(db, kind, row[0]) for row in db.execute('SELECT id FROM collaboration WHERE kind=?', (kind,))]


def _service():
    # Only these data methods are exposed. No Service construction, startup,
    # resource selection, current pointer, dispatch, or native access occurs.
    return SimpleNamespace(collaboration=_Catalog(),
        work_drivers=SimpleNamespace(reference=lambda v: {k: v[k] for k in ('id', 'generation', 'revision')}),
        work_units=SimpleNamespace(auth_ref=lambda v: {k: v[k] for k in ('id', 'revision')}))


def _get(service, db, kind, identity):
    reference(identity)
    value = service.collaboration.get(db, kind, identity['id'])
    if ref(value) != identity: raise ValueError('V3资产准确版本引用不符。')
    return value


def _event(db, identity, payload=None, task=None, attempt=None):
    row = db.execute('SELECT * FROM events WHERE seq=?', (identity['seq'],)).fetchone()
    if (row is None or row['kind'] != identity['kind'] or
            digest(json.loads(row['payload'])) != identity['payload_digest'] or
            row['task_id'] != task or row['attempt_id'] != attempt or
            (payload is not None and json.loads(row['payload']) != payload)):
        raise ValueError('V3资产事件须保持准确序号、载荷和独立任务归属。')


def _baselines(value, file_baseline):
    pending = [value]; seen = set()
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if {'root', 'paths', 'manifest', 'missing', 'version'} <= node.keys():
                identity = digest(node)
                if identity not in seen: file_baseline(node); seen.add(identity)
            pending.extend(node.values())
        elif isinstance(node, list): pending.extend(node)


def _ancestry(service, db, path):
    from . import work_coordinator_handoff_adjustment_records as adjustment
    from . import work_coordinator_handoff_round_records as first
    from . import work_coordinator_handoff_mixed_records as mixed
    from . import work_coordinator_handoff_post_round_saved_records as post
    from . import work_coordinator_handoff_post_round_v2_saved_records as v2
    modules = {'ADJUSTMENT': adjustment, 'FIRST_ROUND': first, 'MIXED_ROUND': mixed,
        'POST_V1': post, 'POST_V2': v2, 'POST_V3': r}
    for entry in path['ancestors']:
        plan = _get(service, db, 'teamwork_plan', entry['plan'])
        if digest({k: v for k, v in plan.items() if k != 'version'}) != plan['version']:
            raise ValueError('V3资产祖先完整方案摘要不符。')
        if entry['kind'] == 'ROOT': continue
        module = modules[entry['kind']]; edge = entry['edge']
        records = {}
        final_key = 'consumption' if module is adjustment else 'registration'
        final_protocol = module.CONSUMPTION if module is adjustment else module.REGISTRATION
        for key, protocol in (('checkpoint', module.CHECKPOINT), ('candidate', module.CANDIDATE),
                ('application', module.APPLICATION), (final_key, final_protocol)):
            row = _get(service, db, module.KIND_FOR[protocol], edge[key])
            if module in (post, v2, r): module.validate_row(row, protocol)
            else: module.validate_row(module.KIND_FOR[protocol], row)
            records[key] = row
        if module in (post, v2, r):
            if records['candidate']['snapshot']['child_plan'] != plan:
                raise ValueError('V3祖先资产child不等于实际准确候选。')
        elif module in (first, mixed):
            if module is first:
                from .work_coordinator_handoff_round_contracts import prepared
            else:
                from .work_coordinator_handoff_mixed_contracts import prepared
            if prepared(records['checkpoint'], records['candidate']) != plan:
                raise ValueError('V3祖先ROUND方案不等于实际准备重建。')
        if records['application']['snapshot']['child_plan'] != entry['plan']:
            raise ValueError('V3祖先应用的实际child引用不符。')
        for key in ('save_event',) if module is adjustment else ('save_event', 'registration_event'):
            _event(db, edge[key])
        if module in (post, v2, r):
            cp, candidate, app, reg = [records[k] for k in ('checkpoint', 'candidate', 'application', 'registration')]
            h = cp['snapshot']['history']; b = app['snapshot']; context = WorkCoordinatorHandoffContext(service)
            driver = context._driver(db, plan, b['driver_after'])
            auth = context._auth(db, plan, b['authorization_after'])
            module.adjacent(cp, candidate, plan, h['driver'], driver, h['authorization'], auth)
            args = (cp, candidate, plan, h['driver'], driver, h['authorization'], auth, edge['save_event'], b['request'])
            bodies = module.bodies(*args) if module is r else module.bodies(service, *args)
            if bodies != (app['snapshot'], reg['snapshot']) or any(v['command_id'] != candidate['command_id'] for v in (cp, app, reg)):
                raise ValueError('V3祖先保存四元组未按原请求准确重建。')
            from .work_coordinator_handoff_mixed_assets import actual_event
            for protocol, value in ((module.CHECKPOINT, cp), (module.CANDIDATE, candidate),
                    (module.APPLICATION, app), (module.REGISTRATION, reg)):
                actual_event(db, module.EVENTS[protocol], value)
            expected = {k: v for k, v in (('application', app), ('registration', reg),
                ('child_plan', plan), ('driver', driver), ('authorization', auth))}
            expected.update(no_dispatch=True, automatically_resumed=False)
            command = db.execute('SELECT * FROM commands WHERE id=?', (candidate['command_id'],)).fetchone()
            kind = 'internal.post-round.save' if module is post else 'internal.post-round.v2.save' if module is v2 else 'internal.post-round.v3.save'
            if (command is None or command['kind'] != kind or command['payload_hash'] != digest(b['request']) or
                    json.loads(command['receipt']) != expected or command['created'] < reg['created']):
                raise ValueError('V3祖先资产缺少原保存命令及完整回执。')


def required_records(db, file_baseline, *, catalog=None):
    """Rebuild the original paused saved data; return no permission flags."""
    return _required_records(db, file_baseline, catalog)


@closed
def _required_records(db, file_baseline, catalog):
    if catalog is not None: catalog.require_snapshot(db)
    db = _Rows(db); service = _service()
    owner = WorkCoordinatorHandoffPostRoundV3SavedComponent(service)
    rows = {p: service.collaboration.all(db, kind) for p, kind in r.KIND_FOR.items()}
    for protocol, values in rows.items():
        for row in values: r.validate_row(row, protocol)
        events = list(db.execute('SELECT * FROM events WHERE kind=?', (r.EVENTS[protocol],)))
        if (sorted(map(digest, values)) != sorted(digest(json.loads(e['payload'])) for e in events) or
                any(e['task_id'] is not None or e['attempt_id'] is not None for e in events)):
            raise ValueError('V3资产记录与实际事件全集不闭合。')
    used = {p: [] for p in r.KIND_FOR}; canonical = {}; canonical_events = {}
    for candidate in rows[r.CANDIDATE]:
        if catalog is not None and candidate['snapshot']['child_plan']['id'] in catalog.originals:
            # The new owner has rebuilt these exact saved carriers and every
            # later running carrier under the same real read snapshot.
            original = catalog.originals[candidate['snapshot']['child_plan']['id']]
            cp = _get(service, db, r.KIND_FOR[r.CHECKPOINT], candidate['snapshot']['checkpoint'])
            for protocol, row in ((r.CHECKPOINT, cp), (r.CANDIDATE, candidate),
                    (r.APPLICATION, original['application']), (r.REGISTRATION, original['registration'])):
                actual = catalog.ledger.rows.get((r.KIND_FOR[protocol], row['id']))
                if actual is None or actual[0] != row:
                    raise ValueError('V3运行路由须消费本次已独立重建的准确保存载体。')
                used[protocol].append(row)
            _baselines(cp, file_baseline)
            continue
        cp = _get(service, db, r.KIND_FOR[r.CHECKPOINT], candidate['snapshot']['checkpoint'])
        b = cp['snapshot']; h = b['history']; parent = _get(service, db, 'teamwork_plan', b['parent_plan'])
        proposal = _get(service, db, 'work_proposal', b['proposal'])
        if parent != b['plan'] or proposal['item_id'] != cp['item_id']:
            raise ValueError('V3资产须保留原完整父及准确提案。')
        source = proposal['snapshot']['source']
        _event(db, b['source_check_event'], proposal, source['task_id'], source['attempt_id'])
        context = WorkCoordinatorHandoffContext(service)
        if (context._driver(db, parent, b['request']['current_driver']) != h['driver'] or
                context._auth(db, parent, b['request']['authorization']) != h['authorization']):
            raise ValueError('V3资产原责任及授权不等于实际历史版本。')
        for frame in h['frontier']:
            run = service.collaboration.get(db, 'work_unit_run', frame['run_id'])
            task = db.execute('SELECT spec FROM tasks WHERE id=?', (run['task_id'],)).fetchone()
            if (run['item_id'] != cp['item_id'] or frame['evidence']['run_id'] != run['id'] or
                    frame['evidence']['task_id'] != run['task_id'] or frame['evidence']['attempt_id'] != run['attempt_id'] or
                    digest(run) != frame['run_version'] or run['input_version'] != frame['input_version'] or
                    digest(run['input']) != run['input_version'] or task is None or
                    digest(json.loads(task['spec'])) != frame['task_spec_version']):
                raise ValueError('V3资产历史前沿不等于实际原运行。')
            for saved in frame['files']['snapshots']:
                artifact = _get(service, db, 'work_unit_artifact', saved['reference'])
                if artifact['snapshot'] != saved['snapshot'] or digest(artifact['snapshot']) != artifact['version']:
                    raise ValueError('V3资产冻结快照不等于实际完整存储值。')
        facts = {'plan': parent, 'proposal': proposal, 'history': h,
            'preview': proposal['snapshot']['adjustment'], 'source_check_event': b['source_check_event']}
        # Canonical envelopes are reconstructed from actual referenced records.
        # This local graph check deliberately does not attest all parent facts.
        if (b != owner._checkpoint(facts, b['request'], b['cutoff']) or
                candidate['snapshot'] != owner._candidate(facts, cp, candidate['snapshot']['child_seed'])):
            raise ValueError('V3资产准备值不等于原请求和准确来源重建。')
        value = owner._namespace(db, facts, cp, candidate)
        for kind, row, event in owner._carriers(cp, candidate, value):
            canonical[(kind, row['id'])] = row
            if event is not None: canonical_events[(event['kind'], event['seq'])] = row
        for key, payload in (('driver_event', {**value['driver'], 'no_dispatch': True}),
                ('save_event', r.save_payload(candidate, value['child_plan'], value['driver'], value['authorization']))):
            event = value['events'][key]; canonical_events[(event['kind'], event['seq'])] = payload
        _ancestry(service, db, value['saved_path'])
        _baselines(b, file_baseline)
        for protocol, row in ((r.CHECKPOINT, cp), (r.CANDIDATE, candidate),
                (r.APPLICATION, value['application']), (r.REGISTRATION, value['registration'])):
            used[protocol].append(row)
    if any(sorted(map(digest, used[p])) != sorted(map(digest, rows[p])) for p in rows):
        raise ValueError('V3资产须形成完整唯一保存四元组，不能遗漏孤立准备或保存记录。')
    # Catch renamed or nested carriers even when no canonical V3 candidate
    # exists. Correct wrappers never authorize a hidden typed record.
    from .work_coordinator_handoff_post_round_v3_path import PATH
    from .work_coordinator_handoff_post_round_v3_candidates import LINEAGE
    protocols = {*r.KIND_FOR, PATH, LINEAGE}
    def marked(value):
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if node.get('protocol') in protocols: return True
                identity = node.get('id')
                if isinstance(identity, str) and any(identity.startswith(k + ':') for k in r.KIND_FOR.values()): return True
                pending.extend(node.values())
            elif isinstance(node, list): pending.extend(node)
        return False
    for row in db.execute('SELECT * FROM collaboration'):
        value = json.loads(row['data'])
        current = catalog.ledger.rows.get((row['kind'], row['id'])) if catalog is not None else None
        if marked(value) and canonical.get((row['kind'], row['id'])) != value and (current is None or current[0] != value):
            raise ValueError('V3资产包含改名或嵌套载体。')
    for row in db.execute('SELECT * FROM events'):
        value = json.loads(row['payload'])
        current = catalog.ledger.events.get(row['seq']) if catalog is not None else None
        running = current is not None and current[0] == (row['kind'], value, row['task_id'], row['attempt_id'])
        if marked(value) and not running and (canonical_events.get((row['kind'], row['seq'])) != value or
                row['task_id'] is not None or row['attempt_id'] is not None):
            raise ValueError('V3资产事件包含改名、嵌套或错误归属。')


@closed
def require_successor_context(db, context):
    """Existing V7/V8 frozen formats only; no new successor input format."""
    db = _Rows(db); service = _service()
    from . import work_coordinator_handoff_post_round_successor_contracts as v1
    from . import work_coordinator_handoff_post_round_v2_run_contracts as v2
    module = v2 if context['protocol'] in (v2.PROTOCOL, v2.UNIT) else v1
    cap = _get(service, db, module.KIND, context['successor_capability'])
    module.validate_context(context, cap)
    from .work_coordinator_handoff_mixed_assets import actual_event
    actual_event(db, module.OPENED, cap)
    command = db.execute('SELECT * FROM commands WHERE id=?', (cap['command_id'],)).fetchone()
    runs = [v for v in service.collaboration.all(db, 'work_unit_run') if v['command_id'] == cap['command_id']]
    if command is None or command['kind'] != 'work-item.unit-send' or len(runs) != 1:
        raise ValueError('后继资产缺少同命令准确首运行。')
    receipt = json.loads(command['receipt']); run = runs[0]
    if (receipt.get('unit_run_id') != run['id'] or receipt.get('launch_attempt') != run['attempt_id'] or
            receipt.get('cooperative') != run['cooperative'] or command['created'] < cap['created']):
        raise ValueError('后继资产首运行与原命令完整回执不符。')
    if module is v2:
        if command['payload_hash'] != cap['snapshot']['request_hash']:
            raise ValueError('V8资产开启原请求摘要不符。')
        from .work_coordinator_handoff_post_round_v2_commands import RECEIPT_KIND, RECEIPT_EVENT
        witnesses = [v for v in service.collaboration.all(db, RECEIPT_KIND) if v['command_id'] == cap['command_id']]
        if (len(witnesses) != 1 or witnesses[0]['snapshot']['receipt'] != receipt or
                witnesses[0]['snapshot']['request'] != cap['snapshot']['request'] or witnesses[0]['snapshot']['run'] != run or
                digest({k: v for k, v in witnesses[0]['snapshot'].items() if k != 'version'}) != witnesses[0]['version']):
            raise ValueError('V8资产缺少准确原请求及完整回执见证。')
        actual_event(db, RECEIPT_EVENT, witnesses[0])
