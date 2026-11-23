"""Historical checkpoints and candidates, without current substitution.

Root reconstruction lives here; saved-parent checkpoints use the independent
Path reader. Prepared data alone proves no actual child save or permission.
"""
from copy import deepcopy
from pathlib import Path
import json

from .store import digest
from .work_read_observation import database_observation
from .work_coordinator_handoff_adjustments import proposal_state
from .work_transitions import plan_ref
from .work_coordinator_handoff_roots import (
    PLAN_FIELDS, CONFIG_FIELDS, MARKER_FIELD, root_marker, WorkCoordinatorHandoffRoots)
from .work_coordinator_handoff_runtime import READ
from .work_coordinator_handoff_adjustment_runtime import (
    WorkCoordinatorHandoffAdjustmentRuntime, PROTOCOL, UNIT)
from .work_coordinator_handoff_records import (
    PERMISSIONS, DELIVERY_KIND, COMPLETION_KIND, fields)
from .work_coordinator_handoff_adjustment_records import (
    CHECKPOINT, CANDIDATE, PREVIEW, SCOPE, LINEAGE, PAUSE_REASON, KIND_FOR,
    EVENTS, HISTORY_FIELDS, WorkCoordinatorHandoffAdjustmentRecords,
    original_authorization, finite, hashed, SOURCE_FIELDS)


class WorkCoordinatorHandoffAdjustmentSources(WorkCoordinatorHandoffAdjustmentRuntime):
    def read_prepared(self, db, candidate_ref):
        """Independent historical candidate data, still no actual saved edge.

        This gate uses historical checkpoint routing, not live require_save.
        It can therefore remain historical after a pointer/pause/deadline change.
        A saved-edge reader must additionally verify every child and event.
        """
        try:
            with database_observation(db):
                return self._read_prepared(db, candidate_ref)
        except (KeyError, TypeError, AttributeError, LookupError, StopIteration) as exc:
            raise ValueError('写前候选真实来源或完整字段无效。') from exc

    def _read_prepared(self, db, candidate_ref):
        changes = db.total_changes
        transactional = db.in_transaction
        data = WorkCoordinatorHandoffAdjustmentRecords(self.s)
        candidate = data.get(db, CANDIDATE, candidate_ref); body = candidate['snapshot']
        checkpoint = data.get(db, CHECKPOINT, body['checkpoint']); frozen = checkpoint['snapshot']
        source = self.read_checkpoint(db, body['checkpoint'])
        preview_row = data.get(db, PREVIEW, body['preview']); preview = frozen['preview']
        plan = frozen['plan']; proposal = self.s.collaboration.get(db, 'work_proposal', body['proposal']['id'])
        related = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE])
                   if r.get('snapshot', {}).get('proposal') == body['proposal']]
        if (related != [candidate] or preview_row['snapshot'] != preview or
                body['proposal'] != frozen['proposal'] or
                any(r['item_id'] != frozen['item_id'] for r in (candidate, checkpoint, preview_row)) or
                not proposal['created'] <= preview_row['created'] <= checkpoint['created'] <=
                    body['child_seed']['created'] <= candidate['created'] < frozen['original_dispatch_before']):
            raise ValueError('写前候选须绑定唯一准确提案、完整预览和实际时间。')
        transition = {'proposal': frozen['proposal'], 'source': preview['source'],
            'impact': {k:preview[k] for k in ('direct_changes', 'affected_keys', 'protected_keys')},
            'usage_at_transition': preview['usage'], 'authorization_from': preview['authorization'],
            'command_id': candidate['command_id'], 'no_dispatch': True}
        from .work_coordinator_handoff_records import versioned
        expected = versioned({'protocol': CANDIDATE, 'item_id': frozen['item_id'],
            'root': frozen['root'], 'parent_plan': plan_ref(plan), 'capability': frozen['capability'],
            'operation': 'ADJUSTMENT', **PERMISSIONS, 'scope': SCOPE,
            'preview': data.ref(preview_row), 'checkpoint': data.ref(checkpoint),
            'proposal': frozen['proposal'], 'child_seed': body['child_seed'],
            'candidate_units': preview['candidate_units'], 'carry_sources': preview['carry_sources'],
            'historical_coordinators': preview['historical_coordinators'],
            'transition': transition, 'expected_pause_reason': PAUSE_REASON})
        sequences = [self._event(db, EVENTS[p], row) for p,row in
                     ((PREVIEW, preview_row), (CHECKPOINT, checkpoint), (CANDIDATE, candidate))]
        if (body != expected or not self._event(db, 'coordinator_proposal_checked', proposal) <
                sequences[0] < sequences[1] < sequences[2]):
            raise ValueError('写前候选不等于独立真实历史，或实际事件顺序不符。')
        child = {**deepcopy(plan), **body['child_seed'], 'item_revision': source['history']['task']['revision'],
            'units': body['candidate_units'], 'previous_plan': plan['id'], 'previous_plan_reference': plan_ref(plan),
            'result_lineage': body['carry_sources'], 'historical_coordinators': body['historical_coordinators'],
            'transition': transition, 'coordinator_file_handoff_lineage': {'protocol': LINEAGE,
                'root': frozen['root'], 'parent_plan': plan_ref(plan), 'candidate': candidate_ref}}
        child = versioned({k:v for k,v in child.items() if k != 'version'})
        from .work_coordinator_handoff_adjustment_contracts import successor_plan
        successor_plan(child)
        if transactional:
            # DB facts were completely reconstructed once in this transaction.
            # External snapshots/baselines/current bytes remain a fresh boundary.
            self.recheck_assets(db, source, plan)
        elif self.read_checkpoint(db, body['checkpoint']) != source:
            raise ValueError('写前候选最终历史或当前材料读取已变化。')
        if db.total_changes != changes or transactional and not db.in_transaction:
            raise ValueError('写前候选最终历史或当前材料读取已变化。')
        return deepcopy({**source, 'candidate': candidate, 'checkpoint_record': checkpoint,
            'preview_record': preview_row, 'child_plan': child, 'candidate_data_verified': True})

    def recheck_assets(self, db, source, plan):
        for frame in source['frames']:
            if self.sources._saved_files(db, frame['run'])[0] != frame['files']:
                raise ValueError('写前候选最终全部历史快照字节已变化。')
            frozen = frame['run']['input']
            context = frozen.get('handoff_coordination_context') or frozen.get('handoff_unit_context')
            if context:
                self.s.work_file_rework_applications.require_baseline(context['material_observation'])
        self.s.work_file_rework_applications.require_baseline(source['history']['material_observation'])
        observed = self.s.work_file_rework_applications.observe(
            plan[MARKER_FIELD]['project_root'], plan['file_handoffs'][0]['paths'])
        if observed != source['current_material_observation']:
            raise ValueError('写前候选最终当前材料字节已变化。')

    def read_checkpoint(self, db, reference):
        row = WorkCoordinatorHandoffAdjustmentRecords(self.s).get(db,CHECKPOINT,reference)
        if row['snapshot']['parent_plan'] == row['snapshot']['root']:
            return self.read_original(db,reference)
        from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
        return WorkCoordinatorHandoffAdjustmentPath(self.s).checkpoint(db,reference)

    def _original(self, db, checkpoint):
        """Validate original definition independently of the current pointer."""
        plan = self.s.collaboration.get(db, 'teamwork_plan', checkpoint['snapshot']['root']['id'])
        fields(plan, PLAN_FIELDS); hashed(plan); finite(plan['created'])
        roots = [p for p in self.s.collaboration.all(db, 'teamwork_plan')
                 if p.get('item_id') == plan['item_id'] and p.get('previous_plan') is None]
        if (roots != [plan] or plan_ref(plan) != checkpoint['snapshot']['root'] or
                plan_ref(plan) != checkpoint['snapshot']['parent_plan'] or
                plan != checkpoint['snapshot']['plan'] or plan['previous_plan'] is not None or
                plan['state'] != 'PREPARED' or not plan['id'].startswith('teamwork:')):
            raise ValueError('原根历史必须绑定唯一完整原根，不能用后继marker冒充。')
        item = self.s.work_items.item(db, plan['item_id'])
        self.s.work_items.project(db, item['project_id'])
        root = self.s.work_file_rework_applications.root(db, item)
        if (item['state'] != 'OPEN' or item['project_id'] != plan['project_id'] or
                plan['work_requirements'] != {k:item[k] for k in ('title','goal','constraints_text','acceptance')} or
                plan[MARKER_FIELD] != root_marker(plan, root)):
            raise ValueError('原根历史的原要求、项目或原目录已变化。')
        raw = {k:deepcopy(plan[k]) for k in CONFIG_FIELDS}
        for name in ('coordinator', 'integrator'): raw[name] = raw[name]['reference']
        for unit in raw['units']: unit['member'] = unit['member']['reference']
        if WorkCoordinatorHandoffRoots(self.s)._configuration(db, raw, Path(root)) != {
                k:plan[k] for k in CONFIG_FIELDS}:
            raise ValueError('原根完整配置或准确原资源已变化。')
        self.s.work_flows.require_stopped(db, item['id'])
        return plan, item, root

    def _original_versions(self, db, plan, boundary):
        all_revisions = [r for r in self.s.collaboration.all(db, 'work_driver_revision')
                         if r.get('item_id') == plan['item_id'] and r.get('plan_id') == plan['id']]
        revisions = []
        for saved in all_revisions:
            value = self.old._driver(db, plan, {'id':saved['driver_id'],
                'generation':saved['generation'], 'revision':saved['revision']})
            seq = self._driver_event(db, value)
            if seq < boundary: revisions.append((seq, saved, value))
        if not revisions: raise ValueError('原根历史缺少截止前准确责任事件。')
        revisions.sort(key=lambda entry:entry[0]); driver = revisions[-1][2]
        if any(value['generation'] != driver['generation'] for _,_,value in revisions):
            raise ValueError('原根历史generation发生变化。')
        auth_rows = [r for r in self.s.collaboration.all(db, 'work_unit_authorization_revision')
                     if r.get('item_id') == plan['item_id'] and r.get('plan_id') == plan['id']]
        if len(auth_rows) != 1: raise ValueError('原根历史须有唯一准确原授权。')
        auth = self.old._auth(db, plan, {'id':'unit-auth:'+driver['generation'], 'revision':auth_rows[0]['revision']})
        original_authorization(auth)
        if (auth['driver_generation'] != driver['generation'] or
                self._event(db, 'cooperation_authorized', auth) >= boundary):
            raise ValueError('原授权实际事件晚于写前检查点。')
        return driver, auth, [entry[1] for entry in revisions]

    def _records_before(self, db, plan, boundary):
        """Partition by actual events; later layers must verify later records."""
        result = {}
        for kind, event in ((DELIVERY_KIND, 'coordinator_handoff_delivered'),
                            (COMPLETION_KIND, 'coordinator_handoff_completed')):
            values = []
            for row in self.s.collaboration.all(db, kind):
                body = row.get('snapshot', {})
                if (row.get('item_id') != plan['item_id'] and body.get('item_id') != plan['item_id'] and
                        body.get('root', {}).get('id') != plan['id']): continue
                saved = self.records.get(db, kind, self.records.reference(row))
                seq = self._event(db, event, saved)
                if seq >= boundary: continue
                if body['root'] != plan_ref(plan) or body['marker'] != plan[MARKER_FIELD]:
                    raise ValueError('检查点前正式记录不属于准确原根。')
                values.append((saved, seq))
            if len(values) > 1: raise ValueError('原根历史交付及完成必须唯一。')
            result[kind] = values
        return result

    def read_original(self, db, checkpoint_ref):
        """Read-only component. Never call this as a complete saved-edge gate."""
        try:
            with database_observation(db):
                return self._read_original(db, checkpoint_ref)
        except (KeyError, TypeError, AttributeError, LookupError, StopIteration) as exc:
            raise ValueError('原根检查点真实历史字段或来源无效。') from exc

    def _read_original(self, db, checkpoint_ref):
        changes = db.total_changes
        data = WorkCoordinatorHandoffAdjustmentRecords(self.s)
        checkpoint = data.get(db, CHECKPOINT, checkpoint_ref)
        boundary = self._event(db, EVENTS[CHECKPOINT], checkpoint)
        plan, item, root = self._original(db, checkpoint)
        current = deepcopy(self.s.teamwork.current(db, item['id']))
        cap, activated = self.capability(db, plan)
        self._adjustment, self._opened = self.adjustment_capability(db, plan)
        if not activated < self._opened < boundary:
            raise ValueError('原运行能力、调整能力与检查点实际顺序不一致。')
        driver, auth, revisions = self._original_versions(db, plan, boundary)
        if driver['state'] != 'RESERVED': raise ValueError('原写前检查点责任必须实际可用。')
        rows = [r for r in self.s.work_units.rows(db, item['id']) if r['plan_id'] == plan['id']]
        if any(self.sources._reservation_event(db, r) >= boundary for r in rows):
            raise ValueError('原根在已冻结检查点后又有新运行。')
        records = self._records_before(db, plan, boundary)
        frames, revision, seen = self._reconstruct_runs(
            db, plan, item, root, cap, activated, driver, auth, revisions, rows, records)
        for frame in frames:
            run = frame['run']; attempt = self.s.store.attempt(run['attempt_id'], db)
            exits = [r['seq'] for r in db.execute(
                "SELECT seq,payload FROM events WHERE task_id=? AND attempt_id=? AND kind='attempt_result_saved'",
                (run['task_id'], run['attempt_id'])) if json.loads(r['payload']) == attempt['result']]
            if (len(exits) != 1 or exits[0] >= boundary or
                    any(e['seq'] >= boundary for e in self.sources._capture_events(db,run,frame['files']))):
                raise ValueError('原退出或全部快照捕获必须实际早于检查点。')
        if revision != driver['item_revision']:
            raise ValueError('原根完整运行绑定没有到达检查点责任修订。')
        before = [f for f in frames if self.sources._reservation_event(db, f['run']) < self._opened]
        after = [f for f in frames if self.sources._reservation_event(db, f['run']) > self._opened]
        prior = [self.sources._frame_before(db, f['run'], after[0]['run']) for f in before] if after else before
        seen_before = {f['run']['unit_key'] for f in before}
        eligible = sorted(u['key'] for u in plan['units'] if u['role'] == 'coordinate' and u['key'] not in seen_before)
        if (self._adjustment['snapshot']['activation_frontier'] != [self.old.frontier_entry(f) for f in prior] or
                self._adjustment['snapshot']['eligible_coordinate_keys'] != eligible):
            raise ValueError('原调整激活前沿或全部未运行coordinate缺失。')
        for frame in after:
            frozen = frame['run']['input']
            context = frozen.get('handoff_coordination_context') or frozen['handoff_unit_context']
            if context['protocol'] != (PROTOCOL if frozen['unit']['role'] == 'coordinate' else UNIT):
                raise ValueError('原调整能力激活后不能新增旧输入协议。')
        proposal = self.s.collaboration.get(db, 'work_proposal', checkpoint['snapshot']['proposal']['id'])
        fields(proposal, {'id','version','snapshot','item_id','created','command_id'})
        checked = self._event(db, 'coordinator_proposal_checked', proposal)
        actual = [json.loads(r['payload']) for r in db.execute(
            "SELECT seq,payload FROM events WHERE kind='coordinator_proposal_checked' ORDER BY seq")
            if r['seq'] < boundary and json.loads(r['payload']).get('item_id') == item['id']]
        if (self.ref(proposal) != checkpoint['snapshot']['proposal'] or
                proposal['version'] != digest(proposal['snapshot']) or proposal['item_id'] != item['id'] or
                not actual or actual[-1] != proposal or checked >= boundary or
                not proposal_state(proposal['snapshot']) or
                proposal['snapshot']['plan'] != plan_ref(plan) or
                proposal['snapshot']['adjustment'] != checkpoint['snapshot']['preview']):
            raise ValueError('原检查点须来自实际截止前最新准确V3提案。')
        # All original snapshots remain required, including retired A snapshots.
        self._match_records(db, plan, frames, records)
        if not frames or frames[-1]['run']['input']['unit']['role'] != 'coordinate':
            raise ValueError('调整检查点必须由最后真实协调运行冻结。')
        source = frames[-1]['source_evidence']
        if (proposal['snapshot']['source'] != {k:source[k] for k in SOURCE_FIELDS} or
                checkpoint['snapshot']['preview']['source'] != proposal['snapshot']['source'] or
                self.s.work_units.usage(db,rows) != checkpoint['snapshot']['usage']):
            raise ValueError('原检查点实际来源或全部原运行累计不一致。')
        observation = frames[-1]['run']['input']['handoff_coordination_context']['material_observation']
        self.s.work_file_rework_applications.require_baseline(observation)
        current_observation = self.s.work_file_rework_applications.observe(root, plan['file_handoffs'][0]['paths'])
        phase = self._phase(plan, frames, records)
        seen = {f['run']['unit_key'] for f in frames}
        history = {'protocol':READ, 'root':plan_ref(plan), 'marker':plan[MARKER_FIELD],
            'capability':self.ref(cap), 'adjustment_capability':self.ref(self._adjustment),
            'task':{'id':item['id'], 'revision':revision}, 'driver':driver, 'authorization':auth,
            'phase':phase, 'frontier':[self.old.frontier_entry(f) for f in frames],
            'material_observation':observation, 'delivery':[r for r,_ in records[DELIVERY_KIND]],
            'completion':[r for r,_ in records[COMPLETION_KIND]],
            'units':[{'definition':u, 'qualification':'HISTORICAL_RETIRED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['source_unit'] else 'CURRENT_COMPLETED' if phase == 'B_COMPLETED' and
                u['key'] == plan['file_handoffs'][0]['target_unit'] else 'RECORDED' if u['key'] in seen else 'NOT_RUN',
                **PERMISSIONS} for u in plan['units']], **PERMISSIONS}
        from .work_coordinator_handoff_adjustments import rebuild_preview
        if (not frames[-1]['run']['created'] <= proposal['created'] <= checkpoint['created'] < auth['dispatch_before']):
            raise ValueError('原提案与检查点须在实际来源后、最初截止前生成。')
        # CURRENT here describes the already verified historical snapshots.
        # It confers no present-day attachment or result eligibility.
        facts = {**history, 'frames': frames, 'current_frames': frames,
                 'usage': self.s.work_units.usage(db, rows)}
        preview = rebuild_preview(plan, facts, frames[-1]['run'], proposal['snapshot']['decision'],
                                  observed_at=proposal['created'])
        if preview != checkpoint['snapshot']['preview']:
            raise ValueError('原预览完整图语义不等于已核真实历史和原回复重建。')
        if (set(history) != HISTORY_FIELDS or history != checkpoint['snapshot']['history'] or
                db.total_changes != changes or self.s.teamwork.current(db, item['id']) != current or
                self.s.work_file_rework_applications.observe(root, plan['file_handoffs'][0]['paths']) != current_observation):
            raise ValueError('原根真实历史不等于完整检查点，或只读期间current/材料已变化。')
        return deepcopy({'history':history, 'frames':frames, 'checkpoint':checkpoint_ref,
            'checkpoint_event_seq':boundary, 'current_material_observation':current_observation,
            'saved_edge_verified':False, 'public_apply':False,
            'successor_layers_verified':False, 'preview_semantics_verified':True, **PERMISSIONS})
