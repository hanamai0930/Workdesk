"""Independent complete current responsibility proof; no lifecycle writes/input."""
from copy import deepcopy
import json

from .work_read_observation import database_observation
from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
from .work_coordinator_handoff_post_round_candidates import ref
from .work_coordinator_handoff_post_round_v2_saved_tree import WorkCoordinatorHandoffPostRoundV2SavedTree
from .work_coordinator_handoff_post_round_v3_saved_component import SavedRecords
from . import work_coordinator_handoff_post_round_v3_saved_records as r


class WorkCoordinatorHandoffPostRoundV3ResponsibilityCurrent(WorkCoordinatorHandoffMixedRuntime):
    def __init__(self, service, *, _application=None):
        super().__init__(service)
        self.data = SavedRecords(service)
        from .work_coordinator_handoff_post_round_v3_responsibility_component import _CurrentComponent
        self._responsibility_application = _application
        self.component = _CurrentComponent(service, _application=_application)

    _collect = WorkCoordinatorHandoffPostRoundV2SavedTree._collect

    def _resources(self, db, plan):
        # Historical role members may be absent from the final unit list.
        # Observe every actual ancestor's roles as well as its units anew.
        selections = [('coordinator', plan['coordinator']), ('integrator', plan['integrator'])]
        selections += [('unit:' + u['key'], u['member']) for u in plan['units']]
        return {label: {k: v for k, v in self.s.resources.selection(db,
            member['reference']['member_id']).items() if k != 'selected_at'}
            for label, member in selections}

    def _events(self, db, kind, item):
        if kind != 'work_dispatch_bound':
            return super()._events(db, kind, item)
        result = []
        for event in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,)):
            value = json.loads(event['payload'])
            if not (self._belongs(value, item) or event['task_id'] in self._task_ids or event['attempt_id'] in self._attempt_ids):
                continue
            if event['task_id'] != value.get('task_id') or event['attempt_id'] != value.get('attempt_id'):
                raise ValueError('V3原派发事件任务与尝试归属不符。')
            result.append((event, value))
        return result

    def _check_proposals(self, db, plan, h):
        expected = self._parent_reader._proposal_events(db, plan['item_id'])
        actual = self._proposal_events(db, plan['item_id'])
        if actual != expected:
            raise ValueError('V3完整树存在冻结后新增或未知原提案。')
        self._same(self._related(db, 'work_proposal', plan['item_id']), [v for _, v in expected],
            'V3完整树提案记录不等于准确语义重建全集。')

    def _once(self, db, plan):
        candidates = [v for v in self.s.collaboration.all(db, r.KIND_FOR[r.CANDIDATE])
            if v.get('snapshot', {}).get('child_plan') == plan]
        if len(candidates) != 1:
            raise ValueError('V3完整树须有唯一准确child候选。')
        # _once creates a fresh actual V8 owner. Its private V2 traversal still
        # executes exactly two components per complete V2 read, without reuse.
        ready = self.component._once(db, ref(candidates[0]))
        parent = self.component._parent_reader
        self._parent_reader = parent
        self._parents = parent._parents + [ready['checkpoint']['snapshot']['plan']]
        self._layers = parent._layers + [parent]
        self._collected = self._collect(db, plan)
        if self._collected != self._parents + [plan]:
            raise ValueError('V3完整树实际祖先不等于逐层独立重建顺序。')
        self._root_plan = self._parents[0]
        self._plan_ids = {p['id'] for p in self._collected}
        self._identity_ids = {v for p in self._collected for v in plan_ref(p).values()}
        self._task_ids = set(); self._attempt_ids = set()
        self._frame_identities(ready['frames'])
        self._ready = ready; self._round = None
        self._application = ready['application']; self._registration = ready['registration']
        self._auth = ready['authorization']
        self._edge_value = ready['saved_path']['ancestors'][-1]['edge']
        original_driver = ready['driver']
        self._responsibility = deepcopy(self.component._chain)
        chain = self._responsibility
        self._versions = [(ready['events']['driver_event']['seq'], original_driver,
            {**original_driver, 'id': 'driver-version:' + original_driver['revision'], 'driver_id': original_driver['id']})]
        for e, v in zip(chain['events'][::2], chain['versions'][1:]):
            self._versions.append((e['seq'], v, {**v, 'id': 'driver-version:' + v['revision'], 'driver_id': v['id']}))
        driver = chain['driver']
        ph = ready['observation']['history']
        current = deepcopy(ready['frames'])
        for f in current:
            f['source_evidence'] = self.s.work_units.evidence(db, f['run'])
        eligible = self._eligible(plan, current)
        phase = ph['phase']; relation = plan['file_handoffs'][0]
        key = relation['target_unit'] if phase == 'B_COMPLETED' else relation['source_unit'] if phase == 'A_CAPTURED' else None
        if key and next(f for f in reversed(current) if f['run']['unit_key'] == key)['source_evidence']['file_artifacts']['state'] != 'CURRENT':
            phase = 'BLOCKED'
        from .work_coordinator_handoff_adjustments import qualifications
        result = {**deepcopy(ph), 'protocol': 'COORDINATOR_FILE_HANDOFF_POST_ROUND_RESPONSIBILITY_CURRENT_V3',
            'task': {'id': plan['item_id'], 'revision': driver['item_revision']},
            'driver': driver, 'original_driver': original_driver, 'responsibility': chain,
            'full_current_tree_verified': True, 'authorization': self._auth, 'frames': ready['frames'],
            'current_frames': eligible, 'phase': phase, 'material_observation': ready['current_material_observation'],
            'units': [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
                for u, q in zip(plan['units'], qualifications(plan, phase, eligible))],
            'application': self._application, 'registration': self._registration, 'child_plan': plan,
            'saved_path': ready['saved_path'], 'usage': self.s.work_units.usage(db, [f['run'] for f in ready['frames']]),
            'saved_edge_verified': True, 'full_saved_tree_verified': True,
            'preparation_namespace_verified': True, 'successor_runtime_open': False, 'public_apply': False, **PERMISSIONS}
        from .work_coordinator_handoff_post_round_v3_responsibility_audit import audit
        audit(self, db, plan, result)
        # Observe current control/execution/resource conditions independently
        # from the immutable historical save. No observation enables controls.
        item = self.s.work_items.item(db, plan['item_id'])
        live_plan, issues = self.s.work_drivers.readiness(db, item, driver)
        if live_plan != plan:
            raise ValueError('V3完整current当前资源方案已变化。')
        if self.s.work_drivers.execution_issues(db, item['id'], driver):
            raise ValueError('V3完整current关联执行未全部已知退出。')
        if phase == 'BLOCKED':
            issues.append({'code': 'V3_MATERIAL_BLOCKED', 'message': '当前文件材料受阻，不能恢复V3责任。'})
        result['responsibility_conditions'] = issues
        return deepcopy(result)

    def _tail(self, db, value):
        for f in value['current_frames']:
            if self.s.work_units.evidence(db, f['run']) != f['source_evidence']:
                raise ValueError('V3完整树沿用来源末尾变化。')
        self.checkpoints._recheck(db, {'frames': value['frames'], 'observation': self._ready['observation'],
            'current_material_observation': value['material_observation']})
        for layer in self._layers:
            self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])

    @closed
    def read(self, db, plan):
        if not db.in_transaction:
            raise ValueError('V3完整树须属于明确数据库事务。')
        with database_observation(db):
            plans = self._collect(db, plan)
            resources = [(p['id'], self._resources(db, p)) for p in plans]
            changes = db.total_changes
            first_owner = type(self)(self.s, _application=self._responsibility_application)
            final_owner = type(self)(self.s, _application=self._responsibility_application)
            first = first_owner._once(db, plan); again = final_owner._once(db, plan)
            def comparable(value):
                value = deepcopy(value); value['usage'].pop('elapsed_observed_seconds', None)
                return value
            final_plans = self._collect(db, plan)
            final_resources = [(p['id'], self._resources(db, p)) for p in plans]
            first_owner._tail(db, first); final_owner._tail(db, again)
            if (comparable(first) != comparable(again) or final_plans != plans or
                    final_resources != resources or db.total_changes != changes):
                raise ValueError('V3完整树双遍、实际祖先、资源或DB末尾变化。')
            return deepcopy(again)

    def input_for(self, *args, **kwargs):
        raise ValueError('V3后继输入合同尚未开放。')

    activate = input_for
    activate_round = input_for
    reservation = input_for
    _admit = input_for
