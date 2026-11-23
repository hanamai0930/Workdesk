"""Independent event-bounded replay of actual successive ROUND layers."""
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
from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
from .work_coordinator_handoff_mixed_records import (
    CHECKPOINT, CANDIDATE, APPLICATION, REGISTRATION, SAVE_EVENT, KIND_FOR, EVENTS,
    event_ref, record_ref, PAUSE_REASON, WorkCoordinatorHandoffMixedRecords)
from .work_coordinator_handoff_mixed_contracts import (
    successor_plan, successor_driver, transferred_authorization, adjacent)
from .work_coordinator_handoff_mixed_checkpoints import WorkCoordinatorHandoffMixedCheckpoints
from .work_coordinator_handoff_round_path import WorkCoordinatorHandoffRoundPath

PATH = 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_RUNTIME_PATH_V1'
PROTOCOL = 'COORDINATOR_FILE_HANDOFF_COORDINATION_INPUT_V5'
UNIT = 'COORDINATOR_FILE_HANDOFF_UNIT_INPUT_V4'
KIND = 'work_coordinator_handoff_mixed_round_capability'
CAPABILITY = 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_RUNTIME_CAPABILITY_V1'
OPENED = 'coordinator_mixed_round_runtime_opened'
SCOPE = 'COMPLETE_AUTHORIZED_MIXED_ROUND_PATH; DISPATCH_PAUSE_ONLY; POST_ROUND_ADJUSTMENT_NOT_OPEN'
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
        raise ValueError('ROUND冻结输入须使用准确V5或普通V4协议。')
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
        if value['allowed_actions'] != ['dispatch', 'pause']: raise ValueError('V5仅开放派发与暂停。')
        from .work_coordinator_handoff_roots import CONFIG_FIELDS
        fields(value['configuration'], CONFIG_FIELDS)
        if not isinstance(value['units'], list) or len(value['units']) != len(path['current_qualifications']):
            raise ValueError('V5须保存完整原声明单元和准确资格。')
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
            raise ValueError('普通V4必须消费准确新V5派发提案。')
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
    from .work_coordinator_handoff_mixed_records import prior_check
    from .work_coordinator_handoff_round_runtime import PATH as FIRST_PATH, validate_path as first_path
    if value['parent_kind'] != 'ROUND': raise ValueError('Mixed后继必须有准确ROUND父。')
    nested = value['parent_path']
    old = first_path(nested) if nested.get('protocol') == FIRST_PATH else validate_path(nested)
    parent = old['plan']
    if old['root'] != value['root']: raise ValueError('Mixed父路径原根不符。')
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
            versions[0]['event']['kind'] != 'work_driver_coordinator_handoff_mixed_round_revised' or
            versions[0]['event']['seq'] >= e['save_event']['seq'] or
            any(v['plan_id'] != value['plan']['id'] or v['plan_version'] != value['plan']['version'] for v in (driver, auth)) or
            auth['transfer_registration'] != e['candidate'] or driver['generation'] != auth['driver_generation']):
        raise ValueError('ROUND运行路径责任、授权或保存暂停起点不符。')
    source(value['source'], full=True); prior_check(value['prior_check'])
    return deepcopy(value)


class WorkCoordinatorHandoffMixedRuntime(WorkCoordinatorHandoffAdjustmentPath):
    def __init__(self, service):
        super().__init__(service)
        self.data = WorkCoordinatorHandoffMixedRecords(service)
        self.checkpoints = WorkCoordinatorHandoffMixedCheckpoints(service)
        self.namespace = WorkCoordinatorHandoffRoundPath(service)

    def _related(self, db, kind, item):
        return [v for v in self.s.collaboration.all(db, kind) if self._belongs(v, item)]

    def _events(self, db, kind, item):
        rows = []
        for event in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,)):
            value = json.loads(event['payload'])
            if not (self._belongs(value, item) or event['task_id'] in self._task_ids or event['attempt_id'] in self._attempt_ids): continue
            if kind not in {'cooperative_unit_reserved', 'attempt_result_saved', 'cooperative_files_checked', 'coordinator_proposal_checked', 'coordinator_handoff_delivered', 'coordinator_handoff_completed'} and (event['task_id'] is not None or event['attempt_id'] is not None):
                raise ValueError('Mixed保存、能力和责任事件不得伪装成运行事件。')
            rows.append((event, value))
        return rows

    def _belongs(self, value, item):
        """Attribute actual nested identities even when outer labels lie."""
        pending = [value]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if (node.get('item_id') == item or node.get('plan_id') in self._plan_ids or
                        node.get('id') in self._plan_ids): return True
                for key in ('id', 'plan_id', 'plan_version', 'run_id', 'task_id', 'attempt_id', 'driver_id',
                        'driver_revision', 'driver_generation', 'generation', 'artifact_id', 'proposal_id',
                        'version', 'revision', 'input_version', 'input_template_version'):
                    ref = node.get(key)
                    if isinstance(ref, str) and ref in self._identity_ids: return True
                pending.extend(node.values())
            elif isinstance(node, list): pending.extend(node)
        return False

    def _frame_identities(self, frames):
        for frame in frames:
            self._identity_ids.update(frame['run'][k] for k in ('id', 'task_id', 'attempt_id'))
            self._identity_ids.update(frame['run'][k] for k in ('generation', 'driver_revision', 'input_version'))
            self._identity_ids.update(frame['run']['authorization'].values())
            self._task_ids.add(frame['run']['task_id']); self._attempt_ids.add(frame['run']['attempt_id'])
            for snapshot in frame['files']['snapshots']: self._identity_ids.update(snapshot['reference'].values())
            intent = frame['run']['input'].get('handoff_delivery_intent')
            if intent: self._identity_ids.add(intent['input_template_version'])

    _same = staticmethod(WorkCoordinatorHandoffRoundPath._same)

    def _proposal_events(self, db, item_id, boundary=None):
        rows = self._events(db, 'coordinator_proposal_checked', item_id)
        for event, row in rows:
            source = row['snapshot']['source']
            if event['task_id'] != source['task_id'] or event['attempt_id'] != source['attempt_id']:
                raise ValueError('Mixed提案检查事件须绑定实际来源任务和尝试。')
        return [(e['seq'], v) for e, v in rows if boundary is None or e['seq'] < boundary]

    def _base(self, db, plan, ready=None, parent_reader=None):
        self.checkpoints._guard(db, plan)
        successor_plan(plan)
        if self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan:
            raise ValueError('ROUND运行须绑定真实完整方案。')
        if ready is None:
            ready = self.checkpoints._candidate_once(db, plan['coordinator_file_handoff_round_lineage']['candidate'])
            parent_reader = self.checkpoints._parent_reader
        obs = ready['observation']; parent = ready['checkpoint']['snapshot']['plan']
        parents = ready['plans']
        self._parents = parents; self._root_plan = parents[0]; self._plan_ids = {p['id'] for p in parents + [plan]}
        self._identity_ids = self._plan_ids | {plan['item_id'], obs['history']['driver']['id'], obs['history']['authorization']['id']}
        self._identity_ids.update(p['version'] for p in parents + [plan])
        self._identity_ids.update(obs['history']['driver'][k] for k in ('generation', 'revision'))
        self._identity_ids.add(obs['history']['authorization']['revision'])
        self._task_ids = set(); self._attempt_ids = set()
        self._frame_identities(ready['frames'])
        self._layers = getattr(parent_reader, '_layers', []) + [parent_reader]
        apps = [r for r in self._related(db, KIND_FOR[APPLICATION], plan['item_id']) if r['snapshot']['child_plan'] == plan_ref(plan)]
        regs = [r for r in self._related(db, KIND_FOR[REGISTRATION], plan['item_id']) if r['snapshot']['to_plan'] == plan_ref(plan)]
        if len(apps) != 1 or len(regs) != 1: raise ValueError('ROUND运行须有唯一实际应用和登记。')
        app = self.data.get(db, APPLICATION, self.ref(apps[0])); reg = self.data.get(db, REGISTRATION, self.ref(regs[0]))
        b = app['snapshot']; after = self.old._driver(db, plan, b['driver_after']); auth = self.old._auth(db, plan, b['authorization_after'])
        h = obs['history']; cp = ready['checkpoint']; candidate = ready['candidate']
        adjacent(cp, candidate, plan, h['authorization'], auth, h['driver'], after)
        from .work_coordinator_handoff_mixed_apply import bodies, save_payload
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
        rows = [r for r in self._related(db, KIND, plan['item_id']) if r.get('snapshot', {}).get('plan') == plan_ref(plan)]
        events = [(e, v) for e, v in self._events(db, OPENED, plan['item_id']) if v.get('snapshot', {}).get('plan') == plan_ref(plan)]
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
            if proposal is None: raise ValueError('普通V4输入须有准确新V5派发提案。')
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
            if not row or any(r.get('proposal') == self.ref(row) for r in rows): raise ValueError('新V5派发提案缺失或已消费。')
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
        from .work_coordinator_handoff_mixed_intent import PROTOCOL as INTENT, SCOPE as INTENT_SCOPE, validate_intent
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
        h, _ = self._walk(db, plan, audit_namespace=True)
        h.update(full_mixed_path_verified=True, successor_layers_verified=True,
            verified_scope=SCOPE, public_apply=False)
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
            for layer in self._layers:
                self.s.work_file_rework_applications.require_baseline(
                    layer._ready['observation']['history']['material_observation'])
            # Elapsed time is a live display observation, outside the frozen
            # path/input proof. RESERVED attempts accrue time between reads.
            comparable = lambda v: {**v, 'usage': {k: x for k, x in v['usage'].items()
                if k != 'elapsed_observed_seconds'}}
            if comparable(first) != comparable(again) or before != db.total_changes:
                changed = [k for k in first if comparable(first)[k] != comparable(again).get(k)]
                raise ValueError('ROUND完整两次重建或数据库发生变化：' + ','.join(changed))
            return deepcopy(again)

    def _scope(self, db, plan, ready, application, registration, after, auth, save, parent_path, frames, h):
        from .work_coordinator_handoff_mixed_audit import audit
        return audit(self, db, plan, h)

    def _walk(self, db, plan, boundary=None, material=None, *, audit_namespace=False):
        '''Replay actual ancestors once in order; no recursive complete proof cache.'''
        from .work_coordinator_handoff_mixed_records import LINEAGE
        from .work_coordinator_handoff_mixed_replay import FirstRoundReplay
        maximum = self.checkpoints._guard(db, plan)
        pending = []; seen = set(); parent = plan; bound = boundary; frozen_material = material
        while parent.get('coordinator_file_handoff_round_lineage', {}).get('protocol') == LINEAGE:
            if parent['id'] in seen or len(pending) >= maximum:
                raise ValueError('Mixed实际检查点祖先循环或超过最初授权次数。')
            seen.add(parent['id'])
            ref = parent['coordinator_file_handoff_round_lineage']['candidate']
            candidate = self.data.get(db, CANDIDATE, ref)
            cp = self.data.get(db, CHECKPOINT, candidate['snapshot']['checkpoint'])
            pending.append((parent, ref, cp, bound, frozen_material))
            bound = self.checkpoints._event(db, EVENTS[CHECKPOINT], cp)
            frozen_material = cp['snapshot']['observation']['history']['material_observation']
            parent = cp['snapshot']['plan']
        if not pending: raise ValueError('Mixed遍历必须包含实际新协议保存层。')
        reader = FirstRoundReplay(self.s)
        h, actual = reader._walk(db, parent, bound, frozen_material)
        for child, ref, cp, bound, frozen_material in reversed(pending):
            helper = WorkCoordinatorHandoffMixedCheckpoints(self.s)
            source = helper._from_history(db, helper.data.ref(cp), reader, h, actual)
            ready = helper._candidate_from_source(db, ref, source)
            layer = self if child['id'] == plan['id'] else WorkCoordinatorHandoffMixedRuntime(self.s)
            h, actual = layer._walk_layer(db, child, bound, frozen_material, ready, reader,
                audit_namespace=audit_namespace and child['id'] == plan['id'])
            reader = layer
        return h, actual

    def _walk_layer(self, db, plan, boundary, material, ready, parent_reader, *, audit_namespace=False):
        '''One actual saved layer, using the independently rebuilt preceding layer.'''
        self._db = db
        ready, app, reg, start, auth, edge = self._base(db, plan, ready, parent_reader)
        self._ready, self._edge_value, self._auth = ready, edge, auth
        self._versions = self._drivers(db, plan, start, edge['registration_event']['seq'])
        limit = boundary['seq'] if boundary else float('inf')
        versions = [v for v in self._versions if v[0] < limit]
        if not versions: raise ValueError('ROUND父截止之前没有真实保存责任。')
        driver = versions[-1][1]
        self._round = self._round_capability(db, plan, ready, edge, self._versions)
        opened = self.checkpoints._event(db, OPENED, self._round)['seq'] if self._round else None
        if opened is not None and opened >= limit:
            raise ValueError('ROUND父运行能力晚于真实检查点。')
        cap, activated = self.capability(db, plan)
        self._inherited = ready['frames']
        rows = [r for r in self.s.work_units.rows(db, plan['item_id']) if r['plan_id'] == plan['id']]
        if rows and (self._round is None or any(self.sources._reservation_event(db, r) <= opened for r in rows)):
            raise ValueError('ROUND父实际运行缺少派发前的独立能力。')
        if rows and (rows[0]['command_id'] != self._round['command_id'] or rows[0]['created'] < self._round['created']):
            raise ValueError('ROUND父能力与首条运行必须属于同一实际命令。')
        if any(self.sources._reservation_event(db, r) >= limit for r in rows):
            raise ValueError('冻结ROUND父之后出现额外父层运行。')
        helper = WorkCoordinatorHandoffAdjustmentSources(self.s)
        records = helper._records_before(db, self._root_plan, limit)
        frames, revision, _ = self._layer_runs(db, plan, ready['observation']['history']['task'],
            self._root_plan[MARKER_FIELD]['project_root'], cap, activated, driver, auth,
            [r for _, _, r in versions], rows, records)
        if revision != driver['item_revision']:
            raise ValueError('ROUND父全部原任务绑定没有到达截止责任。')
        # Exit/capture events belong to the frozen parent history, not merely
        # reservations. A cutoff must never hide a later result or snapshot.
        if boundary:
            for frame in frames:
                run = frame['run']; attempt = self.s.store.attempt(run['attempt_id'], db)
                self.checkpoints._event(db, 'attempt_result_saved', attempt['result'], limit - 1,
                    run['task_id'], run['attempt_id'])
                if any(e['seq'] >= limit for e in db.execute(
                        "SELECT seq FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked'",
                        (run['task_id'], run['attempt_id']))):
                    raise ValueError('冻结ROUND父之后原快照历史仍有变化。')
        self._match_root_records(db, self._root_plan, frames, records)
        phase = self._phase(plan, frames, records)
        current = deepcopy(frames)
        if boundary is None:
            for frame in current: frame['source_evidence'] = self.s.work_units.evidence(db, frame['run'])
            expected = (plan['file_handoffs'][0]['target_unit'] if phase == 'B_COMPLETED' else
                plan['file_handoffs'][0]['source_unit'] if phase == 'A_CAPTURED' else None)
            if expected and next(f for f in reversed(current) if f['run']['unit_key'] == expected)['source_evidence']['file_artifacts']['state'] != 'CURRENT':
                phase = 'BLOCKED'
        files = self.s.work_file_rework_applications
        actual = files.observe(self._root_plan[MARKER_FIELD]['project_root'], plan['file_handoffs'][0]['paths'])
        if boundary:
            if material is None: raise ValueError('历史ROUND父必须保留当时准确材料基线。')
            files.require_baseline(material)
        else: material = actual
        from .work_coordinator_handoff_adjustments import qualifications
        eligible = self._eligible(plan, current)
        h = {'protocol': READ, 'root': plan_ref(self._root_plan), 'marker': self._root_plan[MARKER_FIELD],
            'capability': self.ref(cap), 'adjustment_capability': ready['observation']['history']['adjustment_capability'],
            'round_capability': self.ref(self._round) if self._round else None,
            'task': {'id': plan['item_id'], 'revision': revision}, 'driver': driver, 'authorization': auth,
            'frames': frames, 'current_frames': eligible, 'phase': phase,
            'frontier': [self.old.frontier_entry(f) for f in frames], 'material_observation': deepcopy(material),
            'delivery': [r for r, _ in records[DELIVERY_KIND]], 'completion': [r for r, _ in records[COMPLETION_KIND]],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))], **PERMISSIONS}
        h['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        if audit_namespace:
            # Audit every live version/pointer even for a historical prefix.
            self._scope(db, plan, ready, app, reg, start, auth, edge['save_event'],
                ready['observation']['saved_path'], h['frames'], h)
        return h, actual
