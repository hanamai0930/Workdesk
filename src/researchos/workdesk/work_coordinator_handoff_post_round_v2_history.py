"""Actual V8 proposal-event prefix; a component, never a new saved edge.

The ordered-save owner still owes its complete saved/preparation namespace
audit. No old V7 codec or live current pointer grants authority to this reader.
"""
from copy import deepcopy

from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import PERMISSIONS, reference, DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref
from .work_coordinator_handoff_post_round_v2_candidates import LINEAGE
from .work_coordinator_handoff_post_round_v2_runtime import WorkCoordinatorHandoffPostRoundV2Runtime
from .work_coordinator_handoff_post_round_v2_run_contracts import KIND, OPENED, PROTOCOL
from .work_coordinator_handoff_post_round_v2_preview import proposal_state, rebuild_preview
from .work_coordinator_handoff_post_round_v2_commands import RECEIPT_KIND, RECEIPT_EVENT, require_receipt

READ = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PARENT_COMPONENT_V2'
SCOPE = 'ACTUAL_V8_PROPOSAL_EVENT_PREFIX; BOUNDED_PARENT_COMPONENT'
CHECKED = 'coordinator_proposal_checked'


class WorkCoordinatorHandoffPostRoundV2History(WorkCoordinatorHandoffPostRoundV2Runtime):
    def _events(self, db, kind, item):
        pairs = [(e, v) for e, v in super()._events(db, kind, item) if e['seq'] < self._cutoff]
        if kind in (OPENED, RECEIPT_EVENT):
            pairs = [(e, v) for e, v in pairs if (v['snapshot']['observation']['plan_snapshot'] == self._plan
                if kind == OPENED else v['snapshot']['plan'] == plan_ref(self._plan))]
        return pairs

    def _related(self, db, kind, item):
        rows = super()._related(db, kind, item)
        if kind == 'work_proposal':
            ids = {v['id'] for _, v in self._events(db, CHECKED, item)}
            return [v for v in rows if v['id'] in ids]
        if kind in (KIND, RECEIPT_KIND):
            return [v for v in rows if (v['snapshot']['observation']['plan_snapshot'] == self._plan
                if kind == KIND else v['snapshot']['plan'] == plan_ref(self._plan))]
        return rows

    def _check_proposal_pointer(self, db, plan, events):
        # The last *actual checked event*, not today's mutable current pointer.
        if not events or events[-1] != (self._boundary_event['seq'], self._proposal_row):
            raise ValueError('V8截止提案不是完整前缀的最后真实检查。')

    def _boundary(self, db, plan, identity):
        reference(identity)
        row = self.s.collaboration.get(db, 'work_proposal', identity['id'])
        if (not row or ref(row) != identity or row['item_id'] != plan['item_id'] or
                not proposal_state(row['snapshot']) or
                plan.get(LINEAGE_FIELD, {}).get('protocol') != LINEAGE or
                row['snapshot']['plan'] != plan_ref(plan)):
            raise ValueError('V8截止须绑定真实V2保存方案及独立调整预览。')
        source = row['snapshot']['source']
        event = self.checkpoints._event(db, CHECKED, row, None, source['task_id'], source['attempt_id'])
        self._cutoff = event['seq'] + 1; self._plan = plan
        self._proposal_row = row; self._boundary_event = event
        return row, event

    def _once_at(self, db, plan, identity):
        self._db = db
        proposal, boundary = self._boundary(db, plan, identity)
        ready, start, auth = self._base_saved(db, plan)
        versions = self._drivers(db, plan, start, self._edge_value['registration_event']['seq'])
        self._versions = [v for v in versions if v[0] < self._cutoff]
        if not self._versions: raise ValueError('V8截止前缺少真实保存责任。')
        driver = self._versions[-1][1]; ph = ready['observation']['history']
        self._load_successor(db, plan)
        obs = self._successor['snapshot']['observation']
        previous = [v for seq, v, _ in self._versions if seq < self._opened]
        if (not previous or previous[-1] != obs['driver'] or self._opened >= boundary['seq'] or
                self._opened <= self._edge_value['registration_event']['seq']):
            raise ValueError('V8截止开启须属于保存后明确恢复和真实检查之前。')
        self._inherited = ready['frames']
        rows = [r for r in self.s.work_units.rows(db, plan['item_id']) if r['plan_id'] == plan['id']]
        if (not rows or any(self.sources._reservation_event(db, r) >= self._cutoff for r in rows) or
                rows[0]['command_id'] != self._successor['command_id'] or
                rows[0]['driver_revision'] != obs['driver']['revision'] or
                rows[0]['input']['unit'] != obs['selected_unit']['definition'] or
                rows[0]['created'] < self._successor['created']):
            raise ValueError('V8截止能力、准确首派发或冻结运行范围不符。')
        cap, activated = self.capability(db, plan)
        records = {k: [(r, seq) for r, seq in group if seq < self._cutoff]
            for k, group in self._records(db, plan).items()}
        frames, revision, _ = self._layer_runs(db, plan, {'id': plan['item_id']},
            ph['marker']['project_root'], cap, activated, driver, auth,
            [r for _, _, r in self._versions], rows, records)
        self._match_root_records(db, self._root_plan, frames, records)
        if revision != driver['item_revision']: raise ValueError('V8截止任务未到达准确原责任版本。')
        for frame in frames:
            run = frame['run']; attempt = self.s.store.attempt(run['attempt_id'], db)
            self.checkpoints._event(db, 'attempt_result_saved', attempt['result'], boundary['seq'] - 1,
                run['task_id'], run['attempt_id'])
            if any(e['seq'] >= self._cutoff for e in self.sources._capture_events(db, run, frame['files'])):
                raise ValueError('V8截止后原成果捕获发生变化。')
        material = proposal['snapshot']['adjustment']['material_observation']
        self.s.work_file_rework_applications.require_baseline(material)
        phase = self._phase(plan, frames, records)
        h = {**deepcopy(ph), 'protocol': READ, 'task': {'id': plan['item_id'], 'revision': revision},
            'driver': driver, 'authorization': auth, 'frames': frames,
            'current_frames': self._eligible(plan, frames), 'phase': phase, 'material_observation': material,
            'frontier': [self.old.frontier_entry(f) for f in frames],
            'delivery': [r for r, _ in records[DELIVERY_KIND]],
            'completion': [r for r, _ in records[COMPLETION_KIND]],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'successor_capability': self.ref(self._successor), **PERMISSIONS}
        h['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        from .work_coordinator_handoff_adjustments import qualifications
        h['units'] = [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
            for u, q in zip(plan['units'], qualifications(plan, phase, h['current_frames']))]
        self._audit_admission(db, plan, ready, obs, rows)
        self._check_proposals(db, plan, h); require_receipt(self, db, plan, frames)
        self._command(db, {**h, 'child_plan': plan, 'application': self._application, 'registration': self._registration})
        source = next((f['run'] for f in frames if f['run']['id'] == proposal['snapshot']['source']['run_id']), None)
        if (source is None or source['plan_id'] != plan['id'] or
                source['input'].get('handoff_coordination_context', {}).get('protocol') != PROTOCOL or
                driver['state'] != 'RESERVED' or rebuild_preview(plan, h, source, proposal['snapshot']['decision'],
                    observed_at=proposal['created']) != proposal['snapshot']['adjustment']):
            raise ValueError('V8截止父不是当时最新准确来源及完整原语义。')
        self._audit_prefix(db, plan, frames)
        self._audit_namespace(db, plan, h)
        actual = self.s.work_file_rework_applications.observe(material['root'], material['paths'])
        return {'protocol': READ, 'scope': SCOPE, 'plan': plan_ref(plan), 'proposal': proposal,
            'boundary_event': boundary, 'history': h, 'current_material_observation': actual,
            'bounded_parent_components_verified': True, 'full_saved_tree_verified': False,
            'preparation_namespace_verified': False, 'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    def _audit_namespace(self, db, plan, h):
        from .work_coordinator_handoff_post_round_v2_history_namespace import audit
        audit(self, db, plan, h)

    def _audit_prefix(self, db, plan, frames):
        self._frame_identities(frames)
        self._same([v for _, v in self._events(db, 'cooperative_unit_reserved', plan['item_id'])],
            [f['run'] for f in frames], 'V8截止实际保留有遗漏或孤立。')
        for kind in ('attempt_result_saved', 'cooperative_files_checked'):
            expected = []
            for frame in frames:
                run = frame['run']
                if kind == 'attempt_result_saved':
                    attempt = self.s.store.attempt(run['attempt_id'], db)
                    expected.append(self.checkpoints._event(db, kind, attempt['result'], None, run['task_id'], run['attempt_id']))
                else:
                    expected.extend(self.checkpoints.live._event(db.execute('SELECT * FROM events WHERE seq=?', (e['seq'],)).fetchone())
                        for e in self.sources._capture_events(db, run, frame['files']))
            self._same([self.checkpoints.live._event(e) for e, _ in self._events(db, kind, plan['item_id'])],
                expected, 'V8截止退出或捕获有遗漏、孤立或任务误归属。')

    def _read_at(self, db, plan, identity):
        changes = db.total_changes
        resources = self._resources(db, plan)
        first = self._once_at(db, plan, identity); again = self._once_at(db, plan, identity)
        self.checkpoints._recheck(db, {'frames': again['history']['frames'],
            'observation': {'history': again['history']},
            'current_material_observation': again['current_material_observation']})
        for layer in self._layers:
            self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])
        def comparable(value):
            value = deepcopy(value); value['history']['usage'].pop('elapsed_observed_seconds', None)
            return value
        if comparable(first) != comparable(again) or db.total_changes != changes or self._resources(db, plan) != resources:
            raise ValueError('V8截止双遍完整重建、末尾资源或数据库发生变化。')
        return deepcopy(again)

    @closed
    def read_at_proposal(self, db, plan, identity):
        if not db.in_transaction: raise ValueError('V8截止读取须属于明确数据库事务。')
        from .work_coordinator_handoff_post_round_v2_traversal import read
        with database_observation(db):
            return read(self, db, plan, lambda connection, parent: self._read_at(connection, parent, identity))
