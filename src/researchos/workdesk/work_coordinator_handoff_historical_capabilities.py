"""Original capability activation prefixes over independently replayed frames.

Only immutable SQL rows and frozen assets are read. Admission observations are
generated from the original boundary; no stored capability observation supplies
history, paths, selected definitions, dependencies or original request values.
"""
from copy import deepcopy
from types import SimpleNamespace

from .store import digest
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_roots import CONFIG_FIELDS
from .work_coordinator_handoff_records import fields, versioned, PERMISSIONS, UNIT_INPUT
from .work_coordinator_handoff_adjustment_records import finite
from .work_coordinator_handoff_adjustments import qualifications, protected
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_assets import _uuid


class HistoricalCapabilities:
    def __init__(self, sources, timeline, ledger, replay):
        if (timeline.sources is not sources or ledger.sources is not sources or
                replay.sources is not sources or not sources.db.in_transaction):
            raise ValueError('原能力观察须属于本次真实来源、完整重放和事务。')
        self.sources, self.timeline, self.ledger, self.replay = sources, timeline, ledger, replay
        self.db = sources.db
        self.admitted = {}
        self.slots = set()

    def _event(self, row, kind):
        fields(row, {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'})
        finite(row['created']); _uuid(row['command_id'])
        event = self.sources._event(self.db, kind, row)
        if row['item_id'] != self.timeline.item_id or row['created'] > event['created']:
            raise ValueError('原能力wrapper须保持真实业务、原命令及事件时间。')
        return event

    def _save(self, row, kind, event_kind, expected, *, owner):
        event = self._event(row, event_kind)
        identity = expected.get('plan') or expected.get('observation', {}).get('plan') or expected.get('root')
        slot = (kind, identity['id'])
        if slot in self.slots:
            raise ValueError('同一原方案的同代能力不能重复开启。')
        if (row['snapshot'] != expected or row['version'] != expected['version'] or
                row['id'] != kind + ':' + expected['version']):
            raise ValueError('原能力完整值不等于独立重建的开启观察。')
        self.ledger.row(kind, row, owner)
        self.ledger.event_at(event['seq'], event_kind, row, owner)
        self.admitted[row['id']] = deepcopy(row)
        self.slots.add(slot)
        return deepcopy(row)

    def _requires(self, *rows):
        for row in rows:
            if row is not None and self.admitted.get(row['id']) != row:
                raise ValueError('后续原能力只能引用本次已独立重建的先前能力。')

    def _boundary(self, values, event):
        frames = self.replay.frames_before(event['seq'])
        if (values.sources is not self.sources or values.timeline is not self.timeline or
                values.boundary != event['seq'] or values.frames != frames or
                self.timeline.business_before(event['seq'])['revision'] != values.driver['item_revision']):
            raise ValueError('原能力须消费完整实际较早运行及准确业务责任边界。')
        # Capability codecs cannot turn decoded responsibility versions into
        # an admitted original responsibility chain.
        for de, driver in self.timeline.driver_versions(values.plan):
            if de['seq'] >= event['seq']:
                continue
            carrier = {**driver, 'id': 'driver-version:' + driver['revision'], 'driver_id': driver['id']}
            actual = self.ledger.rows.get(('work_driver_revision', carrier['id']))
            proved = self.ledger.events.get(de['seq'])
            if actual is None or actual[0] != carrier or proved is None:
                raise ValueError('能力观察缺少本次已证明的完整原责任前缀。')
        self.sources.assets.baseline(values.material)
        return frames

    def root(self, row):
        from . import work_coordinator_handoff_runtime as r
        plan = self.timeline.root
        facts = self.timeline.root_facts()
        event = self._event(row, 'coordinator_runtime_opened')
        frames = self.replay.frames_before(event['seq'])
        if (facts['event'] is not None and facts['event']['seq'] >= event['seq'] or
                row['created'] < plan['created']):
            raise ValueError('原runtime能力须晚于准确原根创建。')
        expected = versioned({'protocol': r.CAPABILITY, 'root': plan_ref(plan),
            'marker': plan['coordinator_file_handoff_root'],
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'activation_frontier': [WorkCoordinatorHandoffContext.frontier_entry(f) for f in frames],
            'coordination_protocol': r.PROTOCOL, 'unit_protocol': UNIT_INPUT,
            'allowed_actions': ['dispatch', 'pause'], **PERMISSIONS,
            'scope': 'INTERNAL_ORIGINAL_ROOT_SERIAL_FIRST_RUNS; SIX_REVISION_ENTRIES_NOT_OPEN'})
        return self._save(row, r.CAPABILITY_KIND, 'coordinator_runtime_opened', expected,
            owner='capability:' + row['id'])

    def adjustment(self, values, row):
        from . import work_coordinator_handoff_adjustment_runtime as r
        event = self._event(row, 'coordinator_adjustment_opened')
        frames = self._boundary(values, event)
        cap = values._caps['runtime']; self._requires(cap)
        if values.plan != self.timeline.root or values.stage != 'ROOT_V2' or values.driver['state'] != 'RESERVED':
            raise ValueError('原调整开启须消费未开启调整的准确可运行原根。')
        seen = {f['run']['unit_key'] for f in frames}
        eligible = [u for u in values.plan['units'] if u['role'] == 'coordinate' and u['key'] not in seen]
        phase = values._phase(values.plan, frames, values.records)
        reachable = []
        for unit in eligible:
            try:
                values._admit(values.plan, unit, frames, phase)
                reachable.append(unit)
            except ValueError:
                pass
        if (not reachable or len(frames) >= values._auth['max_attempts_total'] or
                not values.driver['updated'] <= row['created'] < values._auth['dispatch_before']):
            raise ValueError('原调整开启缺少可达首次协调、准确责任或原授权余量。')
        plan = values.plan
        expected = versioned({'protocol': r.CAPABILITY, 'root': plan_ref(plan),
            'marker': plan['coordinator_file_handoff_root'], 'runtime_capability': ref(cap),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'activation_plan': plan_ref(plan),
            'activation_frontier': [WorkCoordinatorHandoffContext.frontier_entry(f) for f in frames],
            'eligible_coordinate_keys': sorted(u['key'] for u in eligible),
            'coordination_protocol': r.PROTOCOL, 'unit_protocol': r.UNIT,
            'allowed_actions': r.ACTIONS, **PERMISSIONS, 'scope': r.SCOPE})
        return self._save(row, r.KIND, 'coordinator_adjustment_opened', expected,
            owner='capability:' + row['id'])

    def round(self, values, row, saved):
        if values.stage == 'FIRST_ROUND':
            from . import work_coordinator_handoff_round_runtime as r
        elif values.stage == 'MIXED_ROUND':
            from . import work_coordinator_handoff_mixed_runtime as r
        else:
            raise ValueError('原ROUND开启只能消费准确首ROUND或Mixed保存边。')
        event = self._event(row, r.OPENED)
        frames = self._boundary(values, event)
        self._requires(values._caps['runtime'], values._caps['adjustment'])
        edge = saved['edge']; obs = saved['observation']
        first = self._first_run(values.plan, row, event, frames)
        de = next(e for e, d in self.timeline.driver_versions(values.plan) if d == values.driver)
        if (saved['child_plan'] != values.plan or values.frames != saved['frames'] or
                values.driver['state'] != 'RESERVED' or
                not edge['registration_event']['seq'] < de['seq'] < event['seq'] or
                not values.driver['updated'] <= row['created'] < values._auth['dispatch_before'] or
                len(frames) >= values._auth['max_attempts_total']):
            raise ValueError('原ROUND开启须绑定原登记、真实恢复责任与完整继承前沿。')
        expected = versioned({'protocol': r.CAPABILITY, 'root': obs['root'], 'plan': plan_ref(values.plan),
            'runtime_capability': obs['history']['capability'],
            'adjustment_capability': obs['history']['adjustment_capability'],
            'registration': edge['registration'], 'registration_event': edge['registration_event'],
            'source': obs['source'], 'prior_check': obs['prior_check'],
            'activation_frontier': obs['history']['frontier'],
            'driver': {k: values.driver[k] for k in ('id', 'generation', 'revision')},
            'authorization': {k: values._auth[k] for k in ('id', 'revision')},
            'coordination_protocol': r.PROTOCOL, 'unit_protocol': r.UNIT,
            'allowed_actions': ['dispatch', 'pause'], 'scope': r.SCOPE, **PERMISSIONS})
        return self._save(row, r.KIND, r.OPENED, expected, owner='run:' + first['id'])

    def _first_run(self, plan, row, event, frames):
        found = []
        for run in self.sources.all(self.db, 'work_unit_run'):
            if run.get('plan_id') == plan['id']:
                actual, _, _, _, _, reserved = self.sources.run(run['id'])
                found.append((reserved['seq'], actual))
        found.sort(key=lambda entry: entry[0])
        if (not found or found[0][0] <= event['seq'] or
                found[0][1]['command_id'] != row['command_id'] or
                found[0][1]['created'] < row['created'] or
                found[0][1]['ordinal_total'] != len(frames) + 1):
            raise ValueError('原能力须与此方案首条实际运行同命令且先于保留。')
        return found[0][1]

    def post(self, values, row, *, saved=None):
        """Rebuild original V6, V7 or V8 activation and its nine-field request."""
        successor = values.stage in {'POST_V1', 'POST_V2'}
        if values.stage == 'POST_V2':
            from . import work_coordinator_handoff_post_round_v2_run_contracts as r
            from .work_coordinator_handoff_post_round_v2_successor_contracts import OBS_SCOPE as observation_scope
        elif values.stage == 'POST_V1':
            from . import work_coordinator_handoff_post_round_successor_contracts as r
            observation_scope = r.OBS_SCOPE
        elif values.stage in {'FIRST_ROUND', 'MIXED_ROUND'}:
            from . import work_coordinator_handoff_post_round_contracts as r
        else:
            raise ValueError('POST能力须使用准确原ROUND、POST_V1或POST_V2边界。')
        event = self._event(row, r.OPENED)
        frames = self._boundary(values, event)
        self._requires(*(values._caps[name] for name in ('runtime', 'adjustment', 'round', 'post')))
        plan, driver, auth = values.plan, values.driver, values._auth
        if successor:
            if (saved is None or saved['child_plan'] != plan or saved['frames'] != frames or
                    values._successor is not None):
                raise ValueError('后继开启须消费本次完整原保存边且尚无子运行。')
            base = saved['original']['saved_path']
            path = versioned({**{k: v for k, v in base.items() if k != 'version'},
                'driver': driver, 'authorization': auth, 'material_observation': values.material})
        else:
            path = values.history()['saved_path']
        first = self._first_run(plan, row, event, frames) if successor else self._first_after(values, row, event)
        unit = next(u for u in plan['units'] if u['key'] == first['unit_key'])
        if (unit['role'] != 'coordinate' or driver['state'] != 'RESERVED' or
                driver['generation'] != auth['driver_generation']):
            raise ValueError('POST首开启须为原声明协调单元及准确恢复责任。')
        phase = values._phase(plan, frames, values.records)
        dependencies = values._admit(plan, unit, frames, phase)
        common = {'root': plan_ref(self.timeline.root), 'plan': plan_ref(plan), 'plan_snapshot': plan,
            'task': {'id': plan['item_id'], 'revision': driver['item_revision']}, 'driver': driver,
            'authorization': auth, 'saved_path': path, 'phase': phase,
            'frontier': [WorkCoordinatorHandoffContext.frontier_entry(f) for f in frames],
            'material_observation': values.material, 'selected_unit': {'definition': unit, 'revision': digest(unit)},
            'dependencies': dependencies, 'usage': self.sources.usage([f['run'] for f in frames], event['seq']),
            'protected_keys': sorted(protected(plan['units'], plan['file_handoffs'][0])),
            'configuration_version': digest({k: plan[k] for k in CONFIG_FIELDS}),
            'observed_at': row['snapshot']['observation']['observed_at'], **PERMISSIONS}
        if successor:
            common.update(protocol=r.OBSERVATION, scope=observation_scope,
                qualifications=qualifications(plan, phase, values._eligible(plan, frames)))
            if values.stage == 'POST_V1':
                common.update({key: ref(values._caps[name]) if values._caps[name] is not None else None
                    for key, name in (('runtime_capability', 'runtime'), ('adjustment_capability', 'adjustment'),
                        ('round_capability', 'round'), ('post_round_capability', 'post'))})
            observation = r.validate_observation(versioned(common))
        else:
            from . import work_coordinator_handoff_post_round_context as c
            current = values._eligible(plan, frames)
            sources = [f for f in current if f['run']['plan_id'] == plan['id'] and
                f['run']['generation'] == driver['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
            latest = sources[-1] if sources else None
            relation = plan['file_handoffs'][0]
            common.update(protocol=c.OBSERVATION, scope=c.SCOPE,
                **{key: ref(values._caps[name]) if values._caps[name] is not None else None for key, name in
                    (('runtime_capability', 'runtime'), ('adjustment_capability', 'adjustment'), ('round_capability', 'round'))},
                previous_round_source=path['source'],
                phase_sources={name: next((f['source_evidence'] for f in reversed(current)
                    if f['run']['unit_key'] == relation[key]), None)
                    for name, key in (('source', 'source_unit'), ('target', 'target_unit'))},
                existing_source={'source': source_ref(latest['run'], latest['source_evidence']),
                    'evidence': latest['source_evidence'],
                    'input_protocol': self.replay.inputs[latest['run']['id']]['handoff_coordination_context']['protocol'],
                    'adjustment_eligible': False} if latest else None)
            observation = c.validate_observation(versioned(common))
        observed = observation['observed_at']; finite(observed)
        if not driver['updated'] <= observed <= row['created'] < auth['dispatch_before']:
            raise ValueError('原能力观察时间须位于准确责任和原最初截止之间。')
        request = self.replay.command_request(values, first, frames, post_activation=not successor)
        expected = versioned({'protocol': r.CAPABILITY, 'observation': observation,
            'coordination_protocol': r.PROTOCOL, 'unit_protocol': r.UNIT, 'allowed_actions': r.ACTIONS,
            'request': request, 'request_hash': digest(request), 'scope': r.SCOPE, **PERMISSIONS})
        return self._save(row, r.KIND, r.OPENED, expected, owner='run:' + first['id'])

    def _first_after(self, values, row, event):
        found = []
        for run in self.sources.all(self.db, 'work_unit_run'):
            if run.get('plan_id') == values.plan['id']:
                actual, _, _, _, _, reserved = self.sources.run(run['id'])
                if reserved['seq'] > event['seq']:
                    found.append((reserved['seq'], actual))
        found.sort(key=lambda entry: entry[0])
        if (not found or found[0][1]['command_id'] != row['command_id'] or
                found[0][1]['created'] < row['created'] or
                found[0][1]['ordinal_total'] != len(values.frames) + 1):
            raise ValueError('原POST开启须在同一真实unit-send命令首保留之前。')
        return found[0][1]
