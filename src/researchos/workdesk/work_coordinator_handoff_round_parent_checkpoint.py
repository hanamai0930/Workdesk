"""Independent V2 ROUND-parent observation and real event-cutoff checkpoint.

No successor plan, candidate, application, input or execution is authorized.
The immutable checkpoint reconstructs the executed first ROUND, and remains
strictly gated by its entire live namespace until the mixed reader is present.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import shutil
import time

from .store import digest
from .work_read_observation import database_observation
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_records import (
    PERMISSIONS, fields, reference, text, sha, task_revision, observation, versioned, DELIVERY_KIND, COMPLETION_KIND,
    validate_row as delivery_row)
from .work_coordinator_handoff_adjustment_records import (
    hashed, permissions, source, carry, keys, usage, finite, complete_frontier, units)
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_round_records import (
    PARENT_FIELDS, HISTORY, selected, prior_check, event_ref)
from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources
from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
from .work_coordinator_handoff_round_contracts import successor_plan
from .work_coordinator_handoff_round_runtime import validate_path
from .work_coordinator_handoff_round_parent_history import WorkCoordinatorHandoffRoundParentHistory

PARENT = 'COORDINATOR_FILE_HANDOFF_ROUND_PARENT_OBSERVATION_V2'
CHECKPOINT = 'COORDINATOR_FILE_HANDOFF_ROUND_PARENT_CHECKPOINT_V2'
KIND = 'work_coordinator_handoff_round_parent_checkpoint'
EVENT = 'coordinator_round_parent_checkpointed'
SCOPE = 'FIRST_EXECUTED_ROUND_PARENT_HISTORY; NO_SUCCESSOR_SAVE_OR_INPUT_PERMISSION'
HISTORY_FIELDS = HISTORY | {'round_capability'}


@closed
def validate_parent(value):
    fields(value, PARENT_FIELDS); hashed(value); permissions(value)
    if value['protocol'] != PARENT or value['scope'] != SCOPE or value['parent_kind'] != 'ROUND':
        raise ValueError('V2父观察仅为真实已运行首ROUND的独立历史协议。')
    for k in ('root', 'plan'): reference(value[k])
    text(value['item_id']); source(value['source'], full=True); selected(value['selected_unit'])
    prior_check(value['prior_check']); carry(value['carry_sources']); keys(value['historical_coordinators'])
    usage(value['usage']); finite(value['original_dispatch_before'])
    path = validate_path(value['saved_path']); h = value['history']; fields(h, HISTORY_FIELDS); permissions(h)
    if h['protocol'] != 'COORDINATOR_FILE_HANDOFF_STAGE_HISTORY_V1': raise ValueError('父历史阶段协议不符。')
    if (path['root'] != value['root'] or path['plan'] != value['plan'] or
            h['root'] != path['root'] or h['capability'] != path['runtime_capability'] or
            h['adjustment_capability'] != path['adjustment_capability'] or
            h['round_capability'] is None or h['round_capability'] != path['round_capability'] or
            h['driver'] != path['driver'] or h['authorization'] != path['authorization'] or
            h['frontier'] != path['history_frontier'] or h['material_observation'] != path['material_observation'] or
            h['task'] != {'id': value['item_id'], 'revision': h['driver']['item_revision']} or
            h['driver']['state'] != 'RESERVED' or h['phase'] not in {'BEFORE_A', 'A_CAPTURED', 'B_COMPLETED'} or
            h['authorization']['dispatch_before'] != value['original_dispatch_before']):
        raise ValueError('ROUND父观察须逐值绑定原路径、全部前沿、责任授权和材料。')
    complete_frontier(h['frontier']); observation(h['material_observation'])
    marker = h['marker']; fields(marker, {'protocol','plan_id','item_id','item_revision','project_id',
        'project_root','work_requirements_version','configuration_version','version'}); hashed(marker)
    task_revision(marker['item_revision'])
    for k in ('plan_id', 'item_id', 'project_id', 'project_root'): text(marker[k])
    for k in ('work_requirements_version', 'configuration_version'): sha(marker[k])
    if (marker['plan_id'] != path['root']['id'] or marker['item_id'] != value['item_id'] or
            marker['protocol'] != 'COORDINATOR_FILE_HANDOFF_ROOT_V1' or
            h['material_observation']['root'] != marker['project_root']):
        raise ValueError('ROUND父观察原根marker不符。')
    for name, kind in (('delivery', DELIVERY_KIND), ('completion', COMPLETION_KIND)):
        if not isinstance(h[name], list) or len(h[name]) > 1: raise ValueError('正式交付完成必须唯一。')
        for row in h[name]: delivery_row(kind, row)
    if not isinstance(h['units'], list) or len(h['units']) != len(path['current_qualifications']):
        raise ValueError('ROUND父须保留全部原定义和当前资格。')
    for u, q in zip(h['units'], path['current_qualifications']):
        fields(u, {'definition', 'qualification', *PERMISSIONS}); permissions(u)
        if u['definition']['key'] != q['key'] or u['qualification'] != q['qualification']:
            raise ValueError('ROUND父资格必须属于准确当前身份。')
    units([u['definition'] for u in h['units']])
    src = value['source']; unit = value['selected_unit']['definition']
    if (src['plan_id'] != value['plan']['id'] or src['plan_version'] != value['plan']['version'] or
            src['unit_revision'] != digest(unit) or unit not in [u['definition'] for u in h['units']] or
            unit['key'] in value['historical_coordinators'] or
            not any(f['run_id'] == src['run_id'] and f['input_version'] == src['input_version'] for f in h['frontier'])):
        raise ValueError('ROUND父来源须为完整前沿内准确当前plan的新回合。')
    ev = value['events']; fields(ev, {'cutoff', 'runs', 'checks', 'prior_check'})
    if type(ev['cutoff']) is not int or ev['cutoff'] < 1 or not isinstance(ev['runs'], list):
        raise ValueError('ROUND父须绑定真实全局事件截止。')
    if [r['run_id'] for r in ev['runs']] != [r['run_id'] for r in h['frontier']]:
        raise ValueError('ROUND父事件须覆盖全部有序历史运行。')
    seen = set()
    for r in ev['runs']:
        fields(r, {'run_id','reservation','result','captures'}); text(r['run_id'])
        event_ref(r['reservation'], 'cooperative_unit_reserved'); event_ref(r['result'], 'attempt_result_saved')
        if not isinstance(r['captures'], list) or r['reservation']['seq'] >= r['result']['seq']:
            raise ValueError('ROUND父运行必须实际退出。')
        for e in r['captures']:
            event_ref(e, 'cooperative_files_checked')
            if e['seq'] <= r['result']['seq']: raise ValueError('ROUND父快照必须晚于实际退出。')
        for e in [r['reservation'], r['result'], *r['captures']]:
            if e['seq'] > ev['cutoff'] or e['seq'] in seen: raise ValueError('ROUND父事件重复或超出截止。')
            seen.add(e['seq'])
    if not isinstance(ev['checks'], list): raise ValueError('ROUND父须保留完整旧检查历史。')
    previous = 0; ids = set()
    for c in ev['checks']:
        fields(c, {'reference','state','event'}); reference(c['reference']); text(c['state'])
        event_ref(c['event'], 'coordinator_proposal_checked'); seq = c['event']['seq']
        if not previous < seq <= ev['cutoff'] or seq in seen or c['reference']['id'] in ids:
            raise ValueError('ROUND父旧检查须唯一有序。')
        previous = seq; seen.add(seq); ids.add(c['reference']['id'])
    if (value['prior_check'] is None) != (ev['prior_check'] is None): raise ValueError('ROUND父同版本检查事件不符。')
    if ev['prior_check'] is not None:
        event_ref(ev['prior_check'], 'coordinator_proposal_checked')
        if not ev['checks'] or ev['checks'][-1] != {'reference': value['prior_check']['reference'],
                'state': value['prior_check']['state'], 'event': ev['prior_check']}:
            raise ValueError('ROUND父同版本检查必须为完整历史末项。')
    return deepcopy(value)


@closed
def validate_row(row):
    fields(row, {'id','version','snapshot','item_id','created','command_id'})
    b = row['snapshot']; fields(b, {'protocol','plan','observation','reason','scope','version',*PERMISSIONS})
    hashed(b); permissions(b); successor_plan(b['plan']); obs = validate_parent(b['observation'])
    text(b['reason']); finite(row['created']); text(row['command_id'])
    if (b['protocol'] != CHECKPOINT or b['scope'] != SCOPE or not b['reason'].strip() or len(b['reason']) > 2000 or
            plan_ref(b['plan']) != obs['plan'] or b['plan']['item_id'] != obs['item_id'] or
            b['plan']['units'] != [u['definition'] for u in obs['history']['units']] or
            b['plan']['coordinator_file_handoff_root'] != obs['history']['marker'] or
            row['id'] != KIND + ':' + b['version'] or row['version'] != b['version'] or row['item_id'] != obs['item_id']):
        raise ValueError('ROUND父V2检查点完整字段、身份或归属不符。')
    return deepcopy(row)


@closed
def required_assets(db, file_baseline):
    """Structural backup closure; it grants no runtime/history permission."""
    from .work_coordinator_handoff_round_assets import get, require_path_assets
    rows = [validate_row(json.loads(r[0])) for r in db.execute('SELECT data FROM collaboration WHERE kind=?', (KIND,))]
    events = list(db.execute('SELECT seq,payload,task_id,attempt_id FROM events WHERE kind=? ORDER BY seq', (EVENT,)))
    if sorted(map(digest, rows)) != sorted(digest(json.loads(r[1])) for r in events):
        raise ValueError('ROUND父备份检查点记录和事件不闭合。')
    for row in rows:
        obs = row['snapshot']['observation']; plan = row['snapshot']['plan']
        require_path_assets(db, obs['saved_path'])
        if get(db, 'teamwork_plan', obs['plan']) != plan:
            raise ValueError('ROUND父备份完整方案不符。')
        matches = [r for r in events if json.loads(r[1]) == row]
        if (len(matches) != 1 or matches[0][0] != obs['events']['cutoff'] + 1 or
                matches[0][2] is not None or matches[0][3] is not None):
            raise ValueError('ROUND父备份缺少准确首次事件。')
        file_baseline(obs['history']['material_observation'])


class WorkCoordinatorHandoffRoundParentCheckpoints:
    def __init__(self, service):
        self.s = service
        self.old = WorkCoordinatorHandoffRoundCheckpoints(service)
        self.sources = WorkCoordinatorHandoffRoundSources(service)

    @staticmethod
    def ref(row): return {k: row[k] for k in ('id', 'version')}

    def _assemble(self, db, plan, h, cutoff):
        candidates = [f for f in h['current_frames'] if f['run']['plan_id'] == plan['id'] and
            f['run']['generation'] == h['driver']['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        if not candidates: raise ValueError('ROUND父没有当前方案实际新协调回合。')
        frame = candidates[-1]; run = frame['run']; ev = frame['source_evidence']; unit = run['input']['unit']
        if (ev['state'] != 'COMPLETED' or not ev['exact_identity'] or not ev['result_available'] or
                not isinstance(ev['answer'], str) or not ev['answer'] or
                unit['member']['reference'] != plan['coordinator']['reference']):
            raise ValueError('ROUND父来源须为已实际退出的准确协调文字。')
        checks, rows = self.old._checks(db, plan, h, cutoff)
        prior = prior_event = None
        if checks:
            b = rows[checks[-1]['reference']['id']]['snapshot']
            if b['source']['run_id'] == run['id']:
                if b['source']['version'] != ev['version'] or b['plan'] != plan_ref(plan): raise ValueError('ROUND父同版本检查来源不符。')
                prior = {'reference': checks[-1]['reference'], 'state': b['state'], 'problems': b['problems']}
                prior_event = checks[-1]['event']
        carry_sources = {}; retired = plan['file_handoffs'][0]['source_unit'] if h['phase'] == 'B_COMPLETED' else None
        from .work_coordinator_handoff_adjustments import _require_result
        current = {f['run']['unit_key']: f for f in h['current_frames']}
        for u in plan['units']:
            if u['role'] == 'coordinate' or u['key'] == retired or u['key'] not in current: continue
            f = current[u['key']]; e = f['source_evidence']
            if e['state'] != 'COMPLETED' or not e['exact_identity'] or f['run']['input']['unit'] != u:
                raise ValueError('ROUND父普通成果须完整原定义准确完成。')
            _require_result(e, u); carry_sources[u['key']] = source_ref(f['run'], e)
        events = []
        for f in h['frames']:
            r = f['run']; attempt = self.s.store.attempt(r['attempt_id'], db)
            events.append({'run_id': r['id'],
                'reservation': self.old._event(db, 'cooperative_unit_reserved', r, cutoff, r['task_id'], r['attempt_id']),
                'result': self.old._event(db, 'attempt_result_saved', attempt['result'], cutoff, r['task_id'], r['attempt_id']),
                'captures': [self.sources._event(e) for e in db.execute(
                    "SELECT * FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked' ORDER BY seq",
                    (r['task_id'], r['attempt_id']))]})
        historical = set(plan['historical_coordinators']) | {f['run']['unit_key'] for f in h['frames']
            if f['run']['input']['unit']['role'] == 'coordinate'}
        historical.discard(unit['key'])
        return validate_parent(versioned({'protocol': PARENT, 'item_id': plan['item_id'], 'root': h['root'],
            'plan': plan_ref(plan), 'parent_kind': 'ROUND', 'saved_path': h['saved_path'],
            'history': {k: deepcopy(h[k]) for k in HISTORY_FIELDS}, 'source': source_ref(run, ev),
            'selected_unit': {'definition': unit, 'revision': digest(unit)}, 'prior_check': prior,
            'carry_sources': carry_sources, 'historical_coordinators': sorted(historical),
            'usage': h['usage'], 'original_dispatch_before': h['authorization']['dispatch_before'],
            'events': {'cutoff': cutoff, 'runs': events, 'checks': checks, 'prior_check': prior_event},
            'scope': SCOPE, **PERMISSIONS}))

    def _observe_once(self, db, plan):
        if not db.in_transaction: raise ValueError('ROUND父观察必须属于明确同一事务。')
        history = WorkCoordinatorHandoffRoundParentHistory(self.s); h = history.audit_current(db, plan)
        item = self.s.work_items.item(db, plan['item_id']); auth = h['authorization']
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(plan)})
        self.s.work_flows.require_stopped(db, item['id'])
        if len(h['frames']) >= auth['max_attempts_total'] or time.time() >= auth['dispatch_before']:
            raise ValueError('ROUND父原累计或最初截止已无余量。')
        return self._assemble(db, plan, h, db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]), h

    @closed
    def observe(self, db, plan):
        with database_observation(db):
            before = db.total_changes
            a, _ = self._observe_once(db, plan); b, h = self._observe_once(db, plan)
            self.sources._recheck(db, plan, b, h)
            if a != b or db.total_changes != before or time.time() >= b['original_dispatch_before']:
                raise ValueError('ROUND父观察完整重建或最初截止发生变化。')
            return deepcopy(b)

    def get(self, db, ref):
        reference(ref)
        raw = db.execute('SELECT kind,data FROM collaboration WHERE id=?', (ref['id'],)).fetchone()
        if raw is None or raw[0] != KIND: raise ValueError('缺少准确ROUND父V2检查点。')
        row = validate_row(json.loads(raw[1]))
        if self.ref(row) != ref: raise ValueError('ROUND父检查点引用版本不符。')
        return row

    def _namespace(self, db, plan, history):
        # V2 owns a full nested plan/observation, rather than the V1 top-level
        # root/item fields. An incorrect outer label cannot hide that identity.
        def related(v):
            obj = lambda x: x if isinstance(x, dict) else {}
            b = obj(v.get('snapshot')); obs = obj(b.get('observation'))
            return (v.get('item_id') == plan['item_id'] or
                obj(b.get('plan')).get('id') in history._plan_ids or
                obj(obs.get('plan')).get('id') in history._plan_ids or
                obj(obs.get('root')).get('id') in history._plan_ids)
        rows = [v for v in self.s.collaboration.all(db, KIND) if related(v)]
        events = [r for r in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (EVENT,))
            if related(json.loads(r['payload']))]
        if any(r['task_id'] is not None or r['attempt_id'] is not None for r in events):
            raise ValueError('ROUND父检查点事件不能伪装成运行事件。')
        if sorted(map(digest, rows)) != sorted(digest(json.loads(r['payload'])) for r in events):
            raise ValueError('ROUND父全部检查点记录和事件存在孤立或遗漏。')
        if len(rows) != 1: raise ValueError('本版首ROUND父只允许一个完整准确检查点。')
        for r in rows: validate_row(r)

    def _once(self, db, ref):
        row = self.get(db, ref); b = row['snapshot']; frozen = b['observation']; plan = b['plan']
        event = self.old._event(db, EVENT, row)
        if event['seq'] != frozen['events']['cutoff'] + 1 or row['created'] >= frozen['original_dispatch_before']:
            raise ValueError('ROUND父检查点必须为观察后首条真实事件且未过原截止。')
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('ROUND父完整原方案已变化。')
        history = WorkCoordinatorHandoffRoundParentHistory(self.s)
        h, current = history._walk(db, plan, event, frozen['history']['material_observation'], audit_namespace=True)
        rebuilt = self._assemble(db, plan, h, frozen['events']['cutoff'])
        if rebuilt != frozen: raise ValueError('ROUND父检查点不等于实际事件截止逐项重建的历史。')
        # Independent live audit, not a historical proof. This intentionally
        # fails after any successor plan until the complete mixed gate exists.
        self._namespace(db, plan, history)
        return {'checkpoint': row, 'checkpoint_event': event, 'observation': rebuilt,
            'frames': h['frames'], 'current_material_observation': current,
            'saved_edge_verified': False, 'full_mixed_path_verified': False, 'public_apply': False, **PERMISSIONS}

    @closed
    def read(self, db, ref):
        with database_observation(db):
            before = db.total_changes
            row = self.get(db, ref); pointer = deepcopy(self.s.teamwork.current(db, row['item_id']))
            a = self._once(db, ref); b = self._once(db, ref)
            self.old._recheck(db, b)
            if a != b or before != db.total_changes or self.s.teamwork.current(db, row['item_id']) != pointer:
                raise ValueError('ROUND父检查点完整重建、数据库或当前指针已变化。')
            return deepcopy(b)

    @contextmanager
    def preserving(self, db, plan, reason):
        if not db.in_transaction: raise ValueError('ROUND父检查点必须属于同一命令事务。')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('ROUND父检查点须有非空原因且最多2000字。')
        obs = self.observe(db, plan); baseline = obs['history']['material_observation']
        files = self.s.work_file_rework_applications; asset = files.folder(baseline).parent
        results = (self.s.locations.data / 'results').resolve()
        if asset.resolve().parent != results or asset.name != 'file-rework-base-' + baseline['version'] or asset.is_symlink():
            raise ValueError('ROUND父材料目录不是准确服务资产位置。')
        existed = asset.exists(); db.execute('SAVEPOINT round_parent_v2_assets')
        try:
            files.preserve(baseline)
            if self.observe(db, plan) != obs: raise ValueError('ROUND父材料保存期间历史已变化。')
            body = versioned({'protocol': CHECKPOINT, 'plan': plan, 'observation': obs, 'reason': reason,
                'scope': SCOPE, **PERMISSIONS})
            row = validate_row({'id': KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
                'item_id': plan['item_id'], 'created': time.time(), 'command_id': self.s.command_identity})
            if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
                raise ValueError('ROUND父检查点身份不能覆盖。')
            self.s.collaboration.put(db, KIND, row); self.s.store.event(None, None, EVENT, row, db)
            result = self.read(db, self.ref(row))
            yield deepcopy(result)
            if self.read(db, self.ref(row)) != result or time.time() >= obs['original_dispatch_before']:
                raise ValueError('ROUND父检查点保存末尾完整历史或原截止已变化。')
        except BaseException:
            db.execute('ROLLBACK TO round_parent_v2_assets'); db.execute('RELEASE round_parent_v2_assets')
            if not existed and asset.exists():
                if asset.resolve().parent != results or asset.is_symlink(): raise ValueError('ROUND父失败材料位置变化，保留现场。')
                shutil.rmtree(asset)
            raise
        else: db.execute('RELEASE round_parent_v2_assets')
