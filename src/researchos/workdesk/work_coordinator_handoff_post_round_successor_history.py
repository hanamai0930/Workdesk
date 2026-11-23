"""Event-bounded V7 parent components for subsequent typed saves.

Each traversal rebuilds the actual first post-round edge and its V7/ordinary
V6 runs. The caller of a subsequent save still owes its complete tree audit.
"""
from copy import deepcopy
import json

from .store import digest
from .work_transitions import plan_ref
from .work_read_observation import database_observation
from .work_coordinator_handoff_records import PERMISSIONS, reference
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_post_round_candidates import LINEAGE_FIELD, ref
from .work_coordinator_handoff_post_round_successor_runtime import (
    WorkCoordinatorHandoffPostRoundSuccessorRuntime)
from .work_coordinator_handoff_post_round_successor_contracts import KIND, OPENED, PROTOCOL
from .work_coordinator_handoff_post_round_successor_commands import (
    RECEIPT_KIND, RECEIPT_EVENT, require_receipt)
from .work_coordinator_handoff_post_round_successor_preview import proposal_state, rebuild_preview

READ = 'COORDINATOR_FILE_HANDOFF_POST_ROUND_SUCCESSOR_PARENT_COMPONENT_V1'
SCOPE = 'ACTUAL_V7_PROPOSAL_EVENT_PREFIX; BOUNDED_PARENT_COMPONENT'
CHECKED = 'coordinator_proposal_checked'


class WorkCoordinatorHandoffPostRoundSuccessorHistory(WorkCoordinatorHandoffPostRoundSuccessorRuntime):
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

    def _check_proposals(self, db, plan, h):
        # Historical semantics are shared with the installed runtime; its one
        # live current-proposal assertion is replaced by the exact event bound.
        from .work_proposals import parse
        from .work_coordinator_handoff_adjustment_records import SOURCE_FIELDS
        from .work_coordinator_handoff_post_round_contracts import PROTOCOL as OLD
        from .work_coordinator_handoff_post_round_preview import proposal_state as old_state
        from .work_coordinator_handoff_adjustments import proposal_state as legacy_state
        pairs = self._proposal_events(db, plan['item_id'])
        self._same(self._related(db, 'work_proposal', plan['item_id']), [v for _, v in pairs],
            '截止父提案记录与真实检查事件不符。')
        _, activated = self.capability(db, self._root_plan)
        for seq, row in pairs:
            body = row['snapshot']
            if set(row) != {'id', 'version', 'snapshot', 'item_id', 'created', 'command_id'} or row['version'] != digest(body):
                raise ValueError('截止父提案wrapper或摘要不符。')
            source = next((f for f in h['frames'] if f['run']['id'] == body['source']['run_id']), None)
            if not source or source['run']['input']['unit']['role'] != 'coordinate':
                raise ValueError('截止父提案缺少实际coordinate来源。')
            run = source['run']; full = source['source_evidence']; frozen = run['input']
            p = self.s.collaboration.get(db, 'teamwork_plan', run['plan_id'])
            driver = self.old._driver(db, p, body['driver']); auth = self.old._auth(db, p, body['authorization'])
            known = [f for f in h['frames'] if self.sources._reservation_event(db, f['run']) < seq]
            if (body['plan'] != plan_ref(p) or body['item_revision'] != driver['item_revision'] or
                    self._driver_event(db, driver) >= seq or self.sources._reservation_event(db, run) >= seq or
                    body['source'] != {k: full[k] for k in SOURCE_FIELDS} or
                    body['answer_digest'] != digest(full['answer']) or
                    body['answer'] != (full['answer'] if len(full['answer']) <= 32000 else None) or
                    body['handoff_context'] != frozen['coordination_context']['handoff_context'] or
                    body['frontier'] != digest([{'run_id': f['run']['id'], 'evidence': f['evidence' if seq > activated else 'source_evidence']['version']} for f in known]) or
                    not known or [f for f in known if f['run']['input']['unit']['role'] == 'coordinate'][-1]['run']['id'] != run['id'] or
                    driver['generation'] != run['generation'] or auth['driver_generation'] != driver['generation']):
                raise ValueError('截止父提案原回复、完整前沿或责任授权不符。')
            if body['state'] == 'INVALID': continue
            decision = parse(full['answer'])
            if decision != body['decision']: raise ValueError('截止父决定不是原回复。')
            if decision['action'] == 'dispatch':
                target = next((v for v in frozen['coordination_context']['candidates'] if v['unit']['key'] == decision['unit_key']), None)
                if (body['state'] != 'VALID' or not target or not target['ready_after_proposal'] or
                        decision['retry_of'] is not None or body['target'] != {'key': target['unit']['key'], 'revision': digest(target['unit'])}):
                    raise ValueError('截止派发提案不是冻结的准确可达候选。')
            elif decision['action'] == 'pause':
                if body['state'] != 'PAUSE_SUGGESTED' or body['target'] is not None:
                    raise ValueError('截止暂停提案身份不符。')
            elif decision['action'] == 'adjust_dependencies':
                protocol = frozen.get('handoff_coordination_context', {}).get('protocol')
                state = proposal_state if protocol == PROTOCOL else old_state if protocol == OLD else legacy_state
                if not state(body) or body['target'] is not None: raise ValueError('截止调整预览协议或目标不符。')
                if protocol in (PROTOCOL, OLD): self._preview_at(db, p, h, row, seq, driver, auth)
            else: raise ValueError('截止父输入未开放该提案操作。')

    def _boundary(self, db, plan, identity):
        reference(identity)
        row = self.s.collaboration.get(db, 'work_proposal', identity['id'])
        if not row or ref(row) != identity or row['item_id'] != plan['item_id'] or not proposal_state(row['snapshot']):
            raise ValueError('后继父截止须为真实V7调整预览。')
        body = row['snapshot']; source = body['source']
        event = self.checkpoints._event(db, CHECKED, row, None, source['task_id'], source['attempt_id'])
        if body['plan'] != plan_ref(plan) or not plan.get(LINEAGE_FIELD):
            raise ValueError('后继父截止提案须绑定准确POST_ROUND保存方案。')
        self._cutoff = event['seq'] + 1; self._plan = plan
        return row, event

    def _once_at(self, db, plan, identity):
        self._db = db
        proposal, boundary = self._boundary(db, plan, identity)
        ready, start, auth = self._base_saved(db, plan)
        all_versions = self._drivers(db, plan, start, self._edge_value['registration_event']['seq'])
        self._versions = [v for v in all_versions if v[0] < self._cutoff]
        if not self._versions: raise ValueError('截止前缺少真实保存责任。')
        driver = self._versions[-1][1]; ph = ready['observation']['history']
        self._load_successor(db, plan)
        if self._opened >= boundary['seq']: raise ValueError('V7开启晚于真实检查事件。')
        obs = self._successor['snapshot']['observation']
        prior_versions = [v for seq, v, _ in self._versions if seq < self._opened]
        if (not prior_versions or prior_versions[-1] != obs['driver'] or
                self._opened <= self._edge_value['registration_event']['seq']):
            raise ValueError('截止开启缺少保存后准确恢复责任。')
        self._inherited = ready['frames']
        rows = [r for r in self.s.work_units.rows(db, plan['item_id']) if r['plan_id'] == plan['id']]
        if (not rows or any(self.sources._reservation_event(db, r) >= self._cutoff for r in rows) or
                rows[0]['command_id'] != self._successor['command_id'] or
                rows[0]['driver_revision'] != obs['driver']['revision'] or
                rows[0]['input']['unit'] != obs['selected_unit']['definition'] or rows[0]['created'] < self._successor['created']):
            raise ValueError('截止V7能力与实际首派发或冻结运行范围不符。')
        cap, activated = self.capability(db, plan)
        records = {k: [(v, seq) for v, seq in group if seq < self._cutoff]
            for k, group in self._records(db, plan).items()}
        frames, revision, _ = self._layer_runs(db, plan, ph['task'], ph['marker']['project_root'],
            cap, activated, driver, auth, [r for _, _, r in self._versions], rows, records)
        if revision != driver['item_revision']: raise ValueError('截止任务绑定没有到达准确原责任版本。')
        for f in frames:
            run = f['run']; attempt = self.s.store.attempt(run['attempt_id'], db)
            self.checkpoints._event(db, 'attempt_result_saved', attempt['result'], boundary['seq'] - 1,
                run['task_id'], run['attempt_id'])
            if any(e['seq'] >= self._cutoff for e in self.sources._capture_events(db, run, f['files'])):
                raise ValueError('父冻结后原成果捕获仍有变化。')
        material = proposal['snapshot']['adjustment']['material_observation']
        self.s.work_file_rework_applications.require_baseline(material)
        phase = self._phase(plan, frames, records)
        h = {**deepcopy(ph), 'protocol': READ, 'task': {'id': plan['item_id'], 'revision': revision},
            'driver': driver, 'authorization': auth, 'frames': frames,
            'current_frames': self._eligible(plan, frames), 'phase': phase, 'material_observation': material,
            'frontier': [self.old.frontier_entry(f) for f in frames],
            'usage': self.s.work_units.usage(db, [f['run'] for f in frames]),
            'successor_capability': self.ref(self._successor), **PERMISSIONS}
        h['saved_path'] = self._path_value(plan, frames, material, phase, driver)
        from .work_coordinator_handoff_adjustments import qualifications
        h['units'] = [{'definition': u, 'qualification': q['qualification'], **PERMISSIONS}
            for u, q in zip(plan['units'], qualifications(plan, phase, h['current_frames']))]
        self._audit_admission(db, plan, ready, obs, rows)
        self._check_proposals(db, plan, h); require_receipt(self, db, plan, frames)
        self._command(db, {**h, 'child_plan': plan, 'application': self._application, 'registration': self._registration})
        pairs = self._proposal_events(db, plan['item_id'])
        source = next((f['run'] for f in frames if f['run']['id'] == proposal['snapshot']['source']['run_id']), None)
        if (not pairs or pairs[-1] != (boundary['seq'], proposal) or source is None or
                source['plan_id'] != plan['id'] or source['input'].get('handoff_coordination_context', {}).get('protocol') != PROTOCOL or
                driver['state'] != 'RESERVED' or rebuild_preview(plan, h, source, proposal['snapshot']['decision'],
                    observed_at=proposal['created']) != proposal['snapshot']['adjustment']):
            raise ValueError('截止父不是当时最新准确V7来源及原语义。')
        self._audit_prefix(db, plan, frames)
        actual = self.s.work_file_rework_applications.observe(material['root'], material['paths'])
        return {'protocol': READ, 'scope': SCOPE, 'plan': plan_ref(plan), 'proposal': proposal,
            'boundary_event': boundary, 'history': h, 'current_material_observation': actual,
            'bounded_parent_components_verified': True, 'full_saved_tree_verified': False,
            'preparation_namespace_verified': False, 'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    def _audit_prefix(self, db, plan, frames):
        self._frame_identities(frames)
        self._same([v for _, v in self._events(db, 'cooperative_unit_reserved', plan['item_id'])],
            [f['run'] for f in frames], '截止父实际保留事件有遗漏或孤立。')
        for kind in ('attempt_result_saved', 'cooperative_files_checked'):
            expected = []
            for f in frames:
                run = f['run']
                if kind == 'attempt_result_saved':
                    attempt = self.s.store.attempt(run['attempt_id'], db)
                    expected.append(self.checkpoints._event(db, kind, attempt['result'], None, run['task_id'], run['attempt_id']))
                else:
                    expected.extend(self.checkpoints.live._event(db.execute('SELECT * FROM events WHERE seq=?', (e['seq'],)).fetchone())
                        for e in self.sources._capture_events(db, run, f['files']))
            self._same([self.checkpoints.live._event(e) for e, _ in self._events(db, kind, plan['item_id'])],
                expected, '截止父退出或捕获事件有遗漏、孤立或误归属。')

    @closed
    def read_at_proposal(self, db, plan, identity):
        if not db.in_transaction: raise ValueError('后继父历史读取须属于明确数据库事务。')
        with database_observation(db):
            changes = db.total_changes
            selection = lambda: {u['key']: {k: v for k, v in self.s.resources.selection(db, u['member']['reference']['member_id']).items()
                if k != 'selected_at'} for u in plan['units']}
            before = selection()
            first = self._once_at(db, plan, identity); again = self._once_at(db, plan, identity)
            self.checkpoints._recheck(db, {'frames': again['history']['frames'],
                'observation': {'history': again['history']}, 'current_material_observation': again['current_material_observation']})
            for layer in self._layers:
                self.s.work_file_rework_applications.require_baseline(layer._ready['observation']['history']['material_observation'])
            def comparable(value):
                value = deepcopy(value); value['history']['usage'].pop('elapsed_observed_seconds', None)
                return value
            if comparable(first) != comparable(again) or db.total_changes != changes or selection() != before:
                raise ValueError('后继父双遍完整重建、末尾资源或数据库发生变化。')
            return deepcopy(again)
