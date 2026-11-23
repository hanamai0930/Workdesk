"""Pure V3 values over one independently reconstructed historical boundary.

The enclosing catalog supplies original saved facts and SQL/frozen-byte frames.
This small value owner exposes only pure formatting and original SQL accounting;
it cannot select current resources, inspect live files, dispatch, or construct
any of the runtime/Current owners. It does not certify its own input facts.
"""
from copy import deepcopy
from types import SimpleNamespace

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_artifacts import WorkArtifacts
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_records import versioned, PERMISSIONS
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_assets import _json


def _reference(value, names):
    return {k: value[k] for k in names} if value is not None else None


class HistoricalValues:
    """One event boundary; instantiate afresh instead of mutating its source."""
    def __init__(self, sources, timeline, original, boundary, frames, records, material):
        if timeline.sources is not sources or not sources.db.in_transaction:
            raise ValueError('历史值须属于本次真实SQL来源和原时间线。')
        if type(boundary) is not int or sources.db.execute('SELECT 1 FROM events WHERE seq=?',
                (boundary,)).fetchone() is None:
            raise ValueError('历史值边界须为本次实际事件序号。')
        self.sources, self.timeline, self._db = sources, timeline, sources.db
        self.original = deepcopy(original)
        self.plan = original['child_plan']
        if self.plan not in timeline.plans:
            raise ValueError('历史值方案不属于实际原祖先。')
        self.boundary = boundary
        self.stage = 'POST_V3'
        self.frames, self.records, self.material = deepcopy(frames), deepcopy(records), deepcopy(material)
        self._root_plan = timeline.root
        self.driver = timeline.driver_before(self.plan, boundary)
        self._versions = [(e['seq'], driver, {**driver, 'id': 'driver-version:' + driver['revision'],
            'driver_id': driver['id']}) for e, driver in timeline.driver_versions(self.plan) if e['seq'] < boundary]
        self._auth = deepcopy(original['authorization'])
        sources.assets.baseline(material)
        # These are actual SQL methods and pure references, never availability
        # stand-ins. usage() replays each already-proved exit before summing.
        self.s = SimpleNamespace(collaboration=sources, store=sources,
            work_units=SimpleNamespace(auth_ref=lambda value: _reference(value, ('id', 'revision')),
                usage=lambda db, runs: sources.usage(runs, boundary)),
            work_drivers=SimpleNamespace(reference=lambda value: _reference(value, ('id', 'generation', 'revision'))),
            work_proposals=SimpleNamespace(reference=lambda value: _reference(value, ('id', 'version'))),
            work_artifacts=SimpleNamespace(require=lambda value, gate: WorkArtifacts.require(None, value, gate)))
        self.old = WorkCoordinatorHandoffContext(self.s)
        self.checkpoints = SimpleNamespace(_event=sources._event,
            live=SimpleNamespace(_event=self._event_value))

    @staticmethod
    def _event_value(row):
        return {'seq': row['seq'], 'kind': row['kind'], 'payload_digest': digest(_json(row['payload']))}

    @staticmethod
    def ref(value):
        return ref(value)

    def _driver_event(self, db, driver):
        if db is not self._db:
            raise ValueError('历史值不能跨连接重用。')
        matches = [event['seq'] for event, actual in self.timeline.driver_versions(self.plan) if actual == driver]
        if len(matches) != 1:
            raise ValueError('历史值须绑定准确实际责任事件。')
        return matches[0]

    _eligible = WorkCoordinatorHandoffMixedRuntime._eligible
    _admit = WorkCoordinatorHandoffMixedRuntime._admit
    _dependencies = WorkCoordinatorHandoffMixedRuntime._dependencies
    _phase = WorkCoordinatorHandoffRuntime._phase
    _forecast = WorkCoordinatorHandoffRuntime._forecast

    def history(self):
        plan = self.plan
        phase = self._phase(plan, self._eligible(plan, self.frames), self.records)
        return {**deepcopy(self.original), 'task': {'id': plan['item_id'], 'revision': self.driver['item_revision']},
            'driver': deepcopy(self.driver), 'frames': deepcopy(self.frames),
            'current_frames': self._eligible(plan, self.frames), 'phase': phase,
            'material_observation': deepcopy(self.material),
            'usage': self.sources.usage([f['run'] for f in self.frames], self.boundary)}

    def first_observation(self, prefix, unit, observed_at):
        from .work_coordinator_handoff_post_round_v3_successor_context import values
        if self.driver != prefix['driver']:
            raise ValueError('首次历史观察须消费准确原责任前缀尾。')
        return values(self, self._db, self.original, self.frames, prefix, unit, observed_at)

    def first_input(self, capability):
        """Original V9 codec, distinct from every later context and receipt."""
        from . import work_coordinator_handoff_post_round_v3_successor_contracts as c
        c.validate_capability(capability)
        obs = capability['snapshot']['observation']
        plan, frames, driver, auth = self.plan, self.frames, self.driver, self._auth
        unit = obs['selected_unit']['definition']
        task = {'id': plan['item_id'], 'revision': driver['item_revision']}
        if (unit not in plan['units'] or unit['role'] != 'coordinate' or obs['plan_snapshot'] != plan or
                driver != obs['driver'] or auth != obs['authorization'] or task != obs['task'] or
                [self.old.frontier_entry(f) for f in frames] != obs['frontier'] or
                self.material != obs['material_observation']):
            raise ValueError('首次V9须绑定独立重建的准确原历史边界。')
        deps = self._admit(plan, unit, frames, obs['phase'])
        if deps != obs['dependencies']:
            raise ValueError('首次V9依赖不等于独立历史来源。')
        old = self.old._context(plan, task, driver, auth, self._eligible(plan, frames), self.material)
        context = c.validate_context(versioned({'protocol': c.PROTOCOL, 'scope': c.SCOPE,
            'root': obs['root'], 'marker': plan['coordinator_file_handoff_root'], 'plan': plan_ref(plan),
            'task': task, 'driver': driver, 'authorization': auth, 'phase': obs['phase'],
            'saved_path': c.path_for(obs, capability), 'prior_frontier': obs['frontier'],
            'material_observation': self.material, 'selected_unit': obs['selected_unit'],
            'work_requirements': plan['work_requirements'], 'configuration': old['configuration'],
            'units': [{**entry, 'qualification': q['qualification']}
                for entry, q in zip(old['units'], obs['qualifications'])],
            'allowed_actions': c.ACTIONS, **PERMISSIONS}), capability)
        forecast = self._forecast(plan, context, frames, unit)
        forecast.update(scope=c.SCOPE, allowed_actions=c.ACTIONS,
            previous_round=deepcopy(plan['coordination_revision']))
        return deepcopy({'item_id': plan['item_id'], 'item_revision': task['revision'],
            'plan_id': plan['id'], 'plan_version': plan['version'], 'work_requirements': plan['work_requirements'],
            'unit': unit, 'dependencies': deps,
            'check_evidence': [{'unit_key': d['unit_key'], 'artifact': d['file_artifacts']['reference'],
                'check_status': d['check_status']} for d in deps],
            'file_artifacts': [{'unit_key': d['unit_key'], **d['file_artifacts']}
                for d in deps if d['file_artifacts']['state'] == 'CURRENT'],
            'handoff_coordination_context': context, 'coordination_context': forecast})

    def continuation_observation(self, prefix, unit, first, receipt, proposal, request, observed_at):
        from .work_coordinator_handoff_post_round_v3_continuation_context import observation
        return observation(self, self._db, self.original, self.frames, prefix, self.driver, self.records,
            unit, self.material, first, receipt, proposal, request, observed_at)

    def continuation_input(self, prefix, unit, first, continuation, proposal):
        from .work_coordinator_handoff_post_round_v3_continuation_context import path, input_value, intent
        saved = path(self, self._db, self.original, self.frames, prefix, self.driver, self.records,
            self.material, first, continuation)
        frozen = input_value(self, self.plan, unit,
            {'id': self.plan['item_id'], 'revision': self.driver['item_revision']},
            self.driver, self._auth, self.frames, self.material, self.records, saved, proposal)
        if unit['key'] == self.plan['file_handoffs'][0]['target_unit']:
            frozen['handoff_delivery_intent'] = intent(self, self.plan, frozen, self.frames)
        return frozen

    def proposal_snapshot(self, request, previous):
        from .work_coordinator_handoff_post_round_v3_continuation_proposals import snapshot
        return snapshot(self, self.history(), request, previous)

    @staticmethod
    def prompt(frozen):
        from . import work_coordinator_handoff_post_round_v3_successor_contracts as first
        from . import work_coordinator_handoff_post_round_v3_continuation_contracts as later
        from .work_coordinator_handoff_runtime import UNIT_PROMPT
        context = frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
        if context['protocol'] not in {first.PROTOCOL, later.COORDINATION, later.UNIT}:
            raise ValueError('V3历史prompt不能接纳其它冻结协议。')
        return ('读取完整冻结协调输入，只返回候选的 dispatch_schema 或 pause_schema 对应严格 JSON；不写文件。\n输入快照 JSON：\n'
            if frozen['unit']['role'] == 'coordinate' else UNIT_PROMPT) + canonical(frozen)
