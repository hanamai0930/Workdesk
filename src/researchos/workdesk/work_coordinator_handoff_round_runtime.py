"""Actual first ROUND runtime, after ROOT or the original adjustment chain.

This separate protocol includes child runs. It does not broaden SAVED_PATH_V1
or SAVED_DRIVER_HISTORY_V1, and does not yet accept a ROUND parent.
"""
from copy import deepcopy
import json
import time

from .store import canonical, digest
from .work_read_observation import database_observation
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import MARKER_FIELD
from .work_coordinator_handoff_records import (
    PERMISSIONS, versioned, fields, reference, observation, DELIVERY_KIND, COMPLETION_KIND)
from .work_coordinator_handoff_adjustment_records import hashed, permissions, complete_frontier, finite
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, READ
from .work_coordinator_handoff_round_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, SAVE_EVENT, KIND_FOR, EVENTS,
    event_ref, record_ref, PAUSE_REASON, WorkCoordinatorHandoffRoundRecords)
from .work_coordinator_handoff_round_contracts import (
    successor_plan, successor_driver, transferred_authorization, adjacent)
from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
from .work_coordinator_handoff_round_path import WorkCoordinatorHandoffRoundPath

PATH = 'COORDINATOR_FILE_HANDOFF_ROUND_RUNTIME_PATH_V1'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V4'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V3'
KIND = 'work_coordinator_handoff_round_capability'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_ROUND_RUNTIME_CAPABILITY_V1'
OPENED = 'coordinator_round_runtime_opened'
SCOPE = 'FIRST_ROUND_AFTER_ROOT_OR_ADJUSTMENT; DISPATCH_PAUSE_ONLY; ROUND_PARENT_NOT_OPEN'
PROMPT = '读取完整冻结协调输入，只返回 dispatch_schema 或 pause_schema 对应严格 JSON；不写文件。\n输入快照 JSON：\n'


@closed
def validate_context(value):
    coordinate = value['protocol'] == PROTOCOL
    common = {'protocol', 'root', 'marker', 'plan', 'task', 'driver', 'authorization', 'phase',
        'prior_frontier', 'material_observation', 'saved_path', 'runtime_capability',
        'adjustment_capability', 'round_capability', 'round_registration', 'source', 'prior_check',
        'scope', 'version', *PERMISSIONS}
    fields(value, common | ({'work_requirements', 'configuration', 'units', 'allowed_actions'} if coordinate
        else {'unit_revision', 'proposal'}))
    hashed(value); permissions(value)
    if value['protocol'] not in (PROTOCOL, UNIT) or value['scope'] != SCOPE:
        raise ValueError('ROUND冻结输入须使用准确V4或普通V3协议。')
    path = validate_path(value['saved_path'])
    from .work_coordinator_handoff_records import identity, task_revision, PHASES, sha
    identity(value); fields(value['task'], {'id', 'revision'}); task_revision(value['task']['revision'])
    if (any(value[k] != path[k] for k in ('root', 'plan', 'driver', 'authorization', 'runtime_capability',
            'adjustment_capability', 'round_capability', 'source', 'prior_check', 'material_observation')) or
            value['prior_frontier'] != path['history_frontier'] or value['round_registration'] != path['edge']['registration'] or
            value['round_capability'] is None or value['task']['id'] != value['driver']['item_id'] or
            value['task']['revision'] != value['driver']['item_revision'] or value['phase'] not in PHASES or
            value['marker']['plan_id'] != path['root']['id'] or value['material_observation']['root'] != value['marker']['project_root']):
        raise ValueError('ROUND冻结输入须准确绑定登记、全部前沿、材料、当前方案和原授权。')
    if coordinate:
        if value['allowed_actions'] != ['dispatch', 'pause']: raise ValueError('V4仅开放派发与暂停。')
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        fields(value['configuration'], CONFIG_FIELDS)
        if not isinstance(value['units'], list) or len(value['units']) != len(path['current_qualifications']):
            raise ValueError('V4须保存完整原声明单元和准确资格。')
        for entry, q in zip(value['units'], path['current_qualifications']):
            fields(entry, {'definition', 'state', 'source_run', 'qualification'})
            if entry['definition']['key'] != q['key'] or entry['qualification'] != q['qualification']:
                raise ValueError('V4单元资格与准确路径不符。')
    else:
        sha(value['unit_revision']); fields(value['proposal'], {'reference', 'source', 'decision'})
        reference(value['proposal']['reference'])
        from .work_coordinator_handoff_adjustment_records import source
        from .work_proposals import parse
        source(value['proposal']['source'])
        decision = value['proposal']['decision']
        if parse(json.dumps(decision)) != decision or decision['action'] != 'dispatch' or decision['retry_of'] is not None:
            raise ValueError('普通V3必须消费准确新V4派发提案。')
    return deepcopy(value)


@closed
def validate_path(value):
    fields(value, {'protocol', 'root', 'plan', 'parent_kind', 'parent_path', 'edge',
        'history_frontier', 'current_qualifications', 'material_observation',
        'driver_history', 'driver', 'authorization', 'runtime_capability',
        'adjustment_capability', 'round_capability', 'source', 'prior_check',
        'scope', 'version', *PERMISSIONS})
    hashed(value); permissions(value)
    if value['protocol'] != PATH or value['scope'] != SCOPE:
        raise ValueError('首条ROUND运行路径须使用独立准确协议。')
    for k in ('root', 'plan', 'runtime_capability'): reference(value[k])
    for k in ('adjustment_capability', 'round_capability'):
        if value[k] is not None: reference(value[k])
    from .work_coordinator_handoff_adjustment_records import validate_path as old_path, source
    from .work_coordinator_handoff_round_records import prior_check
    parent = value['root']
    if value['parent_kind'] == 'ADJUSTMENT':
        old = old_path(value['parent_path']); parent = old['plan']
        if old['root'] != value['root']: raise ValueError('ROUND运行路径原根不符。')
    elif value['parent_kind'] != 'ROOT' or value['parent_path'] is not None:
        raise ValueError('本版ROUND运行路径只接受ROOT或原ADJUSTMENT父。')
    e = value['edge']; fields(e, {'parent_plan', 'child_plan', 'checkpoint', 'candidate',
        'application', 'registration', 'save_event', 'registration_event'})
    for k in ('parent_plan', 'child_plan'): reference(e[k])
    for k, protocol in (('checkpoint', CHECKPOINT), ('candidate', CANDIDATE),
            ('application', APPLICATION), ('registration', REGISTRATION)):
        record_ref(e[k], protocol)
    event_ref(e['save_event'], SAVE_EVENT); event_ref(e['registration_event'], EVENTS[REGISTRATION])
    if (e['parent_plan'] != parent or e['child_plan'] != value['plan'] or
            value['plan'] in (parent, value['root']) or
            e['save_event']['seq'] >= e['registration_event']['seq']):
        raise ValueError('ROUND运行路径相邻边或完整登记不符。')
    complete_frontier(value['history_frontier']); observation(value['material_observation'])
    from .work_coordinator_handoff_adjustment_records import qualifications
    qualifications(value['current_qualifications'])
    driver = successor_driver(value['driver']); auth = transferred_authorization(value['authorization'])
    versions = value['driver_history']
    if not isinstance(versions, list) or not versions: raise ValueError('ROUND运行路径须包含真实责任历史。')
    last = 0
    for v in versions:
        fields(v, {'event', 'driver'}); event_ref(v['event']); successor_driver(v['driver'])
        if v['event']['seq'] <= last: raise ValueError('ROUND责任历史须按实际事件排序。')
        last = v['event']['seq']
        if (v['driver']['plan_id'] != value['plan']['id'] or v['driver']['plan_version'] != value['plan']['version'] or
                v['driver']['transition_registration'] != e['candidate']):
            raise ValueError('ROUND责任历史不得跨方案或登记。')
    if (versions[-1]['driver'] != driver or versions[0]['driver']['state'] != 'PAUSED' or
            versions[0]['driver']['reason'] != PAUSE_REASON or
            versions[0]['event']['kind'] != 'work_driver_coordinator_handoff_round_revised' or
            versions[0]['event']['seq'] >= e['save_event']['seq'] or
            any(v['plan_id'] != value['plan']['id'] or v['plan_version'] != value['plan']['version'] for v in (driver, auth)) or
            auth['transfer_registration'] != e['candidate'] or driver['generation'] != auth['driver_generation']):
        raise ValueError('ROUND运行路径责任、授权或保存暂停起点不符。')
    source(value['source'], full=True); prior_check(value['prior_check'])
    return deepcopy(value)


class WorkCoordinatorHandoffRoundRuntime(WorkCoordinatorHandoffAdjustmentPath):
    def __init__(self, service):
        super().__init__(service)
        self.data = WorkCoordinatorHandoffRoundRecords(service)
        self.checkpoints = WorkCoordinatorHandoffRoundCheckpoints(service)
        self.namespace = WorkCoordinatorHandoffRoundPath(service)

    def _related(self, db, kind, item):
        return self.namespace._related(db, kind, item, self._plan_ids)

    def _events(self, db, kind, item):
        return self.namespace._events(db, kind, item, self._plan_ids)

    _same = staticmethod(WorkCoordinatorHandoffRoundPath._same)

    def _base(self, db, plan):
        successor_plan(plan)
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('ROUND运行须绑定真实完整方案。')
        ready = self.checkpoints.read_candidate(db, plan['coordinator_file_handoff_round_lineage']['candidate'])
        obs = ready['observation']; parent = ready['checkpoint']['snapshot']['plan']
        if obs['parent_kind'] == 'ROOT': parents = [parent]
        else:
            root, chain = WorkCoordinatorHandoffAdjustmentPath(self.s)._chain(db, parent); parents = [root] + chain
        self._parents = parents; self._root_plan = parents[0]; self._plan_ids = {p['id'] for p in parents + [plan]}
        apps = [r for r in self._related(db, KIND_FOR[APPLICATION], plan['item_id']) if r['snapshot']['child_plan'] == plan_ref(plan)]
        regs = [r for r in self._related(db, KIND_FOR[REGISTRATION], plan['item_id']) if r['snapshot']['to_plan'] == plan_ref(plan)]
        if len(apps) != 1 or len(regs) != 1: raise ValueError('ROUND运行须有唯一实际应用和登记。')
        app = self.data.get(db, APPLICATION, self.ref(apps[0])); reg = self.data.get(db, REGISTRATION, self.ref(regs[0]))
        b = app['snapshot']; after = self.old._driver(db, plan, b['driver_after']); auth = self.old._auth(db, plan, b['authorization_after'])
        h = obs['history']; cp = ready['checkpoint']; candidate = ready['candidate']
        adjacent(cp, candidate, plan, h['authorization'], auth, h['driver'], after)
        from .work_coordinator_handoff_round_apply import bodies, save_payload
        save = self.checkpoints._event(db, SAVE_EVENT, save_payload(self.s, candidate, plan, after, auth))
        ae = self.checkpoints._event(db, EVENTS[APPLICATION], app); re = self.checkpoints._event(db, EVENTS[REGISTRATION], reg)
        if (bodies(self.s, cp, candidate, plan, h['driver'], after, h['authorization'], auth, save) != (b, reg['snapshot']) or
                not ready['candidate_event']['seq'] < self._driver_event(db, after) < save['seq'] < ae['seq'] < re['seq'] or
                any(r['command_id'] != candidate['command_id'] for r in (app, reg)) or
                not candidate['created'] <= auth['transferred_at'] <= after['updated'] <= app['created'] <= reg['created'] < auth['dispatch_before']):
            raise ValueError('ROUND运行实际保存边、完整登记、时间或事件顺序不符。')
        edge = {'parent_plan': obs['plan'], 'child_plan': plan_ref(plan),
            'checkpoint': self.ref(cp), 'candidate': self.ref(candidate), 'application': self.ref(app),
            'registration': self.ref(reg), 'save_event': save, 'registration_event': re}
        return ready, app, reg, after, auth, edge

    def _drivers(self, db, plan, start, registered):
        values = []
        for r in self._related(db, 'work_driver_revision', plan['item_id']):
            if r['plan_id'] != plan['id']: continue
            v = self.old._driver(db, plan, {'id': r['driver_id'], 'generation': r['generation'], 'revision': r['revision']})
            successor_driver(v); values.append((self._driver_event(db, v), v, r))
        values.sort(key=lambda x: x[0])
        if not values or values[0][1] != start or any(seq <= registered for seq, _, _ in values[1:]):
            raise ValueError('ROUND保存后责任须从完整登记开始准确重放。')
        self._driver_transitions(db, values)
        return values

    def capability(self, db, plan):
        return WorkCoordinatorHandoffRuntime.capability(self, db, self._root_plan)

    def _cap_body(self, plan, ready, edge, driver):
        obs = ready['observation']
        return versioned({'protocol': CAPABILITY, 'root': obs['root'], 'plan': plan_ref(plan),
            'runtime_capability': obs['history']['capability'], 'adjustment_capability': obs['history']['adjustment_capability'],
            'registration': edge['registration'], 'registration_event': edge['registration_event'],
            'source': obs['source'], 'prior_check': obs['prior_check'], 'activation_frontier': obs['history']['frontier'],
            'driver': self.s.work_drivers.reference(driver), 'authorization': self.s.work_units.auth_ref(self._auth),
            'coordination_protocol': PROTOCOL, 'unit_protocol': UNIT, 'allowed_actions': ['dispatch', 'pause'],
            'scope': SCOPE, **PERMISSIONS})

    def _round_capability(self, db, plan, ready, edge, versions):
        rows = self._related(db, KIND, plan['item_id']); events = self._events(db, OPENED, plan['item_id'])
        self._same([v for _, v in events], rows, 'ROUND能力记录与实际事件全集不符。')
        if not rows: return None
        if len(rows) != 1: raise ValueError('首条ROUND只允许唯一准确运行能力。')
        row = rows[0]; fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
        driver = next((v for _, v, _ in versions if self.s.work_drivers.reference(v) == row['snapshot']['driver']), None)
        if driver is None: raise ValueError('ROUND能力缺少实际责任版本。')
        expected = self._cap_body(plan, ready, edge, driver); finite(row['created'])
        opened = self.checkpoints._event(db, OPENED, row)['seq']
        if (row['snapshot'] != expected or row['id'] != KIND + ':' + expected['version'] or
                row['version'] != expected['version'] or row['item_id'] != plan['item_id'] or
                not isinstance(row['command_id'], str) or not row['command_id'] or driver['state'] != 'RESERVED' or
                not edge['registration_event']['seq'] < self._driver_event(db, driver) < opened or
                not driver['updated'] <= row['created'] < self._auth['dispatch_before']):
            raise ValueError('ROUND能力须绑定真实登记、原能力、原授权和明确恢复责任。')
        if any(self._driver_event(db, driver) < seq < opened for seq, _, _ in versions):
            raise ValueError('ROUND能力须绑定激活事件前最新实际责任。')
        return row

    def activate_round(self, db, plan):
        h = self.read(db, plan)
        if self._round is not None: return self._round
        if (h['driver']['state'] != 'RESERVED' or len(h['frames']) >= h['authorization']['max_attempts_total'] or
                time.time() >= h['authorization']['dispatch_before']):
            raise ValueError('ROUND运行能力须明确恢复且仍有原授权余量。')
        body = self._cap_body(plan, self._ready, self._edge_value, h['driver'])
        row = {'id': KIND + ':' + body['version'], 'version': body['version'], 'snapshot': body,
            'item_id': plan['item_id'], 'created': time.time(), 'command_id': self.s.command_identity}
        if db.execute('SELECT 1 FROM collaboration WHERE id=?', (row['id'],)).fetchone():
            raise ValueError('ROUND能力身份已被占用。')
        self.s.collaboration.put(db, KIND, row); self.s.store.event(None, None, OPENED, row, db)
        self.read(db, plan)
        return deepcopy(row)

    def _eligible(self, plan, frames):
        # Current coordinate identity belongs to this plan. Carried ordinary
        # results keep their actual original run and frozen dependencies.
        return [f for f in frames if f['run']['plan_id'] == plan['id'] or
            (f['run']['unit_key'] in plan['result_lineage'] and
             plan['result_lineage'][f['run']['unit_key']]['run_id'] == f['run']['id'])]

    def _admit(self, plan, unit, frames, phase, pending=None):
        if unit['key'] in plan['historical_coordinators']: raise ValueError('旧协调历史不能作为本方案新回合。')
        return WorkCoordinatorHandoffRuntime._admit(self, plan, unit, self._eligible(plan, frames), phase, pending)

    def _dependencies(self, plan, unit, frames, phase, pending=None):
        return WorkCoordinatorHandoffRuntime._dependencies(self, plan, unit, self._eligible(plan, frames), phase, pending)

    def _path_value(self, plan, frames, material, phase, driver):
        from .work_coordinator_handoff_adjustments import qualifications
        seq = self._driver_event(self._db, driver)
        history = [{'event': self.checkpoints.live._event(self._db.execute('SELECT * FROM events WHERE seq=?', (s,)).fetchone()),
            'driver': v} for s, v, _ in self._versions if s <= seq]
        obs = self._ready['observation']
        return validate_path(versioned({'protocol': PATH, 'root': obs['root'], 'plan': plan_ref(plan),
            'parent_kind': obs['parent_kind'], 'parent_path': obs['saved_path'], 'edge': self._edge_value,
            'history_frontier': [self.old.frontier_entry(f) for f in frames],
            'current_qualifications': qualifications(plan, phase, self._eligible(plan, frames)),
            'material_observation': material, 'driver_history': history, 'driver': driver, 'authorization': self._auth,
            'runtime_capability': obs['history']['capability'], 'adjustment_capability': obs['history']['adjustment_capability'],
            'round_capability': self.ref(self._round) if self._round else None,
            'source': obs['source'], 'prior_check': obs['prior_check'], 'scope': SCOPE, **PERMISSIONS}))

    def _input(self, plan, unit, task, driver, auth, frames, material, records, cap, proposal=None):
        if self._round is None: raise ValueError('新输入缺少真实ROUND运行能力。')
        phase = self._phase(plan, frames, records); deps = self._admit(plan, unit, frames, phase)
        path = self._path_value(plan, frames, material, phase, driver)
        value = {'item_id': plan['item_id'], 'item_revision': task['revision'], 'plan_id': plan['id'],
            'plan_version': plan['version'], 'work_requirements': plan['work_requirements'], 'unit': unit,
            'dependencies': deps, 'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
                'check_status': d['check_status']} for d in deps],
            'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']} for d in deps if d['file_artifacts']['state'] == 'CURRENT']}
        context = {'protocol': PROTOCOL if unit['role'] == 'coordinate' else UNIT,
            'root': plan_ref(self._root_plan), 'marker': plan[MARKER_FIELD], 'plan': plan_ref(plan), 'task': task,
            'driver': driver, 'authorization': auth, 'phase': phase, 'prior_frontier': path['history_frontier'],
            'material_observation': material, 'saved_path': path, 'runtime_capability': path['runtime_capability'],
            'adjustment_capability': path['adjustment_capability'], 'round_capability': path['round_capability'],
            'round_registration': path['edge']['registration'], 'source': path['source'], 'prior_check': path['prior_check'],
            'scope': SCOPE, **PERMISSIONS}
        if unit['role'] == 'coordinate':
            # Context states and candidate readiness use only eligible sources;
            # prior_frontier still keeps the complete original ordered history.
            old = self.old._context(plan, task, driver, auth, self._eligible(plan, frames), material)
            context.update({k: old[k] for k in ('work_requirements', 'configuration', 'units')})
            for entry, qualification in zip(context['units'], path['current_qualifications']):
                entry['qualification'] = qualification['qualification']
            context.update(allowed_actions=['dispatch', 'pause'])
            context = versioned(context)
            forecast = self._forecast(plan, context, frames, unit)
            forecast.update(scope=SCOPE, previous_round=deepcopy(plan['coordination_revision']))
            value.update(handoff_coordination_context=context, coordination_context=forecast)
        else:
            if proposal is None: raise ValueError('普通V3输入须有准确新V4派发提案。')
            context.update(unit_revision=digest(unit), proposal=proposal)
            value.update(handoff_unit_context=versioned(context), coordination_proposal=proposal)
        validate_context(value.get('handoff_coordination_context') or value['handoff_unit_context'])
        return deepcopy(value)

    def _source_protocol(self, db, plan, source, consumer):
        return source['plan_id'] == plan['id'] and source['input'].get('handoff_coordination_context', {}).get('protocol') == PROTOCOL

    def _run_protocol(self, db, plan, run, context, boundary):
        return (self._round is not None and boundary > self.checkpoints._event(db, OPENED, self._round)['seq'] and
            context['protocol'] == (PROTOCOL if run['input']['unit']['role'] == 'coordinate' else UNIT))

    def _historical_input(self, db, run, *args): return self._input(*args)

    def _authorization_event(self, db, auth): return self._edge_value['save_event']['seq']

    @staticmethod
    def prompt(frozen):
        from .work_coordinator_handoff_runtime import UNIT_PROMPT
        return (PROMPT if frozen['unit']['role'] == 'coordinate' else UNIT_PROMPT) + canonical(frozen)

    def _records(self, db, plan): return WorkCoordinatorHandoffRuntime._records(self, db, self._root_plan)

    def input_for(self, db, item, plan, unit, rows):
        h = self.read(db, plan)
        if (rows != self.s.work_units.rows(db, item['id']) or h['task'] != {'id': item['id'], 'revision': item['revision']} or
                h['driver']['state'] != 'RESERVED' or len(rows) >= h['authorization']['max_attempts_total'] or
                time.time() >= h['authorization']['dispatch_before']):
            raise ValueError('ROUND输入的任务、前沿、当前责任或原累计/截止不符。')
        frames = deepcopy(h['frames'])
        for f in frames: f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        proposal = None
        if unit['role'] != 'coordinate':
            row = self.s.work_proposals.current(db, item['id'])
            if not row or any(r.get('proposal') == self.ref(row) for r in rows): raise ValueError('新V4派发提案缺失或已消费。')
            proposal = {'reference': self.ref(row), **{k: row['snapshot'][k] for k in ('source', 'decision')}}
            self._proposal(db, plan, unit, frames, proposal, h['task'], h['driver'], h['authorization'])
        cap, _ = self.capability(db, plan)
        frozen = self._input(plan, unit, h['task'], h['driver'], h['authorization'], frames,
            h['material_observation'], self._records(db, plan), cap, proposal)
        if unit['key'] == plan['file_handoffs'][0]['target_unit']: frozen['handoff_delivery_intent'] = self._intent(plan, frozen, frames)
        return frozen

    def deliver(self, db, plan, run):
        return WorkCoordinatorHandoffRuntime.deliver(self, db, self._root_plan, run)

    def complete(self, db, plan, run):
        return WorkCoordinatorHandoffRuntime.complete(self, db, self._root_plan, run)

    def _intent(self, plan, frozen, frames):
        from .work_coordinator_handoff_round_intent import PROTOCOL as INTENT, SCOPE as INTENT_SCOPE, validate_intent
        context = frozen['handoff_unit_context']; relation = plan['file_handoffs'][0]
        frame = next(f for f in self._eligible(plan, frames) if f['run']['unit_key'] == relation['source_unit'])
        full = frame['source_evidence']; baseline = context['material_observation']
        self.s.work_artifacts.require(full, 'checked' if frame['run']['input']['unit']['checks'] or frozen['unit']['dependency_gate'] == 'checked' else 'files')
        return validate_intent(versioned({'protocol': INTENT, 'item_id': plan['item_id'], 'item_revision': context['task']['revision'],
            'root': context['root'], 'marker': plan[MARKER_FIELD], 'plan': plan_ref(plan), 'saved_path': context['saved_path'],
            'relation': relation, 'driver': context['driver'], 'authorization': context['authorization'],
            'source_run': frame['run']['id'], 'source_evidence': full, 'target_revision': digest(frozen['unit']),
            'protected_history': context['prior_frontier'], 'baseline': baseline, 'input_template_version': digest(frozen),
            'frontier': context['prior_frontier'], 'scope': INTENT_SCOPE}))

    def _once(self, db, plan):
        self._db = db
        ready, app, reg, start, auth, edge = self._base(db, plan)
        self._ready = ready; self._edge_value = edge; self._auth = auth
        self._versions = self._drivers(db, plan, start, edge['registration_event']['seq']); driver = self._versions[-1][1]
        self._round = self._round_capability(db, plan, ready, edge, self._versions)
        cap, activated = self.capability(db, plan)
        self._inherited = ready['frames']; rows = [r for r in self.s.work_units.rows(db, plan['item_id']) if r['plan_id'] == plan['id']]
        if rows and self._round is None: raise ValueError('ROUND新运行缺少真实独立能力。')
        if self._round and any(self.sources._reservation_event(db, r) <= self.checkpoints._event(db, OPENED, self._round)['seq'] for r in rows):
            raise ValueError('ROUND新运行早于真实能力事件。')
        if rows and (rows[0]['command_id'] != self._round['command_id'] or rows[0]['created'] < self._round['created']):
            raise ValueError('ROUND能力须随首次新运行在同一真实命令中激活。')
        records = self._records(db, plan)
        frames, revision, _ = self._layer_runs(db, plan, ready['observation']['history']['task'],
            self._root_plan[MARKER_FIELD]['project_root'], cap, activated, driver, auth,
            [r for _, _, r in self._versions], rows, records)
        if revision != driver['item_revision']: raise ValueError('ROUND任务绑定没有覆盖完整运行历史。')
        self._match_root_records(db, self._root_plan, frames, records)
        phase = self._phase(plan, frames, records); current = deepcopy(frames)
        for f in current: f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        expected = plan['file_handoffs'][0]['target_unit'] if phase == 'B_COMPLETED' else plan['file_handoffs'][0]['source_unit'] if phase == 'A_CAPTURED' else None
        if expected and next(f for f in reversed(current) if f['run']['unit_key'] == expected)['source_evidence']['file_artifacts']['state'] != 'CURRENT': phase = 'BLOCKED'
        material = self.s.work_file_rework_applications.observe(self._root_plan[MARKER_FIELD]['project_root'], plan['file_handoffs'][0]['paths'])
        from .work_coordinator_handoff_adjustments import qualifications
        h = {'protocol': READ, 'root': plan_ref(self._root_plan), 'marker': self._root_plan[MARKER_FIELD], 'capability': self.ref(cap),
            'adjustment_capability': ready['observation']['history']['adjustment_capability'], 'round_capability': self.ref(self._round) if self._round else None,
            'task': {'id': plan['item_id'], 'revision': revision}, 'driver': driver, 'authorization': auth,
            'frames': frames, 'current_frames': self._eligible(plan, current), 'phase': phase,
            'frontier': [self.old.frontier_entry(f) for f in frames], 'material_observation': material,
            'delivery': [r for r, _ in records[DELIVERY_KIND]], 'completion': [r for r, _ in records[COMPLETION_KIND]],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS} for u, q in
                zip(plan['units'], qualifications(plan, phase, self._eligible(plan, current)))],
            'full_mixed_path_verified': False, 'successor_layers_verified': True, 'verified_scope': SCOPE, 'public_apply': False, **PERMISSIONS}
        self._scope(db, plan, ready, app, reg, start, auth, edge['save_event'], ready['observation']['saved_path'], frames, h)
        h['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        return deepcopy(h)

    def read(self, db, plan): return self._read_current(db, plan)

    @closed
    def _read_current(self, db, plan):
        if not db.in_transaction: raise ValueError('ROUND运行读取须在明确同一数据库事务内。')
        with database_observation(db):
            before = db.total_changes
            first = self._once(db, plan); again = self._once(db, plan)
            for f in again['current_frames']:
                if self.s.work_units.evidence(db, f['run']) != f['source_evidence']: raise ValueError('ROUND当前来源末尾发生变化。')
            self.checkpoints._recheck(db, {'frames': again['frames'], 'observation': self._ready['observation'],
                'current_material_observation': again['material_observation']})
            # Elapsed time is a live display observation, outside the frozen
            # path/input proof. RESERVED attempts accrue time between reads.
            comparable = lambda v: {**v, 'usage': {k: x for k, x in v['usage'].items()
                if k != 'elapsed_observed_seconds'}}
            if comparable(first) != comparable(again) or before != db.total_changes:
                changed = [k for k in first if comparable(first)[k] != comparable(again).get(k)]
                raise ValueError('ROUND完整两次重建或数据库发生变化：' + ','.join(changed))
            return deepcopy(again)

    def _scope(self, db, plan, ready, application, registration, after, auth, save, parent_path, frames, h):
        """Account for every plan/run/auth/driver and saved record in this scope."""
        cp = ready['checkpoint']; obs = ready['observation']; parent = cp['snapshot']['plan']
        old = WorkCoordinatorHandoffAdjustmentPath(self.s)
        if obs['parent_kind'] == 'ROOT': parents = [parent]
        else:
            root, chain = old._chain(db, parent); parents = [root] + chain
        plans = parents + [plan]; ids = {p['id'] for p in plans}; item = plan['item_id']
        related = lambda kind: self._related(db, kind, item)
        events = lambda kind: self._events(db, kind, item)
        self._same(related('teamwork_plan'), plans, '回合首边存在未重建方案或其它保存层。')
        self._same(related('work_unit_run'), [f['run'] for f in frames], '回合首边尚未开放后继运行或未知运行层。')
        reservations = events('cooperative_unit_reserved')
        self._same([payload for _, payload in reservations], [f['run'] for f in frames],
            '回合责任历史存在孤立、重复或未重建的实际保留事件。')
        if any(event['task_id'] != payload['task_id'] or event['attempt_id'] != payload['attempt_id']
                for event, payload in reservations):
            raise ValueError('回合实际保留事件必须绑定原运行的准确任务和尝试。')
        artifacts = [s['reference'] for f in frames for s in f['files']['snapshots']]
        self._same([self.s.work_artifacts.reference(r) for r in related('work_unit_artifact')],
            artifacts, '回合首边包含未核原快照或其它运行的成果。')
        # The original readers verify every canonical current pointer. An extra
        # pointer alias to one of these artifacts is also in this namespace.
        artifact_ids = {r['id'] for r in artifacts}
        run_ids = {f['run']['id'] for f in frames}
        for pointer in self.s.collaboration.all(db, 'work_unit_artifact_current'):
            if pointer.get('artifact_id') in artifact_ids and pointer['id'] not in {'unit-artifact-current:' + r for r in run_ids}:
                raise ValueError('回合首边包含额外原成果指针。')
        # Validate all live immutable responsibility versions even for a bounded
        # projection. A bound selects the historical value, never hides corrupt
        # later rows, orphan events or a stale live pointer.
        self.checkpoints._driver_history(db, parent, obs['events']['cutoff'])
        values = []; child_versions = []
        for row in related('work_driver_revision'):
            p = next((p for p in plans if p['id'] == row['plan_id']), None)
            if p is None: raise ValueError('回合责任引用未知方案。')
            value = old.old._driver(db, p, {'id': row['driver_id'],
                'generation': row['generation'], 'revision': row['revision']})
            seq = old._driver_event(db, value)
            if p == plan:
                successor_driver(value)
                child_versions.append((seq, value, row))
            elif seq > obs['events']['cutoff']:
                raise ValueError('冻结原父后责任历史仍有变化。')
            values.append(value)
        child_versions.sort(key=lambda v: v[0])
        registered = self.checkpoints._event(db, EVENTS[REGISTRATION], registration)['seq']
        if not child_versions or child_versions[0][1] != after:
            raise ValueError('回合责任缺少准确首次保存暂停版本。')
        for seq, value, _ in child_versions[1:]:
            kind = db.execute('SELECT kind FROM events WHERE seq=?', (seq,)).fetchone()['kind']
            if kind not in {'work_driver_resume', 'work_driver_pause', 'work_driver_restart_paused', 'work_driver_release', 'work_driver_execution_linked'} or seq <= registered:
                raise ValueError('未运行回合仅接受登记后的恢复、暂停、重启和释放。')
        old._driver_transitions(db, child_versions)
        latest = child_versions[-1][1]
        if (self.s.teamwork.current(db, item) != plan or
                self.s.work_drivers.current(db, item) != latest or
                self.s.work_units.authorization(db, latest) != auth or
                self.s.work_items.item(db, item)['revision'] != latest['item_revision'] or
                related('work_driver') != [latest] or related('work_unit_authorization') != [auth]):
            raise ValueError('回合责任当前指针、任务、责任或原授权不符。')
        self._same([v for _, v in events('work_driver_coordinator_handoff_round_revised')],
            [{**after, 'no_dispatch': True}], '回合首边须有唯一实际新责任事件。')
        driver_events = []
        for kind in {r[0] for r in db.execute("SELECT DISTINCT kind FROM events WHERE kind LIKE 'work_driver_%'")}:
            driver_events.extend(v for _, v in events(kind))
        self._same(driver_events, [{**v, 'no_dispatch': True} for v in values], '回合首边全部责任事件存在孤立或遗漏。')
        old_auths = []
        for p in parents:
            versions = [r for r in related('work_unit_authorization_revision') if r['plan_id'] == p['id']]
            if len(versions) != 1: raise ValueError('每个原父层须有唯一完整授权版本。')
            a = old.old._auth(db, p, {'id': 'unit-auth:' + versions[0]['driver_generation'], 'revision': versions[0]['revision']})
            old_auths.append(a)
        if old_auths[-1] != obs['history']['authorization']: raise ValueError('回合检查点原授权版本不符。')
        self._same(related('work_unit_authorization_revision'),
            [{**v, 'id': 'unit-auth-version:' + v['revision']} for v in old_auths + [auth]], '回合首边全层原授权有遗漏。')
        self._same([v for _, v in events('cooperation_authorized')], [old_auths[0]], '回合首边原授权事件不唯一。')
        # Exact record/event namespaces, including pre-existing old preview data.
        expected = {CHECKPOINT: cp, CANDIDATE: ready['candidate'], APPLICATION: application, REGISTRATION: registration}
        for protocol, row in expected.items():
            self._same(related(KIND_FOR[protocol]), [row], '回合首边包含未验证的明确登记数据。')
            self._same([v for _, v in events(EVENTS[protocol])], [row], '回合首边记录与实际事件不闭合。')
        self._same([self.checkpoints.live._event(e) for e, _ in events(SAVE_EVENT)], [save], '回合首边存在其它保存事件。')
        from . import work_coordinator_handoff_adjustment_records as ar
        from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
        old_edges = parent_path['edges'] if parent_path else []
        old_data = ar.WorkCoordinatorHandoffAdjustmentRecords(self.s)
        old_sources = WorkCoordinatorHandoffAdjustmentSources(self.s)
        for protocol in ar.KIND_FOR:
            rows = related(ar.KIND_FOR[protocol])
            for r in rows:
                old_data.get(db, protocol, old_data.ref(r))
                seq = old_sources._event(db, ar.EVENTS[protocol], r)
                if seq > obs['events']['cutoff']: raise ValueError('冻结父历史后出现额外旧调整数据。')
                if protocol == ar.CANDIDATE: old_sources.read_prepared(db, old_data.ref(r))
                elif protocol == ar.CHECKPOINT: old_sources.read_checkpoint(db, old_data.ref(r))
            self._same([v for _, v in events(ar.EVENTS[protocol])], rows, '旧调整全部记录事件有孤立或遗漏。')
            if protocol in (ar.APPLICATION, ar.CONSUMPTION):
                field = 'application' if protocol == ar.APPLICATION else 'consumption'
                self._same([old_data.ref(r) for r in rows], [e[field] for e in old_edges], '旧调整存在未核保存边。')
        self._same([self.checkpoints.live._event(e) for e, _ in events('coordinator_handoff_adjustment_plan_saved')],
            [e['save_event'] for e in old_edges], '旧调整保存事件存在遗漏。')
        for kind, name, event in ((DELIVERY_KIND, 'delivery', 'coordinator_handoff_delivered'),
                (COMPLETION_KIND, 'completion', 'coordinator_handoff_completed')):
            self._same(related(kind), h[name], '首边冻结后正式交付或完成发生变化。')
            self._same([v for _, v in events(event)], h[name], '全部交付/完成事件与记录不符。')
        self._check_proposals(db, plan, h)
        proposals = related('work_proposal')
        proposal_ids = {p['id'] for p in proposals}
        for kind in ('work_proposal_consumption', 'work_file_handoff_revision_consumption'):
            for row in self.s.collaboration.all(db, kind):
                body = row.get('snapshot', row)
                if (row.get('item_id') == item or body.get('item_id') == item or
                        row.get('proposal', {}).get('id') in proposal_ids or
                        body.get('proposal', {}).get('id') in proposal_ids or
                        row['id'] in {'proposal-consumption:' + p for p in proposal_ids}):
                    raise ValueError('回合原提案不得混入其它旧修订消费。')
        # Capabilities cannot silently expand after the frozen parent observation.
        for kind, event in (('work_coordinator_handoff_capability', 'coordinator_runtime_opened'),
                ('work_coordinator_handoff_adjustment_capability', 'coordinator_adjustment_opened')):
            self._same([v for _, v in events(event)], related(kind), '原能力记录与实际事件全集不符。')
            for row in related(kind):
                if self.checkpoints._event(db, event, row)['seq'] > obs['events']['cutoff']:
                    raise ValueError('回合首边尚未开放新增能力。')
        self.s.work_flows.require_stopped(db, item)
        if h['usage']['attempts'] > auth['max_attempts_total']:
            raise ValueError('ROUND全层原累计上限已增长。')
        return child_versions
