"""Real round-event checkpoints and independent bounded root/adjustment history.

No fabricated adjustment checkpoint or model proposal is used. The preserving
context must enclose the future whole save, so an exception also removes only
the exact material baseline first created by that transaction.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json
import shutil
import time

from .store import digest, uid
from .work_read_observation import database_observation
from .work_transitions import plan_ref, source_ref
from .work_coordinator_handoff_roots import (
    PLAN_FIELDS, CONFIG_FIELDS, MARKER_FIELD, root_marker, WorkCoordinatorHandoffRoots)
from .work_coordinator_handoff_runtime import WorkCoordinatorHandoffRuntime, READ
from .work_coordinator_handoff_adjustment_runtime import (
    WorkCoordinatorHandoffAdjustmentRuntime, enabled as adjustment_enabled, PROTOCOL, UNIT)
from .work_coordinator_handoff_adjustment_sources import WorkCoordinatorHandoffAdjustmentSources
from .work_coordinator_handoff_adjustment_path import WorkCoordinatorHandoffAdjustmentPath
from .work_coordinator_handoff_adjustment_contracts import closed
from .work_coordinator_handoff_records import PERMISSIONS, versioned, DELIVERY_KIND, COMPLETION_KIND
from .work_coordinator_handoff_mixed_records import (
    CHECKPOINT, CANDIDATE, EVENTS, HISTORY, OPERATION, SCOPE, KIND_FOR, parent_ref, validate_parent,
    WorkCoordinatorHandoffMixedRecords)
from .work_coordinator_handoff_round_sources import WorkCoordinatorHandoffRoundSources
from .work_coordinator_handoff_round_checkpoints import WorkCoordinatorHandoffRoundCheckpoints
from .work_coordinator_handoff_mixed_records import LINEAGE, PARENT
from .work_coordinator_handoff_mixed_parent import HISTORY_FIELDS, SCOPE as PARENT_SCOPE


class WorkCoordinatorHandoffMixedCheckpoints:
    def __init__(self, service):
        self.s = service
        self.data = WorkCoordinatorHandoffMixedRecords(service)
        self.live = WorkCoordinatorHandoffRoundSources(service)
        self.sources = self.live
        self.old = WorkCoordinatorHandoffRoundCheckpoints(service)

    @contextmanager
    def preserving(self, db, plan, reason):
        """Internal preparation only, scoped through the caller's entire save."""
        if not db.in_transaction: raise ValueError('回合检查点必须属于同一保存事务。')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('明确新回合须说明原因，最多2000字。')
        obs = self.observe(db, plan)
        baseline = obs['history']['material_observation']; files = self.s.work_file_rework_applications
        asset = files.folder(baseline).parent
        results = (self.s.locations.data / 'results').resolve()
        if asset.resolve().parent != results or asset.name != 'file-rework-base-' + baseline['version'] or asset.is_symlink():
            raise ValueError('回合材料保存目录不是准确服务资产位置。')
        existed = asset.exists()
        db.execute('SAVEPOINT round_checkpoint_assets')
        try:
            files.preserve(baseline)
            # Preservation can race external files; observe again before the
            # first event. This remains the actual current parent, not a proof.
            if self.observe(db, plan) != obs: raise ValueError('回合材料保存期间父观察已变化。')
            body = versioned({'protocol': CHECKPOINT, 'item_id': plan['item_id'], 'root': obs['root'],
                'parent_plan': plan_ref(plan), 'parent_path': parent_ref(obs), 'source': obs['source'],
                'selected_unit': obs['selected_unit'], 'reason': reason, 'operation': OPERATION,
                'plan': plan, 'observation': obs, **PERMISSIONS, 'scope': SCOPE})
            row = self.data.put(db, CHECKPOINT, body)
            result = self.read(db, self.data.ref(row))
            if result['current_material_observation'] != baseline or time.time() >= obs['original_dispatch_before']:
                raise ValueError('回合检查点保存末尾当前材料已变化。')
            yield deepcopy(row)
            from .work_coordinator_handoff_runtime import reader
            current = self.s.teamwork.current(db, plan['item_id'])
            result = reader(self.s, db, current).read(db, current)
            self._recheck(db, {'frames': result['frames'], 'observation': obs, 'current_material_observation': result['material_observation']})
            if result['material_observation'] != baseline or time.time() >= obs['original_dispatch_before']:
                raise ValueError('Mixed保存末尾材料或最初截止已变化。')
        except BaseException:
            db.execute('ROLLBACK TO round_checkpoint_assets')
            db.execute('RELEASE round_checkpoint_assets')
            if not existed and asset.exists():
                # This exact path was checked before creation. Never enumerate
                # or delete other baseline assets, including pre-existing ones.
                if asset.resolve().parent != results or asset.is_symlink():
                    raise ValueError('失败回合材料目录发生变化，保留现场待核对。')
                shutil.rmtree(asset)
            raise
        else:
            db.execute('RELEASE round_checkpoint_assets')

    @contextmanager
    def preparing(self, db, plan, reason):
        """Prepare real candidate data; caller still owes an entire saved path."""
        with self.preserving(db, plan, reason) as checkpoint:
            cp = checkpoint['snapshot']; obs = cp['observation']
            answer = next(f for f in obs['history']['frontier'] if f['run_id'] == obs['source']['run_id'])['evidence']['answer']
            common = {k: deepcopy(cp[k]) for k in ('item_id', 'root', 'parent_plan', 'parent_path',
                'source', 'selected_unit', 'reason', 'operation', 'scope')}
            body = versioned({**common, 'protocol': CANDIDATE, 'checkpoint': self.data.ref(checkpoint),
                'child_seed': {'id': 'teamwork:' + uid(), 'created': time.time()},
                'carry_sources': obs['carry_sources'], 'historical_coordinators': obs['historical_coordinators'],
                'coordination_revision': {'id': 'coordination-revision:' + uid(), 'item_id': cp['item_id'],
                    'unit_key': obs['selected_unit']['definition']['key'], 'source': obs['source'], 'reason': reason,
                    'prior_check': obs['prior_check'], 'answer': answer if len(answer) <= 32000 else None,
                    'answer_chars': len(answer), 'answer_digest': digest(answer)},
                'transition': {'kind': OPERATION, 'source': obs['source'], 'reason': reason,
                    'usage_at_transition': obs['usage'],
                    'authorization_from': {k: obs['history']['authorization'][k] for k in ('id', 'revision')},
                    'command_id': self.s.command_identity, 'no_dispatch': True}, **PERMISSIONS})
            candidate = self.data.put(db, CANDIDATE, body)
            result = self.read_candidate(db, self.data.ref(candidate))
            yield deepcopy(result)
            if self.read_candidate(db, self.data.ref(candidate)) != result:
                raise ValueError('回合保存末尾候选完整历史或材料已变化。')

    @closed
    def read_candidate(self, db, reference):
        """Reconstruct actual immutable prepared records, without current proof."""
        with database_observation(db):
            before = db.total_changes
            source = self._candidate_once(db, reference); again = self._candidate_once(db, reference)
            self._recheck(db, again)
            if source != again or before != db.total_changes: raise ValueError('回合候选最终完整历史或材料重读已变化。')
            return deepcopy(again)

    def _candidate_once(self, db, reference):
        """One actual layer replay, used inside each complete outer traversal.

        This never caches a proof or bytes. Repeating the complete traversal at
        the public read boundary avoids multiplying two reads at every parent.
        """
        from .work_coordinator_handoff_mixed_contracts import prepared
        candidate = self.data.get(db, CANDIDATE, reference); body = candidate['snapshot']
        checkpoint = self.data.get(db, CHECKPOINT, body['checkpoint'])
        source = self._once(db, body['checkpoint'])
        return self._candidate_from_source(db, reference, source)

    def _candidate_from_source(self, db, reference, source):
        from .work_coordinator_handoff_mixed_contracts import prepared
        candidate = self.data.get(db, CANDIDATE, reference); body = candidate['snapshot']
        checkpoint = self.data.get(db, CHECKPOINT, body['checkpoint'])
        if source['checkpoint'] != checkpoint: raise ValueError('Mixed父重建不是准确实际候选检查点。')
        child = prepared(checkpoint, candidate)
        related = [r for r in self.s.collaboration.all(db, KIND_FOR[CANDIDATE]) if
                   r.get('snapshot', {}).get('source', {}).get('run_id') == body['source']['run_id']]
        event = self._event(db, EVENTS[CANDIDATE], candidate)
        if (related != [candidate] or event['seq'] <= source['checkpoint_event']['seq'] or
                candidate['created'] < checkpoint['created']):
            raise ValueError('回合候选须为该父源唯一准确实际登记准备。')
        return {**source, 'candidate': candidate, 'candidate_event': event,
                'child_plan': child, 'candidate_data_verified': True}

    def _event(self, db, kind, payload, cutoff=None, task=None, attempt=None):
        rows = [r for r in db.execute('SELECT * FROM events WHERE kind=? ORDER BY seq', (kind,))
                if json.loads(r['payload']) == payload and r['task_id'] == task and r['attempt_id'] == attempt]
        if len(rows) != 1 or cutoff is not None and rows[0]['seq'] > cutoff:
            raise ValueError('回合历史缺少截止前唯一准确实际事件：' + kind)
        return self.live._event(rows[0])


    def _guard(self, db, plan):
        seen = set(); current = plan
        while current.get('previous_plan_reference'):
            if current['id'] in seen: raise ValueError('Mixed方案历史形成循环。')
            seen.add(current['id'])
            ref = current['previous_plan_reference']
            current = self.s.collaboration.get(db, 'teamwork_plan', ref['id'])
            if current is None or plan_ref(current) != ref: raise ValueError('Mixed原父引用不符。')
        auths = [a for a in self.s.collaboration.all(db, 'work_unit_authorization_revision') if a.get('plan_id') == current['id']]
        if len(auths) != 1 or len(seen) > auths[0]['max_attempts_total']:
            raise ValueError('Mixed深度不能超过实际原授权次数。')
        return auths[0]['max_attempts_total']

    def observe(self, db, plan):
        self._guard(db, plan)
        from .work_coordinator_handoff_round_parent_checkpoint import WorkCoordinatorHandoffRoundParentCheckpoints
        if plan['coordinator_file_handoff_round_lineage']['protocol'] != LINEAGE:
            return WorkCoordinatorHandoffRoundParentCheckpoints(self.s).observe(db, plan)
        from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
        h = WorkCoordinatorHandoffMixedRuntime(self.s).read(db, plan)
        item = self.s.work_items.item(db, plan['item_id'])
        self.s.work_units.checked(db, {'id': item['id'], 'revision': item['revision'],
            'current_driver': self.s.work_drivers.reference(h['driver']), 'plan': plan_ref(plan)})
        if len(h['frames']) >= h['authorization']['max_attempts_total'] or time.time() >= h['authorization']['dispatch_before']:
            raise ValueError('Mixed父原授权无余量。')
        return self._assemble(db, plan, h, db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0])

    def _once(self, db, reference):
        self.old = WorkCoordinatorHandoffRoundCheckpoints(self.s)
        row = self.data.get(db, CHECKPOINT, reference); b = row['snapshot']; frozen = b['observation']; plan = b['plan']
        self._guard(db, plan)
        event = self._event(db, EVENTS[CHECKPOINT], row)
        if (event['seq'] != frozen['events']['cutoff'] + 1 or row['created'] >= frozen['original_dispatch_before'] or
                self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan):
            raise ValueError('Mixed检查点实际事件或完整父方案不符。')
        from .work_coordinator_handoff_mixed_replay import FirstRoundReplay
        from .work_coordinator_handoff_round_parent_checkpoint import WorkCoordinatorHandoffRoundParentCheckpoints
        from .work_coordinator_handoff_mixed_runtime import WorkCoordinatorHandoffMixedRuntime
        first = plan['coordinator_file_handoff_round_lineage']['protocol'] != LINEAGE
        history = FirstRoundReplay(self.s) if first else WorkCoordinatorHandoffMixedRuntime(self.s)
        h, current = history._walk(db, plan, event, frozen['history']['material_observation'])
        return self._from_history(db, reference, history, h, current)

    def _from_history(self, db, reference, history, h, current):
        from .work_coordinator_handoff_round_parent_checkpoint import WorkCoordinatorHandoffRoundParentCheckpoints
        row = self.data.get(db, CHECKPOINT, reference); b = row['snapshot']; frozen = b['observation']; plan = b['plan']
        event = self._event(db, EVENTS[CHECKPOINT], row)
        if (event['seq'] != frozen['events']['cutoff'] + 1 or row['created'] >= frozen['original_dispatch_before'] or
                self.s.collaboration.get(db, 'teamwork_plan', plan['id']) != plan or h['saved_path']['plan'] != plan_ref(plan)):
            raise ValueError('Mixed检查点实际事件、完整父方案或单层重建不符。')
        first = plan['coordinator_file_handoff_round_lineage']['protocol'] != LINEAGE
        assembler = WorkCoordinatorHandoffRoundParentCheckpoints(self.s) if first else self
        if assembler._assemble(db, plan, h, frozen['events']['cutoff']) != frozen:
            raise ValueError('Mixed父检查点不是实际事件截止重建的原历史。')
        self._parent_reader = history
        return {'checkpoint': row, 'checkpoint_event': event, 'observation': frozen,
            'frames': h['frames'], 'plans': history._parents + [plan],
            'current_material_observation': current, 'saved_edge_verified': False, 'public_apply': False, **PERMISSIONS}

    @closed
    def read(self, db, reference):
        with database_observation(db):
            before = db.total_changes
            a = self._once(db, reference); b = self._once(db, reference)
            self._recheck(db, b)
            if a != b or db.total_changes != before: raise ValueError('Mixed检查点完整重建或数据库变化。')
            return deepcopy(b)

    def _recheck(self, db, result):
        return WorkCoordinatorHandoffRoundCheckpoints(self.s)._recheck(db, result)

    def _assemble(self, db, plan, h, cutoff):
        candidates = [f for f in h['current_frames'] if f['run']['plan_id'] == plan['id'] and
            f['run']['generation'] == h['driver']['generation'] and f['run']['input']['unit']['role'] == 'coordinate']
        if not candidates: raise ValueError('ROUND父没有当前方案实际新协调回合。')
        frame = candidates[-1]; run = frame['run']; ev = frame['source_evidence']; unit = run['input']['unit']
        if (ev['state'] != 'COMPLETED' or not ev['exact_identity'] or not ev['result_available'] or
                not isinstance(ev['answer'], str) or not ev['answer'] or
                unit['member']['reference'] != plan['coordinator']['reference']):
            raise ValueError('ROUND父来源须为已实际退出的准确协调文字。')
        checks, rows = self.old._checks(db, plan, h, cutoff)
        prior = prior_event = None
        if checks:
            b = rows[checks[-1]['reference']['id']]['snapshot']
            if b['source']['run_id'] == run['id']:
                if b['source']['version'] != ev['version'] or b['plan'] != plan_ref(plan): raise ValueError('ROUND父同版本检查来源不符。')
                prior = {'reference': checks[-1]['reference'], 'state': b['state'], 'problems': b['problems']}
                prior_event = checks[-1]['event']
        carry_sources = {}; retired = plan['file_handoffs'][0]['source_unit'] if h['phase'] == 'B_COMPLETED' else None
        from .work_coordinator_handoff_adjustments import _require_result
        current = {f['run']['unit_key']: f for f in h['current_frames']}
        for u in plan['units']:
            if u['role'] == 'coordinate' or u['key'] == retired or u['key'] not in current: continue
            f = current[u['key']]; e = f['source_evidence']
            if e['state'] != 'COMPLETED' or not e['exact_identity'] or f['run']['input']['unit'] != u:
                raise ValueError('ROUND父普通成果须完整原定义准确完成。')
            _require_result(e, u); carry_sources[u['key']] = source_ref(f['run'], e)
        events = []
        for f in h['frames']:
            r = f['run']; attempt = self.s.store.attempt(r['attempt_id'], db)
            events.append({'run_id': r['id'],
                'reservation': self.old._event(db, 'cooperative_unit_reserved', r, cutoff, r['task_id'], r['attempt_id']),
                'result': self.old._event(db, 'attempt_result_saved', attempt['result'], cutoff, r['task_id'], r['attempt_id']),
                'captures': [self.sources._event(e) for e in db.execute(
                    "SELECT * FROM events WHERE task_id=? AND attempt_id=? AND kind='cooperative_files_checked' ORDER BY seq",
                    (r['task_id'], r['attempt_id']))]})
        historical = set(plan['historical_coordinators']) | {f['run']['unit_key'] for f in h['frames']
            if f['run']['input']['unit']['role'] == 'coordinate'}
        historical.discard(unit['key'])
        return validate_parent(versioned({'protocol': PARENT, 'item_id': plan['item_id'], 'root': h['root'],
            'plan': plan_ref(plan), 'parent_kind': 'ROUND', 'saved_path': h['saved_path'],
            'history': {k: deepcopy(h[k]) for k in HISTORY_FIELDS}, 'source': source_ref(run, ev),
            'selected_unit': {'definition': unit, 'revision': digest(unit)}, 'prior_check': prior,
            'carry_sources': carry_sources, 'historical_coordinators': sorted(historical),
            'usage': h['usage'], 'original_dispatch_before': h['authorization']['dispatch_before'],
            'events': {'cutoff': cutoff, 'runs': events, 'checks': checks, 'prior_check': prior_event},
            'scope': PARENT_SCOPE, **PERMISSIONS}))
