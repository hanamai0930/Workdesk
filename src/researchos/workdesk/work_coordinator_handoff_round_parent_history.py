"""Event-bounded reconstruction of an actually executed first ROUND layer.

_walk is a reconstruction component, not a complete mixed-path proof. Its caller
must audit the complete live namespace as well. No current-reader proof is used
as historical input, and V4/ordinary V3 are rebuilt by their original codec.
"""
from copy import deepcopy

from .work_transitions import plan_ref
from .work_coordinator_handoff_records import PERMISSIONS, DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_runtime import READ
from .work_coordinator_handoff_roots import MARKER_FIELD
from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
from .work_coordinator_handoff_round_runtime import WorkCoordinatorHandoffRoundRuntime, OPENED


class WorkCoordinatorHandoffRoundParentHistory(WorkCoordinatorHandoffRoundRuntime):
    def _walk(self, db, plan, boundary=None, material=None, *, audit_namespace=False):
        """Rebuild this layer at the actual event, without consulting current.

        The boundary must be checked against its real event by the checkpoint
        reader. All immutable versions are validated before selecting a prefix.
        Parent runs and captures after the freeze are rejected, not projected
        away. A future mixed reader must audit all descendant namespaces too.
        """
        self._db = db
        ready, app, reg, start, auth, edge = self._base(db, plan)
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

    def audit_current(self, db, plan):
        """Strict live first-ROUND namespace gate until mixed-path integration.

        It deliberately rejects any successor plan. _walk itself never uses
        current; exposing it as a mixed proof without the future whole-tree
        audit would be incorrect.
        """
        h, _ = self._walk(db, plan, audit_namespace=True)
        return h
