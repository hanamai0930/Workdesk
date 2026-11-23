"""Original ancestor codecs over independently proved event-boundary values.

No runtime owner is constructed. The catalog owns admission of saved edges,
initial drivers, transitions, capabilities, records and the frozen material.
These value algorithms neither prove their arguments nor inspect live state.
"""
from copy import deepcopy
from types import SimpleNamespace

from .store import canonical, digest
from .work_transitions import plan_ref
from .work_artifacts import WorkArtifacts
from .work_coordinator_handoff_context import WorkCoordinatorHandoffContext
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime as Root
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath as Adjustment
from .work_coordinator_handoff_round_runtime import WorkCoordinatorHandoffRoundRuntime as First
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime as Mixed
from .work_coordinator_handoff_post_round_runtime import PostRoundRuntime as Post
from .work_coordinator_handoff_post_round_successor_runtime import WorkCoordinatorHandoffPostRoundSuccessorRuntime as V7
from .work_coordinator_handoff_post_round_v2_runtime import WorkCoordinatorHandoffPostRoundV2Runtime as V8
from .work_coordinator_handoff_records import PERMISSIONS, versioned
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_historical_assets import _json


CODECS = {'ROOT_V2': Root, 'ADJUSTMENT': Adjustment, 'FIRST_ROUND': First,
    'MIXED_ROUND': Mixed, 'POST_ROUND': Post, 'POST_V1': V7, 'POST_V2': V8}


class HistoricalAncestorValues:
    """One immutable historical boundary; caller supplies proved saved facts."""
    def __init__(self, sources, timeline, plan, boundary, *, stage, frames, records,
            material, authorization, capabilities, saved=None, adjustment_edges=()):
        if (timeline.sources is not sources or not sources.db.in_transaction or
                plan not in timeline.plans or type(boundary) is not int or
                sources.db.execute('SELECT 1 FROM events WHERE seq=?', (boundary,)).fetchone() is None or
                stage not in {*CODECS, 'ROOT_V1', 'ROOT_ADJUSTMENT'}):
            raise ValueError('祖先值须属于本次原方案、真实事件边界和明确原codec。')
        self.sources, self.timeline, self._db = sources, timeline, sources.db
        self.plan, self.boundary, self.stage = deepcopy(plan), boundary, stage
        self._root_plan = deepcopy(timeline.root)
        self.frames, self.records, self.material = deepcopy(frames), deepcopy(records), deepcopy(material)
        self.driver = timeline.driver_before(plan, boundary)
        self._auth = deepcopy(authorization)
        self._caps = {key: None for key in ('runtime', 'adjustment', 'round', 'post', 'successor')}
        self._caps.update(deepcopy(capabilities))
        self._ready = deepcopy(saved)
        self._edge_value = deepcopy(saved['edge']) if saved is not None else None
        self._edges = deepcopy(list(adjustment_edges))
        self._round = self._caps.get('round')
        self._post = self._caps.get('post')
        self._successor = self._caps.get('successor')
        self._adjustment = self._caps.get('adjustment')
        self._versions = [(e['seq'], d, {**d, 'id': 'driver-version:' + d['revision'], 'driver_id': d['id']})
            for e, d in timeline.driver_versions(plan) if e['seq'] < boundary]
        self.s = SimpleNamespace(collaboration=sources, store=sources,
            work_artifacts=SimpleNamespace(require=lambda value, gate: WorkArtifacts.require(None, value, gate)))
        self.old = WorkCoordinatorHandoffContext(self.s)
        self.checkpoints = SimpleNamespace(live=SimpleNamespace(_event=lambda raw: {
            'seq': raw['seq'], 'kind': raw['kind'], 'payload_digest': digest(_json(raw['payload']))}))
        if self._ready is not None:
            lineage = plan.get('coordinator_file_handoff_round_lineage', {}).get('protocol')
            self.legacy = Mixed if lineage == 'COORDINATOR_FILE_HANDOFF_MIXED_ROUND_LINEAGE_V1' else First
        sources.assets.baseline(material)

    @staticmethod
    def ref(value):
        return ref(value)

    def _driver_event(self, db, driver):
        if db is not self._db:
            raise ValueError('祖先值不能跨连接复用。')
        actual = [e['seq'] for e, d in self.timeline.driver_versions(self.plan) if d == driver]
        if len(actual) != 1 or actual[0] >= self.boundary:
            raise ValueError('祖先值责任须为本次边界前准确不可变版本。')
        return actual[0]

    def _eligible(self, plan, frames):
        return frames if plan == self._root_plan else Mixed._eligible(self, plan, frames)

    def _dependencies(self, plan, unit, frames, phase, pending=None):
        return Root._dependencies(self, plan, unit, self._eligible(plan, frames), phase, pending)

    def _admit(self, plan, unit, frames, phase, pending=None):
        if plan != self._root_plan and unit['key'] in plan['historical_coordinators']:
            raise ValueError('原历史协调单元不能成为新的首次运行。')
        return Root._admit(self, plan, unit, self._eligible(plan, frames), phase, pending)

    _phase = Root._phase
    _forecast = Root._forecast
    _path = Adjustment._path
    _new_path = Post._new_path

    def _path_value(self, plan, frames, material, phase, driver):
        if self.stage == 'POST_ROUND':
            return self._new_path(plan, frames, material, phase, driver)
        codec = CODECS.get(self.stage)
        if codec not in (First, Mixed, V7, V8):
            raise ValueError('原codec不含此动态责任保存路径。')
        return codec._path_value(self, plan, frames, material, phase, driver)

    def input(self, unit, *, proposal=None):
        """Generate from prior frames; run.input is never an argument."""
        plan, driver, auth = self.plan, self.driver, self._auth
        if unit not in plan['units'] or driver['state'] != 'RESERVED':
            raise ValueError('祖先派发值须绑定原声明单元及原可运行责任。')
        task = {'id': plan['item_id'], 'revision': driver['item_revision']}
        if self.stage == 'ROOT_V1':
            self.old._coordinator(unit)
            # V1 stores full evidence in its frontier; every later codec uses
            # raw evidence. Preserve this original format exception exactly.
            prior = [{**deepcopy(f), 'evidence': deepcopy(f['source_evidence'])} for f in self.frames]
            context = self.old._context(plan, task, driver, auth, prior, self.material)
            return self.old._input(plan, unit, context)
        args = (plan, unit, task, driver, auth, self.frames, self.material,
            self.records, self._caps['runtime'], proposal)
        if self.stage == 'ROOT_ADJUSTMENT':
            # AdjustmentRuntime._input uses super(); reproduce only its pure
            # extension over the original Root value, without an owner object.
            from .work_coordinator_handoff_adjustment_runtime import PROTOCOL, UNIT, SCOPE, ACTIONS, validate_unit
            from .work_coordinator_handoff_adjustments import input_context
            frozen = Root._input(self, *args)
            field = 'handoff_coordination_context' if unit['role'] == 'coordinate' else 'handoff_unit_context'
            context = {k: v for k, v in frozen[field].items() if k != 'version'}
            context.update(protocol=PROTOCOL if unit['role'] == 'coordinate' else UNIT,
                adjustment_capability=ref(self._adjustment), plan=plan_ref(plan), saved_path=None, scope=SCOPE)
            if unit['role'] == 'coordinate':
                context['allowed_actions'] = ACTIONS
            frozen[field] = context = versioned(context)
            if unit['role'] == 'coordinate':
                frozen['coordination_context'].update(allowed_actions=ACTIONS, scope=SCOPE,
                    handoff_context={k: context[k] for k in ('protocol', 'version', 'root')},
                    adjustment_context=input_context(plan, context, self.frames),
                    adjustment_prior_frontier=context['prior_frontier'])
            else:
                validate_unit(context)
        else:
            frozen = CODECS[self.stage]._input(self, *args)
        if unit['key'] == plan['file_handoffs'][0]['target_unit']:
            codec = Root if self.stage == 'ROOT_ADJUSTMENT' else CODECS[self.stage]
            frozen['handoff_delivery_intent'] = codec._intent(self, plan, frozen, self.frames)
        return deepcopy(frozen)

    def prompt(self, frozen):
        if self.stage == 'ROOT_V1':
            from .work_coordinator_handoff_context import PROMPT
            return PROMPT + canonical(frozen)
        if self.stage in {'ROOT_ADJUSTMENT', 'ADJUSTMENT'}:
            from .work_coordinator_handoff_adjustment_runtime import WorkCoordinatorHandoffAdjustmentRuntime
            return WorkCoordinatorHandoffAdjustmentRuntime.prompt(frozen)
        return CODECS[self.stage].prompt(frozen)

    def history(self, *, bounded_parent=False):
        """ALL dynamic original history fields, from the proved boundary state."""
        from .work_coordinator_handoff_adjustments import qualifications
        plan, frames = self.plan, self.frames
        current = self._eligible(plan, frames)
        phase = self._phase(plan, frames, self.records)
        result = {'protocol': 'COORDINATOR_FILE_HANDOFF_STAGE_HISTORY_V1',
            'root': plan_ref(self._root_plan), 'marker': self._root_plan['coordinator_file_handoff_root'],
            'capability': ref(self._caps['runtime']),
            'task': {'id': plan['item_id'], 'revision': self.driver['item_revision']},
            'driver': self.driver, 'authorization': self._auth, 'frames': frames,
            'current_frames': current, 'phase': phase,
            'frontier': [self.old.frontier_entry(f) for f in frames],
            'material_observation': self.material,
            'delivery': [r for r, _ in self.records['work_coordinator_handoff_delivery']],
            'completion': [r for r, _ in self.records['work_coordinator_handoff_completion']],
            'usage': self.sources.usage([f['run'] for f in frames], self.boundary),
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, current))], **PERMISSIONS}
        if plan == self._root_plan:
            seen = {f['run']['unit_key'] for f in frames}
            result['units'] = [{'definition': u, 'qualification': 'HISTORICAL_RETIRED'
                if phase == 'B_COMPLETED' and u['key'] == plan['file_handoffs'][0]['source_unit']
                else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and u['key'] == plan['file_handoffs'][0]['target_unit']
                else 'RECORDED' if u['key'] in seen else 'NOT_RUN', **PERMISSIONS} for u in plan['units']]
        for key, name in (('adjustment_capability', 'adjustment'), ('round_capability', 'round'),
                ('post_round_capability', 'post'), ('successor_capability', 'successor')):
            if name in self._caps:
                result[key] = ref(self._caps[name]) if self._caps[name] is not None else None
        if self.stage == 'ADJUSTMENT':
            result['saved_path'] = self._path(plan, frames, self.material, phase)
        elif self.stage in {'FIRST_ROUND', 'MIXED_ROUND', 'POST_ROUND', 'POST_V1', 'POST_V2'}:
            result['saved_path'] = self._path_value(plan, frames, self.material, phase, self.driver)
        elif self.stage == 'ROOT_ADJUSTMENT':
            result['saved_path'] = None
        if self.stage == 'POST_V2':
            result['protocol'] = ('COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PARENT_COMPONENT_V2'
                if bounded_parent else 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_HISTORY_V2')
        elif self.stage == 'POST_V1':
            result['protocol'] = ('COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PARENT_COMPONENT_V1'
                if bounded_parent else 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_HISTORY_V1')
        return deepcopy(result)
